import razorpay
from django.conf import settings
from django.db import transaction
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import ServiceOrder, OrderItem
from .serializers import ServiceOrderSerializer

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def create_order(request):
    """
    1. Receive cart items total and details.
    2. Create a pending ServiceOrder in our DB.
    3. Create a Razorpay Order.
    4. Store razorpay_order_id and return to frontend.
    """
    user = request.user
    items_data = request.data.get('items', [])
    total_amount = sum(float(item.get('price', 0)) * int(item.get('quantity', 1)) for item in items_data)
    
    if total_amount <= 0:
        return Response({'error': 'Invalid total amount'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # Create our local order
            order = ServiceOrder.objects.create(
                user=user,
                total_amount=total_amount,
                status='pending'
            )
            
            # Create order items
            for item in items_data:
                OrderItem.objects.create(
                    order=order,
                    category=item.get('category'),
                    service_title=item.get('title'),
                    variant_name=item.get('variantName'),
                    price=item.get('price'),
                    quantity=item.get('quantity', 1)
                )

            # Create Razorpay Order (amount in paise)
            razorpay_order = razorpay_client.order.create({
                "amount": int(total_amount * 100),
                "currency": "INR",
                "receipt": f"receipt_order_{order.id}",
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
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def verify_payment(request):
    """
    1. Receive razorpay_payment_id, razorpay_order_id, razorpay_signature.
    2. Verify signature with Razorpay SDK.
    3. If valid, mark ServiceOrder as 'paid'.
    """
    razorpay_order_id = request.data.get('razorpay_order_id')
    razorpay_payment_id = request.data.get('razorpay_payment_id')
    razorpay_signature = request.data.get('razorpay_signature')

    params_dict = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }

    try:
        # Verify signature
        razorpay_client.utility.verify_payment_signature(params_dict)
        
        # Update our order status
        order = ServiceOrder.objects.get(razorpay_order_id=razorpay_order_id)
        order.status = 'paid'
        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_signature = razorpay_signature
        order.save()
        
        return Response({'status': 'Payment verified successfully'})
    except Exception as e:
        # If verification fails or order doesn't exist
        if 'order' in locals():
            order.status = 'failed'
            order.save(update_fields=['status'])
        return Response({'error': 'Payment verification failed'}, status=status.HTTP_400_BAD_REQUEST)
