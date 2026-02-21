import razorpay
import json
from django.conf import settings
from django.db import transaction
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import ServiceOrder, OrderItem
from consultants.models import Service, ClientServiceRequest
from .utils import create_service_requests_from_order

import logging

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

logger = logging.getLogger('service_orders')

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def create_order(request):
    """
    1. Receive cart items.
    2. Fetch REAL prices from Service model (Security Fix).
    3. Create a pending ServiceOrder.
    4. Create Razorpay Order.
    """
    user = request.user
    items_data = request.data.get('items', [])
    
    logger.debug(f"create_order called by {user.email}")
    logger.debug(f"Razorpay Key ID: {settings.RAZORPAY_KEY_ID}")
    
    if not items_data:
        logger.warning("No items in cart")
        return Response({'error': 'No items in cart'}, status=status.HTTP_400_BAD_REQUEST)

    total_amount = 0
    valid_items = []

    try:
        # 1. Validate items and calculate total from DB
        for item in items_data:
            service_id = item.get('service_id')
            qty = int(item.get('quantity', 1))
            
            service = None
            if service_id:
                try:
                    service = Service.objects.get(id=service_id)
                except Service.DoesNotExist:
                    pass
            
            # Fallback for old frontend logic (if passing title but no ID) - Not recommended but safe if we query DB
            if not service and item.get('title'):
                service = Service.objects.filter(title=item.get('title')).first()

            if not service:
                return Response(
                    {'error': f"Service not found: {item.get('title', 'Unknown')}"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not service.price:
                 return Response(
                    {'error': f"Service {service.title} has no price configured"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Security: Use DB price, ignore frontend price
            item_total = float(service.price) * qty
            total_amount += item_total
            
            valid_items.append({
                'service': service,
                'quantity': qty,
                'price': service.price, # Record the price at time of booking
                'category': service.category.name if service.category else 'General',
                'title': service.title,
                'variant': item.get('variantName', '')
            })

        if total_amount <= 0:
            return Response({'error': 'Invalid total amount'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # 2. Create Order
            order = ServiceOrder.objects.create(
                user=user,
                total_amount=total_amount,
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
                    quantity=valid_item['quantity']
                )

            # 4. Razorpay Order
            razorpay_order = razorpay_client.order.create({
                "amount": int(total_amount * 100), # paise
                "currency": "INR",
                "receipt": f"receipt_order_{order.id}",
                "payment_capture": 1 
            })
            
            order.razorpay_order_id = razorpay_order['id']
            order.save(update_fields=['razorpay_order_id'])
            
            return Response({
                'order_id': order.id,
                'razorpay_order_id': razorpay_order['id'],
                'amount': total_amount,
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
