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
from .models import Coupon, ServiceOrder, OrderItem
from consultations.models import ConsultationBooking
from consultants.models import (
    Service, 
    ClientServiceRequest, 
    ConsultantServiceProfile,
    ConsultantServiceExpertise,
    ServiceCategory
)
from consultant_onboarding.category_access import (
    ASSESSMENT_CATEGORY_ORDER,
)
from activity_timeline.models import Activity
from .utils import create_service_requests_from_order
from core_auth.utils import get_active_profile
from .pricing import get_verified_price
from notifications.models import Notification
from activity_timeline.models import Activity
from consultations.models import ConsultationBooking
from consultant_onboarding.assessment_outcome import get_application_assessment_outcome
from consultant_onboarding.category_access import (
    ASSESSMENT_CATEGORY_ORDER,
    get_unlock_category_slugs_for_service,
    is_service_unlocked,
)
from consultant_onboarding.expertise_sync import sync_passed_sessions_to_consultant

import logging

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

logger = logging.getLogger('service_orders')
REGISTRATIONS_SLUG = 'registrations'


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
    item = order.items.select_related('service').first()
    service_title = 'Additional Service'
    description = ''
    amount = order.total_amount

    if item:
        service_title = item.service.title if item.service else (item.service_title or service_title)
        description = item.variant_name or ''
        amount = item.price or amount

    consultant_name = ''
    consultant = getattr(order, 'initiated_by', None)
    if consultant:
        consultant_name = consultant.get_full_name() or consultant.username

    return {
        'order_id': order.id,
        'service_title': service_title,
        'description': description,
        'amount': float(amount or 0),
        'consultant_name': consultant_name,
        'razorpay_order_id': order.razorpay_order_id,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'created_at': order.created_at.isoformat() if order.created_at else None,
        'booking_id': order.from_booking_id,
    }


def _push_additional_payment_notification(order):
    payload = _serialize_additional_service_order(order)
    notification = Notification.objects.create(
        recipient=order.user,
        category='payment',
        title=f"Payment requested for {payload['service_title']}",
        message=payload['description'] or 'Your consultant requested an additional service payment.',
        link='/client',
    )

    channel_layer = get_channel_layer()
    if not channel_layer:
        return notification

    async_to_sync(channel_layer.group_send)(
        f"user_{order.user_id}",
        {
            'type': 'notification_message',
            'data': {
                'id': notification.id,
                'type': 'PAYMENT_REQUEST',
                'category': 'payment',
                'title': notification.title,
                'message': notification.message,
                'link': notification.link,
                'is_read': False,
                **payload,
            },
        },
    )
    return notification


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def validate_coupon(request):
    """Validate a coupon code and return the discount preview."""
    code = (request.data.get('code') or '').strip().upper()
    cart_total = Decimal(str(request.data.get('cart_total', 0)))

    if not code:
        return Response({'error': 'Coupon code is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        coupon = Coupon.objects.get(code__iexact=code)
    except Coupon.DoesNotExist:
        return Response({'valid': False, 'error': 'Invalid coupon code'}, status=status.HTTP_404_NOT_FOUND)

    if not coupon.is_valid:
        return Response({'valid': False, 'error': 'This coupon has expired or is no longer available'})

    discount = coupon.calculate_discount(cart_total)
    if discount <= 0:
        return Response({
            'valid': False,
            'error': f'Minimum purchase of ₹{coupon.min_purchase_amount} required'
        })

    return Response({
        'valid': True,
        'code': coupon.code,
        'discount_type': coupon.discount_type,
        'discount_value': float(coupon.discount_value),
        'discount_amount': float(discount),
        'new_total': float(cart_total - discount),
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
    coupon_code = (request.data.get('coupon_code') or '').strip().upper()
    
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

        # --- Coupon logic ---
        original_amount = total_amount
        discount_amount = Decimal("0.00")
        coupon = None

        if coupon_code:
            try:
                coupon = Coupon.objects.get(code__iexact=coupon_code)
            except Coupon.DoesNotExist:
                return Response({'error': 'Invalid coupon code'}, status=status.HTTP_400_BAD_REQUEST)

            if not coupon.is_valid:
                return Response({'error': 'This coupon has expired or is no longer available'}, status=status.HTTP_400_BAD_REQUEST)

            discount_amount = coupon.calculate_discount(total_amount)
            total_amount = max(total_amount - discount_amount, Decimal("0.00"))

            if total_amount <= 0:
                return Response({'error': 'Discount makes total zero — please remove coupon or add more items'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # 2. Create Order
            order = ServiceOrder.objects.create(
                user=user,
                original_amount=original_amount,
                discount_amount=discount_amount,
                total_amount=total_amount,
                coupon=coupon,
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

            # 4. Increment coupon usage
            if coupon:
                Coupon.objects.filter(id=coupon.id).update(used_count=F('used_count') + 1)

            # 5. Razorpay Order
            razorpay_order = razorpay_client.order.create({
                "amount": int((total_amount * Decimal("100")).quantize(Decimal("1"))), # paise
                "currency": "INR",
                "receipt": f"receipt_order_{order.id}",
                "payment_capture": 1 
            })
            
            order.razorpay_order_id = razorpay_order['id']
            order.save(update_fields=['razorpay_order_id'])
            
            return Response({
                'order_id': order.id,
                'razorpay_order_id': razorpay_order['id'],
                'amount': float(total_amount),
                'original_amount': float(original_amount),
                'discount_amount': float(discount_amount),
                'coupon_code': coupon.code if coupon else None,
                'amount_paise': razorpay_order['amount'],
                'key_id': settings.RAZORPAY_KEY_ID
            })

    except Exception as e:
        print(f"Order creation error: {str(e)}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
                # Lock the order row
                order = ServiceOrder.objects.select_related(
                        'user', 'user__parent_account'
                    ).select_for_update().get(razorpay_order_id=razorpay_order_id)
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
    user = request.user
    if user.role != 'CONSULTANT':
        return Response({'error': 'Only consultants can view additional service options.'}, status=status.HTTP_403_FORBIDDEN)

    unlock_state = _get_consultant_unlock_state(user)
    application = unlock_state['application']
    if application is not None:
        try:
            sync_passed_sessions_to_consultant(application)
        except Exception:
            logger.exception("Failed to sync consultant expertise before loading additional service options")

    unlocked_categories = set(unlock_state['unlocked_categories'])
    services = Service.objects.filter(is_active=True).select_related('category').order_by('title')

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
    })


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

    service = None
    service_title = ''
    category_name = ''
    price = Decimal('0.00')
    description = (request.data.get('description') or '').strip()

    service_id = request.data.get('service_id')
    custom_title = (request.data.get('custom_title') or '').strip()
    custom_price = request.data.get('custom_price')
    category_slug = (request.data.get('category_slug') or '').strip().lower()

    if service_id:
        service = get_object_or_404(Service.objects.select_related('category'), id=service_id, is_active=True)
        if not is_service_unlocked(service, unlocked_categories):
            return Response({'error': 'This service is still locked for your account.'}, status=status.HTTP_400_BAD_REQUEST)
        service_title = service.title
        category_name = getattr(service.category, 'name', 'General')
        price = Decimal(str(service.price or 0))
        if price <= 0:
            return Response({'error': 'Selected service does not have a valid price.'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        if not custom_title:
            return Response({'error': 'custom_title is required when no service_id is provided.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(custom_title) > 255:
            return Response({'error': 'Custom service title is too long.'}, status=status.HTTP_400_BAD_REQUEST)
        if not category_slug:
            return Response({'error': 'category_slug is required for custom services.'}, status=status.HTTP_400_BAD_REQUEST)
        if category_slug not in unlocked_categories:
            return Response({'error': 'This category is still locked for your account.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            price = Decimal(str(custom_price))
        except (InvalidOperation, TypeError, ValueError):
            price = Decimal('0.00')
        if price <= 0:
            return Response({'error': 'Please enter a valid custom price.'}, status=status.HTTP_400_BAD_REQUEST)

        service_title = custom_title
        category_name = ASSESSMENT_CATEGORY_LABELS.get(category_slug, category_slug.replace('_', ' ').title())

    if len(description) > 255:
        return Response({'error': 'Description must be 255 characters or fewer.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        order = ServiceOrder.objects.create(
            user=booking.client,
            total_amount=price,
            original_amount=price,
            discount_amount=Decimal('0.00'),
            status='pending',
            from_booking=booking,
            is_additional=True,
            initiated_by=user,
        )

        OrderItem.objects.create(
            order=order,
            service=service,
            selected_consultant=consultant_profile,
            selection_mode='manual',
            category=category_name or 'General',
            service_title=service_title,
            variant_name=description or '',
            price=price,
            quantity=1,
        )

        razorpay_order = razorpay_client.order.create({
            'amount': int((price * Decimal('100')).quantize(Decimal('1'))),
            'currency': 'INR',
            'receipt': f'additional_order_{order.id}',
            'payment_capture': 1,
        })
        order.razorpay_order_id = razorpay_order['id']
        order.save(update_fields=['razorpay_order_id'])

        Activity.objects.create(
            actor=user,
            target_user=booking.client,
            activity_type='additional_payment_requested',
            title=f"Additional payment requested for {service_title}",
            content_object=order,
            metadata={
                'booking_id': booking.id,
                'service_title': service_title,
                'amount': str(price),
                'description': description,
            },
        )

        _push_additional_payment_notification(order)

    return Response({
        'success': True,
        'order_id': order.id,
        'razorpay_order_id': order.razorpay_order_id,
        'amount': float(price),
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

    item = order.items.select_related('service').first()
    service_title = item.service.title if item and item.service else (getattr(item, 'service_title', '') or 'Additional Service')

    if order.initiated_by_id:
        consultant_name = order.initiated_by.get_full_name() or order.initiated_by.username
        Notification.objects.create(
            recipient=order.initiated_by,
            category='payment',
            title=f'Additional payment declined for {service_title}',
            message=f'{user.get_full_name() or user.username} declined the payment request.',
            link='/dashboard',
        )

        Activity.objects.create(
            actor=user,
            target_user=order.initiated_by,
            activity_type='additional_payment_requested',
            title=f"Additional payment declined for {service_title}",
            description=f"{user.get_full_name() or user.username} declined the request from {consultant_name}.",
            content_object=order,
            metadata={
                'booking_id': order.from_booking_id,
                'service_title': service_title,
                'status': 'declined',
            },
        )

    return Response({'success': True})
