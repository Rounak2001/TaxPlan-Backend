from rest_framework import serializers
from .models import ServiceOrder, OrderItem

class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'category', 'service_title', 'variant_name', 'price', 'quantity']

class ServiceOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    
    class Meta:
        model = ServiceOrder
        fields = [
            'id', 'total_amount', 'status', 'items',
            'razorpay_order_id', 'razorpay_payment_id',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'total_amount', 'status', 'razorpay_order_id', 'created_at']
