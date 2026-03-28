from django.contrib import admin
from .models import Coupon, ServiceOrder, OrderItem


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'discount_type', 'discount_value', 'min_purchase_amount',
        'max_discount_amount', 'used_count', 'usage_limit', 'is_active',
        'valid_from', 'valid_until',
    ]
    list_filter = ['is_active', 'discount_type']
    search_fields = ['code', 'description']
    readonly_fields = ['used_count', 'created_at']
    ordering = ['-created_at']


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['service', 'category', 'service_title', 'variant_name', 'price', 'quantity']


@admin.register(ServiceOrder)
class ServiceOrderAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'total_amount', 'discount_amount', 'coupon',
        'status', 'is_additional', 'created_at',
    ]
    list_filter = ['status', 'is_additional']
    search_fields = ['user__email', 'razorpay_order_id', 'coupon__code']
    readonly_fields = ['created_at', 'updated_at', 'razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature']
    inlines = [OrderItemInline]
