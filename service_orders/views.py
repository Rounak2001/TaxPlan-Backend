import razorpay
import json
from decimal import Decimal, InvalidOperation
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import ServiceOrder, OrderItem, Coupon
from consultations.models import ConsultationBooking
from consultants.models import (
    Service,
    ClientServiceRequest,
    ConsultantServiceProfile,
    ConsultantServiceExpertise,
    ServiceCategory,
)
from consultant_onboarding.category_access import (
    ASSESSMENT_CATEGORY_ORDER,
    get_unlock_category_slugs_for_service,
    is_service_unlocked,
)
from consultant_onboarding.assessment_outcome import get_application_assessment_outcome
from consultant_onboarding.expertise_sync import sync_passed_sessions_to_consultant
from activity_timeline.models import Activity
from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from .utils import create_service_requests_from_order
from core_auth.utils import get_active_profile, resolve_authenticated_user
from .pricing import get_verified_price

import logging

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

logger = logging.getLogger('service_orders')
REGISTRATIONS_SLUG = 'registrations'
MAX_ADDITIONAL_SERVICE_AMOUNT = Decimal('1000000.00')


# --- Helper functions for Additional Services ---

def _require_consultant(request):
    """Utility to ensure user is a consultant."""
    if request.user.role != 'CONSULTANT':
        return Response({'error': 'Only consultants can perform this action.'}, status=status.HTTP_403_FORBIDDEN)
    return None

def _get_consultant_profile_or_404(user):
    """Utility to get consultant profile."""
    from core_auth.models import ConsultantProfile
    try:
        return ConsultantProfile.objects.get(user=user)
    except ConsultantProfile.DoesNotExist:
        return None

def _is_registrations_service(service_obj):
    """Check if service belongs to registrations category."""
    if not service_obj or not service_obj.category:
        return False
    return service_obj.category.slug == REGISTRATIONS_SLUG

def _parse_positive_decimal(value, field_name):
    """Helper to parse decimal values safely."""
    try:
        decimal_val = Decimal(str(value))
        if decimal_val <= 0:
            return None, Response({'error': f'{field_name} must be greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)
        return decimal_val, None
    except (InvalidOperation, ValueError, TypeError):
        return None, Response({'error': f'Invalid {field_name}.'}, status=status.HTTP_400_BAD_REQUEST)

def _get_unlocked_category_slugs_for_consultant(consultant_profile):
    """Get list of category slugs consultant has passed exams for."""
    return ConsultantServiceExpertise.objects.filter(
        consultant=consultant_profile
    ).values_list('service__category__slug', flat=True).distinct()

def _is_itr_returns_item(service_obj, item_title='', item_category=''):
    title = (service_obj.title if service_obj else item_title or '').strip().lower()
    category = ((service_obj.category.name if service_obj and service_obj.category else item_category or '').strip().lower())
    return category == 'returns' and 'itr' in title


ASSESSMENT_CATEGORY_LABELS = {
    'itr': 'Income Tax',
    'gstr': 'GST',
    'scrutiny': 'Notices & Scrutiny',
    'registrations': 'Registrations',
}

ADDITIONAL_PAYMENT_CATALOG_TITLES = (
    'ITR Salary Filing',
    'ITR Individual Business Filing',
    'ITR LLP Filing',
    'ITR NRI Filing',
    'ITR Partnership Filing',
    'ITR Company Filing',
    'ITR Trust Filing',
    'GSTR-1 & GSTR-3B (Monthly)',
    'GSTR-1 & GSTR-3B (Quarterly)',
    'GSTR CMP-08',
    'GSTR-9',
    'GSTR-9C',
    'GSTR-4 (Annual Return)',
    'GSTR-10 (Final Return)',
    'TDS Monthly Payment',
    'TDS Quarterly Filing',
    'TDS Revised Quarterly Filing',
    'Sale of Property (26QB)',
    'PAN Application',
    'TAN Registration',
    'Aadhaar Validation',
    'MSME Registration',
    'Import Export Code (IEC)',
    'Partnership Firm Registration',
    'LLP Registration',
    'Private Limited Company Registration',
    'Startup India Registration',
    'Trust Formation',
    '12A Registration',
    '80G Registration',
    'DSC (Digital Signature Certificate)',
    'HUF PAN',
    'NRI PAN',
    'Foreign Entity Registration',
    'ITR Appeal',
    'ITR Regular Assessment',
    'ITR Tribunal',
    'GST Appeal',
    'GST Regular Assessment',
    'GST Tribunal',
    'TDS Appeal',
    'TDS Regular Assessment',
    'TDS Tribunal',
)


def _get_consultant_unlock_state(user):
    from consultant_onboarding.models import ConsultantApplication

    application = ConsultantApplication.objects.filter(email=getattr(user, 'email', None)).first()
    if not application:
        return {
            'application': None,
            'unlocked_categories': list(ASSESSMENT_CATEGORY_ORDER),
            'available_assessment_categories': [],
        }

    assessment = get_application_assessment_outcome(application)
    return {
        'application': application,
        'unlocked_categories': assessment.get('unlocked_categories', []),
        'available_assessment_categories': assessment.get('available_assessment_categories', []),
    }


def _serialize_additional_service_order(order):
    items_payload = []
    total_amount = Decimal('0.00')
    first_title = 'Additional Service'
    first_description = ''

    items = list(order.items.select_related('service').all())
    for index, item in enumerate(items):
        line_amount = Decimal(item.price or 0)
        total_amount += line_amount
        title = item.service.title if item.service else (item.service_title or 'Additional Service')
        description = (item.variant_name or '').strip()

        if index == 0:
            first_title = title
            first_description = description

        items_payload.append(
            {
                'id': item.id,
                'service_id': item.service_id,
                'service_title': title,
                'category': item.category or '',
                'amount': float(line_amount),
                'base_amount': float(item.base_price if item.base_price is not None else line_amount),
                'is_price_edited': (
                    item.base_price is not None and Decimal(item.base_price) != line_amount
                ),
                'price_update_reason': (item.price_update_reason or '').strip(),
                'description': description,
                'is_custom': item.service_id is None,
            }
        )

    amount = total_amount or Decimal(order.total_amount or 0)
    service_title = first_title
    description = first_description
    if len(items_payload) > 1:
        service_title = f'{first_title} + {len(items_payload) - 1} more'

    consultant_name = ''
    consultant = getattr(order, 'initiated_by', None)
    if consultant:
        consultant_name = consultant.get_full_name() or consultant.username

    return {
        'order_id': order.id,
        'service_title': service_title,
        'description': description,
        'amount': float(amount or 0),
        'item_count': len(items_payload),
        'items': items_payload,
        'consultant_name': consultant_name,
        'razorpay_order_id': order.razorpay_order_id,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'created_at': order.created_at.isoformat() if order.created_at else None,
        'booking_id': order.from_booking_id,
    }


def _push_additional_payment_notification(order):
    payload = _serialize_additional_service_order(order)
    item_count = payload.get('item_count') or 0
    plural = 'service' if item_count == 1 else 'services'
    title = f'Additional payment request ({item_count} {plural})'
    message = (
        f"{payload.get('consultant_name') or 'Your consultant'} requested "
        f"Rs {float(payload.get('amount') or 0):,.2f} for {payload.get('service_title') or 'additional services'}."
    )
    return _create_and_push_notification(
        recipient=order.user,
        category='payment',
        title=title,
        message=message,
        link='/client',
        extra_payload={
            'type': 'PAYMENT_REQUEST',
            **payload,
        },
    )


def _create_and_push_notification(
    *,
    recipient,
    category,
    title,
    message,
    link='',
    extra_payload=None,
):
    notification = Notification.objects.create(
        recipient=recipient,
        category=category,
        title=title,
        message=message,
        link=link or '',
    )

    channel_layer = get_channel_layer()
    if channel_layer:
        payload = NotificationSerializer(notification).data
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)
        async_to_sync(channel_layer.group_send)(
            f'user_{recipient.id}',
            {
                'type': 'notification_message',
                'data': payload,
            },
        )

    return notification


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def validate_coupon(request):
    """
    Validate a coupon code against a given cart total.
    Returns discount_amount and new_total if valid.
    """
    code = str(request.data.get('code') or '').strip().upper()
    raw_total = request.data.get('cart_total', 0)

    if not code:
        return Response({'valid': False, 'error': 'Coupon code is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        cart_total = Decimal(str(raw_total))
    except (InvalidOperation, TypeError, ValueError):
        return Response({'valid': False, 'error': 'Invalid cart total.'}, status=status.HTTP_400_BAD_REQUEST)

    coupon = Coupon.objects.filter(code=code).first()
    if coupon is None:
        return Response({'valid': False, 'error': 'Invalid coupon code.'}, status=status.HTTP_200_OK)

    is_valid, error_msg = coupon.is_valid(cart_total)
    if not is_valid:
        return Response({'valid': False, 'error': error_msg}, status=status.HTTP_200_OK)

    discount_amount = coupon.calculate_discount(cart_total)
    new_total = cart_total - discount_amount

    return Response({
        'valid': True,
        'code': coupon.code,
        'discount_amount': float(discount_amount),
        'new_total': float(new_total),
        'description': coupon.description,
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def create_order(request):
    """
    1. Receive cart items.
    2. Fetch REAL prices from Service model (Security Fix).
    3. Apply coupon discount (if provided).
    4. Create a pending ServiceOrder.
    5. Create Razorpay Order.
    """
    user = get_active_profile(request)
    items_data = request.data.get('items', [])
    coupon_code_input = str(request.data.get('coupon_code') or '').strip().upper()

    logger.debug(f"create_order called by {user.email}")
    logger.debug(f"Razorpay Key ID: {settings.RAZORPAY_KEY_ID}")

    if not items_data:
        logger.warning("No items in cart")
        return Response({'error': 'No items in cart'}, status=status.HTTP_400_BAD_REQUEST)

    total_amount = Decimal("0.00")
    valid_items = []

    try:
        # 1. Validate items and calculate total from DB
        for item in items_data:
            service_id = item.get('service_id')
            qty = max(1, int(item.get('quantity', 1)))
            
            service = None
            if service_id:
                try:
                    service = Service.objects.get(id=service_id)
                except Service.DoesNotExist:
                    pass
            
            # Fallback for old frontend logic (if passing title but no ID) - Not recommended but safe if we query DB
            if not service and item.get('title'):
                service = Service.objects.filter(title=item.get('title')).first()

            # Security: Use DB price if service exists, otherwise trust frontend (for custom landing page bundles)
            # We use get_verified_price which double-checks the DB and calculates add-on prices
            item_price = get_verified_price(service, item)
            
            if item_price <= 0 and not service:
                # Custom bundles must have a valid price from frontend if not in DB
                try:
                    frontend_price = Decimal(str(item.get('price', 0)))
                except (InvalidOperation, TypeError, ValueError):
                    frontend_price = Decimal("0.00")
                if frontend_price <= 0:
                    return Response(
                        {'error': f"Invalid price for custom item: {item.get('title', 'Unknown')}"}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
                item_price = frontend_price
            elif item_price <= 0 and service:
                 return Response(
                    {'error': f"Service {service.title} has no price configured"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            item_total = item_price * qty
            total_amount += item_total
            
            # Extract consultant selection data
            consultant_id = item.get('consultant_id')
            selection_mode = item.get('selection_mode', 'auto')
            selected_consultant = None
            
            if consultant_id and selection_mode == 'manual':
                try:
                    selected_consultant = ConsultantServiceProfile.objects.get(id=consultant_id)
                except ConsultantServiceProfile.DoesNotExist:
                    pass  # Fall back to auto-assignment
            
            valid_items.append({
                'service': service, # May be None for custom bundles
                'quantity': qty,
                    'price': item_price,
                'category': item.get('category') or (service.category.name if service and service.category else 'General'),
                'title': service.title if service else item.get('title', 'Custom Service'),
                'variant': item.get('variantName', ''),
                'selected_consultant': selected_consultant,
                'selection_mode': selection_mode,
            })

        if total_amount <= 0:
            return Response({'error': 'Invalid total amount'}, status=status.HTTP_400_BAD_REQUEST)

        # ── Coupon validation ─────────────────────────────────────────────────
        applied_coupon = None
        discount_amount = Decimal('0.00')
        original_amount = total_amount
        final_amount = total_amount

        if coupon_code_input:
            coupon_obj = Coupon.objects.filter(code=coupon_code_input).first()
            if coupon_obj is None:
                return Response({'error': 'Invalid coupon code.'}, status=status.HTTP_400_BAD_REQUEST)
            is_valid, error_msg = coupon_obj.is_valid(total_amount)
            if not is_valid:
                return Response({'error': error_msg}, status=status.HTTP_400_BAD_REQUEST)
            discount_amount = coupon_obj.calculate_discount(total_amount)
            final_amount = total_amount - discount_amount
            if final_amount <= 0:
                return Response({'error': 'Discount makes total zero — please remove coupon or add more items'}, status=status.HTTP_400_BAD_REQUEST)
            applied_coupon = coupon_obj

        with transaction.atomic():
            # 2. Create Order
            order = ServiceOrder.objects.create(
                user=user,
                total_amount=final_amount,
                original_amount=original_amount if applied_coupon else None,
                discount_amount=discount_amount,
                coupon=applied_coupon,
                status='pending'
            )

            # 3. Create Items
            for valid_item in valid_items:
                OrderItem.objects.create(
                    order=order,
                    service=valid_item['service'],
                    category=valid_item['category'],
                    service_title=valid_item['title'],
                    variant_name=valid_item['variant'],
                    price=valid_item['price'],
                    quantity=valid_item['quantity'],
                    selected_consultant=valid_item['selected_consultant'],
                    selection_mode=valid_item['selection_mode'],
                )

            # 4. Razorpay Order (use discounted amount)
            razorpay_order = razorpay_client.order.create({
                "amount": int((final_amount * Decimal("100")).quantize(Decimal("1"))),  # paise
                "currency": "INR",
                "receipt": f"receipt_order_{order.id}",
                "payment_capture": 1
            })

            order.razorpay_order_id = razorpay_order['id']
            order.save(update_fields=['razorpay_order_id'])

            # 5. Increment coupon usage count
            if applied_coupon:
                Coupon.objects.filter(pk=applied_coupon.pk).update(
                    used_count=applied_coupon.used_count + 1
                )

            return Response({
                'order_id': order.id,
                'razorpay_order_id': razorpay_order['id'],
                'amount': float(final_amount),
                'original_amount': float(original_amount),
                'discount_amount': float(discount_amount),
                'amount_paise': razorpay_order['amount'],
                'key_id': settings.RAZORPAY_KEY_ID
            })

    except Exception as e:
        print(f"Order creation error: {str(e)}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _require_consultant(request):
    user = resolve_authenticated_user(request)
    if not user or getattr(user, 'role', None) != 'CONSULTANT':
        return Response(
            {'error': 'Only consultants can request additional payments.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    request._resolved_consultant_user = user
    return None


def _get_consultant_profile_or_404(user):
    return ConsultantServiceProfile.objects.filter(user=user).first()


def _get_unlocked_category_slugs_for_consultant(consultant_profile):
    expertise = (
        ConsultantServiceExpertise.objects.filter(consultant=consultant_profile)
        .select_related('service', 'service__category')
    )
    unlocked = set()
    for row in expertise:
        unlocked.update(get_unlock_category_slugs_for_service(row.service))
    if consultant_profile and consultant_profile.user.is_onboarded:
        unlocked.add(REGISTRATIONS_SLUG)
    return [slug for slug in ASSESSMENT_CATEGORY_ORDER if slug in unlocked]


def _is_registrations_service(service):
    return REGISTRATIONS_SLUG in get_unlock_category_slugs_for_service(service)


def _parse_positive_decimal(value, field_name):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None, Response(
            {'error': f'{field_name} must be a valid positive number.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed <= 0:
        return None, Response(
            {'error': f'{field_name} must be greater than 0.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return parsed, None


def _match_category_for_slug(category_slug):
    all_categories = ServiceCategory.objects.filter(is_active=True)

    # Prefer a category that already has a service mapped to the unlock slug.
    mapped_service = None
    for service in Service.objects.filter(is_active=True).select_related('category'):
        if category_slug in get_unlock_category_slugs_for_service(service):
            mapped_service = service
            break
    if mapped_service:
        return mapped_service.category

    fallback_by_slug = {
        'itr': 'returns',
        'gstr': 'returns',
        'scrutiny': 'notices',
        'registrations': 'registrations',
    }
    fallback_name = fallback_by_slug.get(category_slug)
    if fallback_name:
        category = all_categories.filter(name__iexact=fallback_name).first()
        if category:
            return category

    return all_categories.first()


def _get_or_create_custom_service(custom_title, custom_price, category_slug):
    category = _match_category_for_slug(category_slug)
    if category is None:
        return None

    existing = Service.objects.filter(category=category, title=custom_title).first()
    if existing:
        return existing

    return Service.objects.create(
        category=category,
        title=custom_title,
        price=custom_price,
        tat='As discussed',
        documents_required='To be shared during consultation.',
        is_active=True,
    )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def additional_service_options(request):
    consultant_error = _require_consultant(request)
    if consultant_error:
        return consultant_error

    consultant_profile = _get_consultant_profile_or_404(request.user)
    if consultant_profile is None:
        return Response(
            {'error': 'Consultant profile not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    unlocked_service_ids = set(
        ConsultantServiceExpertise.objects.filter(consultant=consultant_profile).values_list(
            'service_id', flat=True
        )
    )
    unlocked_category_slugs = _get_unlocked_category_slugs_for_consultant(consultant_profile)

    services = Service.objects.filter(is_active=True).select_related('category').order_by(
        'category__name',
        'title',
    )
    unlocked_services = []
    locked_services = []
    for service in services:
        is_registration_service = _is_registrations_service(service)
        is_locked = service.id not in unlocked_service_ids and not (
            is_registration_service and request.user.is_onboarded
        )
        row = {
            'id': service.id,
            'title': service.title,
            'price': str(service.price) if service.price is not None else '0',
            'category': service.category.name if service.category else '',
            'category_slugs': get_unlock_category_slugs_for_service(service),
            'is_locked': is_locked,
            'lock_message': (
                'Give the required test to unlock this service.'
                if is_locked
                else ''
            ),
        }
        if row['is_locked']:
            locked_services.append(row)
        else:
            unlocked_services.append(row)

    categories = []
    for slug in ASSESSMENT_CATEGORY_ORDER:
        categories.append(
            {
                'slug': slug,
                'label': slug.upper() if slug != 'registrations' else 'Registrations',
                'is_locked': slug not in unlocked_category_slugs,
                'lock_message': (
                    'Give the required test to unlock this category.'
                    if slug not in unlocked_category_slugs
                    else ''
                ),
            }
        )

    return Response(
        {
            'unlocked_services': unlocked_services,
            'locked_services': locked_services,
            'categories': categories,
            'unlocked_category_slugs': unlocked_category_slugs,
            'can_offer_custom_services': bool(unlocked_category_slugs),
        }
    )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def request_additional_service(request):
    consultant_error = _require_consultant(request)
    if consultant_error:
        return consultant_error
    consultant_user = getattr(request, '_resolved_consultant_user', None) or resolve_authenticated_user(request)
    if consultant_user is None:
        return Response({'error': 'Authenticated consultant not found.'}, status=status.HTTP_401_UNAUTHORIZED)

    booking_id = request.data.get('booking_id')
    service_id = request.data.get('service_id')
    custom_title = str(request.data.get('custom_title') or '').strip()
    custom_price_raw = request.data.get('custom_price')
    category_slug = str(request.data.get('category_slug') or '').strip().lower()
    additional_description = str(request.data.get('description') or '').strip()
    if len(additional_description) > 255:
        additional_description = additional_description[:255]

    if not booking_id:
        return Response({'error': 'booking_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    booking = ConsultationBooking.objects.filter(id=booking_id).select_related('client', 'consultant').first()
    if booking is None:
        return Response({'error': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)
    if booking.consultant_id != consultant_user.id:
        return Response(
            {'error': 'You are not authorized to request payment for this booking.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    consultant_profile = _get_consultant_profile_or_404(consultant_user)
    if consultant_profile is None:
        return Response({'error': 'Consultant profile not found.'}, status=status.HTTP_404_NOT_FOUND)

    item_service = None
    item_title = ''
    item_category = ''
    item_price = Decimal('0.00')

    if service_id:
        item_service = Service.objects.filter(id=service_id, is_active=True).select_related('category').first()
        if item_service is None:
            return Response({'error': 'Selected service was not found.'}, status=status.HTTP_404_NOT_FOUND)

        expertise_exists = ConsultantServiceExpertise.objects.filter(
            consultant=consultant_profile,
            service=item_service,
        ).exists()
        can_offer_registration = _is_registrations_service(item_service) and consultant_user.is_onboarded
        if not expertise_exists and not can_offer_registration:
            return Response(
                {
                    'error': (
                        'You are not authorized to offer this service. '
                        'Give the required test to unlock it.'
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        item_price = get_verified_price(item_service, {'service_id': item_service.id, 'quantity': 1})
        if item_price <= 0:
            return Response(
                {'error': 'Selected service has no valid configured price.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item_title = item_service.title
        item_category = item_service.category.name if item_service.category else 'General'
    else:
        if not custom_title:
            return Response(
                {'error': 'custom_title is required when service_id is not provided.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not category_slug:
            return Response(
                {'error': 'category_slug is required for custom services.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if category_slug not in ASSESSMENT_CATEGORY_ORDER:
            return Response(
                {'error': f'category_slug must be one of: {", ".join(ASSESSMENT_CATEGORY_ORDER)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item_price, price_error = _parse_positive_decimal(custom_price_raw, 'custom_price')
        if price_error:
            return price_error

        unlocked_category_slugs = _get_unlocked_category_slugs_for_consultant(consultant_profile)
        if not unlocked_category_slugs:
            return Response(
                {
                    'error': (
                        'You must pass at least one assessment to offer custom services.'
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if category_slug not in unlocked_category_slugs:
            return Response(
                {
                    'error': (
                        'You have not passed the exam for this category. '
                        'Give the required test to unlock it.'
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        item_service = _get_or_create_custom_service(custom_title, item_price, category_slug)
        if item_service is None:
            return Response(
                {'error': 'Could not resolve a valid service category for this custom service.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item_title = custom_title
        item_category = item_service.category.name if item_service.category else category_slug.upper()

    consultant_name = consultant_user.get_full_name() or consultant_user.username
    total_amount = item_price

    try:
        with transaction.atomic():
            order = ServiceOrder.objects.create(
                user=booking.client,
                total_amount=total_amount,
                discount_amount=Decimal('0.00'),
                status='pending',
                is_additional=True,
                from_booking=booking,
                initiated_by=consultant_user,
            )

            OrderItem.objects.create(
                order=order,
                service=item_service,
                category=item_category,
                service_title=item_title,
                variant_name=additional_description,
                price=total_amount,
                quantity=1,
                selected_consultant=consultant_profile,
                selection_mode='manual',
            )

            razorpay_order = razorpay_client.order.create(
                {
                    'amount': int((total_amount * Decimal('100')).quantize(Decimal('1'))),
                    'currency': 'INR',
                    'receipt': f'receipt_additional_{order.id}',
                    'payment_capture': 1,
                }
            )

            order.razorpay_order_id = razorpay_order['id']
            order.save(update_fields=['razorpay_order_id'])

            Activity.objects.create(
                actor=consultant_user,
                target_user=booking.client,
                activity_type='additional_payment_requested',
                title=f'{consultant_name} requested payment for {item_title}',
                content_object=order,
                metadata={
                    'booking_id': booking.id,
                    'service_title': item_title,
                    'amount': float(total_amount),
                    'initiated_by_id': consultant_user.id,
                    'initiated_by_name': consultant_name,
                    'description': additional_description,
                },
            )

            notification = Notification.objects.create(
                recipient=booking.client,
                category='payment',
                title='Additional Service Payment Request',
                message=f'{consultant_name} requested payment for {item_title}.',
                link='/client',
            )

            ws_payload = NotificationSerializer(notification).data
            ws_payload.update(
                {
                    'type': 'PAYMENT_REQUEST',
                    'order_id': order.id,
                    'razorpay_order_id': razorpay_order['id'],
                    'razorpay_key_id': settings.RAZORPAY_KEY_ID,
                    'amount': float(total_amount),
                    'service_title': item_title,
                    'consultant_name': consultant_name,
                    'booking_id': booking.id,
                    'description': additional_description,
                    'message': (
                        'Your consultant has requested a payment for an additional service.'
                    ),
                }
            )

            def _push_payment_request():
                try:
                    channel_layer = get_channel_layer()
                    if channel_layer is None:
                        logger.warning(
                            'Channel layer unavailable; skipping realtime PAYMENT_REQUEST push for order %s',
                            order.id,
                        )
                        return
                    async_to_sync(channel_layer.group_send)(
                        f'user_{booking.client_id}',
                        {
                            'type': 'notification_message',
                            'data': ws_payload,
                        },
                    )
                except Exception as ws_exc:
                    # Do not fail the request if Redis/WebSocket infra is down.
                    # Notification row is already persisted and will be visible via polling.
                    logger.warning(
                        'Realtime PAYMENT_REQUEST push failed for order %s: %s',
                        order.id,
                        ws_exc,
                    )

            transaction.on_commit(_push_payment_request)

        return Response(
            {
                'message': 'Payment request sent to client',
                'order_id': order.id,
                'razorpay_order_id': razorpay_order['id'],
                'key_id': settings.RAZORPAY_KEY_ID,
            },
            status=status.HTTP_201_CREATED,
        )
    except Exception as exc:
        logger.exception('Failed to request additional payment: %s', exc)
        return Response(
            {'error': 'Failed to create additional payment request.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def pending_additional_requests(request):
    user = get_active_profile(request)
    if user.role != 'CLIENT':
        return Response({'results': []})

    pending_orders = (
        ServiceOrder.objects.filter(
            user=user,
            is_additional=True,
            status='pending',
            razorpay_order_id__isnull=False,
        )
        .select_related('initiated_by', 'from_booking')
        .prefetch_related('items')
        .order_by('-created_at')
    )

    results = []
    for order in pending_orders:
        item = order.items.first()
        if item is None:
            continue
        consultant_name = (
            order.initiated_by.get_full_name() or order.initiated_by.username
            if order.initiated_by
            else 'Your Consultant'
        )
        results.append(
            {
                'type': 'PAYMENT_REQUEST',
                'order_id': order.id,
                'razorpay_order_id': order.razorpay_order_id,
                'razorpay_key_id': settings.RAZORPAY_KEY_ID,
                'amount': float(order.total_amount),
                'service_title': item.service_title or (item.service.title if item.service else 'Additional Service'),
                'consultant_name': consultant_name,
                'booking_id': order.from_booking_id,
                'description': item.variant_name or '',
                'message': 'Your consultant has requested a payment for an additional service.',
            }
        )

    return Response({'results': results})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def decline_additional_request(request):
    user = get_active_profile(request)
    if user.role != 'CLIENT':
        return Response(
            {'error': 'Only clients can decline additional payment requests.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    order_id = request.data.get('order_id')
    if not order_id:
        return Response({'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    order = ServiceOrder.objects.filter(
        id=order_id,
        user=user,
        is_additional=True,
        status='pending',
    ).first()
    if order is None:
        return Response({'error': 'Pending additional request not found.'}, status=status.HTTP_404_NOT_FOUND)

    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])

    consultant_name = (
        order.initiated_by.get_full_name() or order.initiated_by.username
        if order.initiated_by
        else 'Consultant'
    )
    Activity.objects.create(
        actor=user,
        target_user=user,
        activity_type='additional_payment_requested',
        title=f'Additional payment request from {consultant_name} was declined',
        content_object=order,
        metadata={
            'booking_id': order.from_booking_id,
            'order_id': order.id,
            'declined': True,
        },
    )

    return Response({'message': 'Additional payment request declined.'})

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def verify_payment(request):
    """
    Verify payment signature and fulfill order atomically.
    Safety Net: If processing fails after valid signature, status is saved as 'processing_error'.
    """
    razorpay_order_id = request.data.get('razorpay_order_id')
    razorpay_payment_id = request.data.get('razorpay_payment_id')
    razorpay_signature = request.data.get('razorpay_signature')

    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }
    
    logger.debug(f"verify_payment called for Order ID: {razorpay_order_id}")
    logger.debug(f"Signature: {razorpay_signature}")

    # If we are here, PAYMENT DATA is received. 
    # Use atomic transaction to prevent race conditions with Webhook
    try:
        with transaction.atomic():
            try:
                # Lock the order row (no select_related on nullable FKs — PostgreSQL forbids FOR UPDATE on outer joins)
                order = ServiceOrder.objects.select_for_update().get(razorpay_order_id=razorpay_order_id)
            except ServiceOrder.DoesNotExist:
                 return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)
            
            # 1. Check if Webhook already processed this
            if order.status == 'paid':
                 return Response({
                     'status': 'Payment verified successfully (via webhook)', 
                     'order_id': order.id,
                     'already_paid': True
                 })

            # 2. Verify signature
            try:
                razorpay_client.utility.verify_payment_signature(params_dict)
            except Exception as sig_err:
                logger.error(f"Signature verification failed: {str(sig_err)}")
                return Response({'error': 'Payment verification failed'}, status=status.HTTP_400_BAD_REQUEST)

            # 3. Update status (Only if signature is valid and not already paid)
            order.status = 'paid'
            order.razorpay_payment_id = razorpay_payment_id
            order.razorpay_signature = razorpay_signature
            order.save()
            
            # 4. Create service requests (Fulfillment)
            service_requests = create_service_requests_from_order(order)
            
        # Success Response
        return Response({
            'status': 'Payment verified successfully',
            'order_id': order.id,
            'service_requests': service_requests
        })

    except Exception as e:
        # 3. SAFETY NET: The transaction rolled back, so DB is clean.
        # But we MUST record that payment happened.
        logger.critical(f"CRITICAL: Payment verified but processing failed: {str(e)}", exc_info=True)
        
        try:
            # Re-fetch the order (Transaction is gone, so this is fresh state)
            order = ServiceOrder.objects.get(razorpay_order_id=razorpay_order_id)
            
            # Save the 'Evidence of Payment'
            order.status = 'failed' # Or 'processing_error' if you add that choice
            order.razorpay_payment_id = razorpay_payment_id
            order.razorpay_signature = razorpay_signature
            # We assume 'failed' status implies manual intervention needed for paid orders
            order.save()
        except Exception as save_err:
            logger.critical(f"Double Fault: Could not save error state: {str(save_err)}", exc_info=True)

        # Return a specific error so frontend can show "Contact Support"
        return Response({
            'error': 'Payment received but order processing failed. Please contact support.',
            'order_id': razorpay_order_id,
            'payment_id': razorpay_payment_id
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def razorpay_webhook(request):
    """
    Handle Razorpay Webhooks for Service Orders (payment.captured).
    This ensures orders are fulfilled even if the frontend fails to call verify_payment.
    """
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not webhook_secret:
        return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    payload = request.body.decode('utf-8')
    # Try both header access methods - Nginx sometimes passes it differently
    signature = (
        request.headers.get('X-Razorpay-Signature')
        or request.META.get('HTTP_X_RAZORPAY_SIGNATURE')
    )

    if not signature:
        logger.warning("Razorpay webhook received without X-Razorpay-Signature header - rejecting")
        return Response({'error': 'Missing signature'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Verify webhook signature
        razorpay_client.utility.verify_webhook_signature(payload, signature, webhook_secret)
        
        event_data = request.data
        event = event_data.get('event')

        if event == 'payment.captured':
            payment_entity = event_data['payload']['payment']['entity']
            razorpay_order_id = payment_entity.get('order_id')
            razorpay_payment_id = payment_entity.get('id')
            
            try:
                # Use select_for_update to lock the row during processing
                with transaction.atomic():
                    order = ServiceOrder.objects.select_for_update().get(razorpay_order_id=razorpay_order_id)
                    
                    # Idempotency check: If already paid, ignore
                    if order.status == 'paid':
                        return Response({'status': 'Order already processed'}, status=status.HTTP_200_OK)

                    # Mark as paid
                    order.status = 'paid'
                    order.razorpay_payment_id = razorpay_payment_id
                    order.save()
                    
                    # Trigger Fulfillment
                    create_service_requests_from_order(order)
                    logger.info(f"Webhook: Order {order.id} fulfilled successfully.")


            except ServiceOrder.DoesNotExist:
                # This might happen if the webhook belongs to 'consultations' app
                # We return 200 so Razorpay doesn't keep retrying
                return Response({'status': 'Order not found in service_orders'}, status=status.HTTP_200_OK)
                
        return Response({'status': 'Webhook processed'}, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def additional_service_options(request):
    """
    Return consultant-side catalog/category options for in-call additional payments.
    """
    user = resolve_authenticated_user(request)
    if not user or getattr(user, 'role', None) != 'CONSULTANT':
        return Response({'error': 'Only consultants can view additional service options.'}, status=status.HTTP_403_FORBIDDEN)

    unlock_state = _get_consultant_unlock_state(user)
    application = unlock_state['application']
    if application is not None:
        try:
            sync_passed_sessions_to_consultant(application)
        except Exception:
            logger.exception("Failed to sync consultant expertise before loading additional service options")

    unlocked_categories = set(unlock_state['unlocked_categories'])
    services = (
        Service.objects.filter(is_active=True, title__in=ADDITIONAL_PAYMENT_CATALOG_TITLES)
        .select_related('category')
        .order_by('title')
    )

    unlocked_services = []
    locked_services = []
    for service in services:
        service_payload = {
            'id': service.id,
            'title': service.title,
            'category_name': getattr(service.category, 'name', ''),
            'price': float(service.price or 0),
            'unlock_category_slugs': get_unlock_category_slugs_for_service(service),
            'is_locked': not is_service_unlocked(service, unlocked_categories),
        }
        if service_payload['is_locked']:
            locked_services.append(service_payload)
        else:
            unlocked_services.append(service_payload)

    categories = [
        {
            'slug': slug,
            'label': ASSESSMENT_CATEGORY_LABELS.get(slug, slug.replace('_', ' ').title()),
            'is_locked': slug not in unlocked_categories,
        }
        for slug in ASSESSMENT_CATEGORY_ORDER
    ]

    return Response({
        'unlocked_services': unlocked_services,
        'locked_services': locked_services,
        'categories': categories,
        'unlocked_category_slugs': list(unlocked_categories),
        'available_assessment_categories': unlock_state['available_assessment_categories'],
        'other_option': {
            'key': 'others',
            'label': 'Others',
            'description': 'Use this when the service is not present in catalog.',
        },
    })


def _coerce_line_text(value, *, max_len=255):
    return str(value or '').strip()[:max_len]


def _is_meaningful_price_reason(value):
    text = str(value or '').strip()
    return len(text) >= 10 and any(ch.isspace() for ch in text)


def _normalize_additional_items_payload(data):
    raw_items = data.get('items')
    if isinstance(raw_items, list) and raw_items:
        return raw_items

    # Backward compatibility for older single-item payloads.
    return [
        {
            'service_id': data.get('service_id'),
            'custom_title': data.get('custom_title'),
            'custom_price': data.get('custom_price'),
            'category_slug': data.get('category_slug'),
            'description': data.get('description'),
            'price': data.get('price'),
            'price_update_reason': data.get('price_update_reason'),
        }
    ]


def _build_additional_razorpay_order(order, amount, *, receipt_prefix='additional_order'):
    return razorpay_client.order.create(
        {
            'amount': int((amount * Decimal('100')).quantize(Decimal('1'))),
            'currency': 'INR',
            'receipt': f'{receipt_prefix}_{order.id}_{int(timezone.now().timestamp())}',
            'payment_capture': 1,
        }
    )


def _resolve_additional_line_items(user, consultant_profile, unlocked_categories, raw_items):
    resolved_items = []
    unlocked_categories = set(unlocked_categories or [])

    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            return None, Response(
                {'error': f'Each entry in items must be an object. Invalid entry at position {index}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        description = _coerce_line_text(
            raw_item.get('description') or raw_item.get('why_needed'),
            max_len=255,
        )
        if len(description) < 5:
            return None, Response(
                {'error': f'Please add a clear "why needed" note for item #{index} (minimum 5 characters).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = None
        service_title = ''
        category_name = ''
        category_slug = _coerce_line_text(raw_item.get('category_slug'), max_len=50).lower()
        base_price = Decimal('0.00')
        selected_price = Decimal('0.00')
        price_update_reason = _coerce_line_text(raw_item.get('price_update_reason'), max_len=255)
        is_custom = False

        service_id = raw_item.get('service_id')
        if service_id:
            service = Service.objects.filter(id=service_id, is_active=True).select_related('category').first()
            if service is None:
                return None, Response(
                    {'error': f'Selected service at item #{index} was not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not is_service_unlocked(service, unlocked_categories):
                return None, Response(
                    {'error': f'Service "{service.title}" is locked for your account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            base_price = Decimal(str(service.price or 0))
            if base_price <= 0:
                return None, Response(
                    {'error': f'Service "{service.title}" does not have a valid configured price.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            requested_price_raw = raw_item.get('price')
            if requested_price_raw in (None, ''):
                selected_price = base_price
            else:
                selected_price, parse_error = _parse_positive_decimal(requested_price_raw, 'price')
                if parse_error:
                    return None, parse_error
            if selected_price > MAX_ADDITIONAL_SERVICE_AMOUNT:
                return None, Response(
                    {
                        'error': (
                            f'Price for "{service.title}" cannot exceed '
                            f'Rs {int(MAX_ADDITIONAL_SERVICE_AMOUNT):,}.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if selected_price != base_price and not _is_meaningful_price_reason(price_update_reason):
                return None, Response(
                    {
                        'error': (
                            f'Please state a meaningful reason (minimum 10 characters, with spaces) '
                            f'for updating the price of "{service.title}".'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service_title = service.title
            category_name = getattr(service.category, 'name', 'General')
        else:
            is_custom = True
            custom_title = _coerce_line_text(raw_item.get('custom_title'), max_len=255)
            if not custom_title:
                return None, Response(
                    {'error': f'custom_title is required for custom item #{index}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not category_slug:
                return None, Response(
                    {'error': f'category_slug is required for custom item "{custom_title}".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if category_slug not in unlocked_categories:
                return None, Response(
                    {
                        'error': (
                            f'Category "{category_slug}" is locked. '
                            'Pass the assessment first to offer this custom service.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            custom_price_raw = raw_item.get('custom_price')
            if custom_price_raw in (None, ''):
                custom_price_raw = raw_item.get('price')

            selected_price, parse_error = _parse_positive_decimal(custom_price_raw, 'custom_price')
            if parse_error:
                return None, parse_error
            if selected_price > MAX_ADDITIONAL_SERVICE_AMOUNT:
                return None, Response(
                    {
                        'error': (
                            f'Custom price for "{custom_title}" cannot exceed '
                            f'Rs {int(MAX_ADDITIONAL_SERVICE_AMOUNT):,}.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            base_price = selected_price
            service = _get_or_create_custom_service(custom_title, selected_price, category_slug)
            if service is None:
                return None, Response(
                    {'error': f'Could not resolve a valid category for "{custom_title}".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service_title = custom_title
            category_name = getattr(service.category, 'name', '') or ASSESSMENT_CATEGORY_LABELS.get(
                category_slug,
                category_slug.replace('_', ' ').title(),
            )
            price_update_reason = ''

        resolved_items.append(
            {
                'service': service,
                'service_title': service_title,
                'category_name': category_name or 'General',
                'description': description,
                'selected_price': selected_price,
                'base_price': base_price,
                'price_update_reason': price_update_reason,
                'is_custom': is_custom,
                'category_slug': category_slug,
                'consultant_profile': consultant_profile,
                'user': user,
            }
        )

    return resolved_items, None


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def request_additional_service(request):
    """
    Create a pending Razorpay order for an additional service during a consultation.
    """
    user = request.user
    if user.role != 'CONSULTANT':
        return Response({'error': 'Only consultants can request additional payments.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        consultant_profile = user.consultant_service_profile
    except ConsultantServiceProfile.DoesNotExist:
        return Response({'error': 'Consultant profile not found.'}, status=status.HTTP_404_NOT_FOUND)

    booking_id = request.data.get('booking_id')
    if not booking_id:
        return Response({'error': 'booking_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    booking = get_object_or_404(ConsultationBooking, id=booking_id)
    if booking.consultant_id != user.id:
        return Response({'error': 'You are not authorized to request payment for this booking.'}, status=status.HTTP_403_FORBIDDEN)
    if booking.status == 'cancelled' or booking.payment_status != 'paid':
        return Response({'error': 'This booking is not active for additional payment requests.'}, status=status.HTTP_400_BAD_REQUEST)

    unlock_state = _get_consultant_unlock_state(user)
    unlocked_categories = set(unlock_state['unlocked_categories'])

    raw_items = _normalize_additional_items_payload(request.data)
    if len(raw_items) > 25:
        return Response(
            {'error': 'You can request up to 25 additional services in one request.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    resolved_items, resolution_error = _resolve_additional_line_items(
        user=user,
        consultant_profile=consultant_profile,
        unlocked_categories=unlocked_categories,
        raw_items=raw_items,
    )
    if resolution_error:
        return resolution_error
    if not resolved_items:
        return Response({'error': 'At least one additional service item is required.'}, status=status.HTTP_400_BAD_REQUEST)

    total_price = sum((item['selected_price'] for item in resolved_items), Decimal('0.00'))
    consultant_name = user.get_full_name() or user.username

    with transaction.atomic():
        order = ServiceOrder.objects.create(
            user=booking.client,
            total_amount=total_price,
            original_amount=total_price,
            discount_amount=Decimal('0.00'),
            status='pending',
            from_booking=booking,
            is_additional=True,
            initiated_by=user,
        )

        for line in resolved_items:
            OrderItem.objects.create(
                order=order,
                service=line['service'],
                selected_consultant=consultant_profile,
                selection_mode='manual',
                category=line['category_name'],
                service_title=line['service_title'],
                variant_name=line['description'],
                price=line['selected_price'],
                base_price=line['base_price'],
                price_update_reason=line['price_update_reason'],
                quantity=1,
            )

            Activity.objects.create(
                actor=user,
                target_user=booking.client,
                activity_type='additional_payment_requested',
                title=f"Added additional service: {line['service_title']}",
                content_object=order,
                metadata={
                    'booking_id': booking.id,
                    'order_id': order.id,
                    'service_title': line['service_title'],
                    'amount': str(line['selected_price']),
                    'base_price': str(line['base_price']),
                    'description': line['description'],
                    'price_update_reason': line['price_update_reason'],
                    'is_custom': line['is_custom'],
                },
            )

        razorpay_order = _build_additional_razorpay_order(order, total_price)
        order.razorpay_order_id = razorpay_order['id']
        order.save(update_fields=['razorpay_order_id'])

        Activity.objects.create(
            actor=user,
            target_user=booking.client,
            activity_type='additional_payment_requested',
            title=f"Additional payment request sent ({len(resolved_items)} services)",
            content_object=order,
            metadata={
                'booking_id': booking.id,
                'order_id': order.id,
                'item_count': len(resolved_items),
                'amount': str(total_price),
                'initiated_by_name': consultant_name,
            },
        )

        _push_additional_payment_notification(order)

    serialized = _serialize_additional_service_order(order)
    return Response({
        'success': True,
        'order_id': order.id,
        'razorpay_order_id': order.razorpay_order_id,
        'amount': float(total_price),
        'item_count': len(resolved_items),
        'order': serialized,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def pending_additional_requests(request):
    """
    Return pending additional payment requests for the active client profile.
    """
    user = get_active_profile(request)
    if user.role != 'CLIENT':
        return Response({'results': []})

    orders = (
        ServiceOrder.objects
        .filter(user=user, is_additional=True, status='pending')
        .select_related('initiated_by', 'from_booking')
        .prefetch_related('items__service')
        .order_by('-created_at')
    )

    return Response({
        'results': [_serialize_additional_service_order(order) for order in orders]
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def update_additional_request_selection(request):
    """
    Allow a client to partially approve additional service line items before paying.
    Rebuilds Razorpay order with the selected items only.
    """
    user = get_active_profile(request)
    if user.role != 'CLIENT':
        return Response(
            {'error': 'Only clients can update additional payment selections.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    order_id = request.data.get('order_id')
    selected_item_ids = request.data.get('selected_item_ids')
    if not order_id:
        return Response({'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(selected_item_ids, list):
        return Response({'error': 'selected_item_ids must be an array.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        selected_ids = {int(item_id) for item_id in selected_item_ids}
    except (TypeError, ValueError):
        return Response({'error': 'selected_item_ids must contain valid numeric IDs.'}, status=status.HTTP_400_BAD_REQUEST)

    if not selected_ids:
        return Response(
            {'error': 'Select at least one service item or use decline for the full request.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    order = get_object_or_404(
        ServiceOrder.objects.select_related('initiated_by', 'from_booking').prefetch_related('items__service'),
        id=order_id,
        user=user,
        is_additional=True,
    )
    if order.status != 'pending':
        return Response(
            {'error': 'This additional payment request is no longer pending.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    current_items = list(order.items.all())
    current_ids = {item.id for item in current_items}
    if not selected_ids.issubset(current_ids):
        return Response(
            {'error': 'One or more selected items are invalid for this order.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    removed_ids = sorted(current_ids - selected_ids)
    if not removed_ids:
        return Response({'success': True, 'order': _serialize_additional_service_order(order)})

    with transaction.atomic():
        order.items.exclude(id__in=selected_ids).delete()
        selected_items = list(OrderItem.objects.filter(order=order))
        if not selected_items:
            order.status = 'cancelled'
            order.save(update_fields=['status', 'updated_at'])
            return Response({'success': True, 'cancelled': True})

        new_total = sum((Decimal(item.price or 0) for item in selected_items), Decimal('0.00'))
        razorpay_order = _build_additional_razorpay_order(order, new_total, receipt_prefix='additional_partial')

        order.total_amount = new_total
        order.original_amount = new_total
        order.razorpay_order_id = razorpay_order['id']
        order.save(update_fields=['total_amount', 'original_amount', 'razorpay_order_id', 'updated_at'])

        Activity.objects.create(
            actor=user,
            target_user=order.initiated_by or user,
            activity_type='additional_payment_requested',
            title='Client updated additional service selection',
            content_object=order,
            metadata={
                'order_id': order.id,
                'booking_id': order.from_booking_id,
                'removed_item_ids': removed_ids,
                'selected_item_ids': sorted(selected_ids),
                'new_total': str(new_total),
            },
        )

    if order.initiated_by_id:
        actor_name = user.get_full_name() or user.username
        _create_and_push_notification(
            recipient=order.initiated_by,
            category='payment',
            title='Client updated additional payment items',
            message=(
                f'{actor_name} selected {len(selected_ids)} item(s) and '
                f'updated the pending additional payment request.'
            ),
            link='/dashboard',
            extra_payload={
                'type': 'ADDITIONAL_SELECTION_UPDATED',
                'order_id': order.id,
            },
        )

    return Response({'success': True, 'order': _serialize_additional_service_order(order)})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def decline_additional_request(request):
    """
    Allow a client to decline a pending additional payment request.
    """
    user = get_active_profile(request)
    if user.role != 'CLIENT':
        return Response({'error': 'Only clients can decline additional payment requests.'}, status=status.HTTP_403_FORBIDDEN)

    order_id = request.data.get('order_id')
    if not order_id:
        return Response({'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    order = get_object_or_404(
        ServiceOrder.objects.select_related('initiated_by', 'from_booking').prefetch_related('items__service'),
        id=order_id,
        user=user,
        is_additional=True,
    )
    if order.status != 'pending':
        return Response({'error': 'This additional payment request is no longer pending.'}, status=status.HTTP_400_BAD_REQUEST)

    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])

    serialized = _serialize_additional_service_order(order)
    service_title = serialized.get('service_title') or 'Additional Service'

    if order.initiated_by_id:
        consultant_name = order.initiated_by.get_full_name() or order.initiated_by.username
        actor_name = user.get_full_name() or user.username
        _create_and_push_notification(
            recipient=order.initiated_by,
            category='payment',
            title=f'Additional payment declined for {service_title}',
            message=f'{actor_name} declined the additional payment request.',
            link='/dashboard',
            extra_payload={
                'type': 'ADDITIONAL_REQUEST_DECLINED',
                'order_id': order.id,
            },
        )

        Activity.objects.create(
            actor=user,
            target_user=order.initiated_by,
            activity_type='additional_payment_requested',
            title=f"Additional payment declined for {service_title}",
            description=f"{actor_name} declined the request from {consultant_name}.",
            content_object=order,
            metadata={
                'booking_id': order.from_booking_id,
                'service_title': service_title,
                'status': 'declined',
                'order_id': order.id,
            },
        )

    return Response({'success': True, 'order_id': order.id})
