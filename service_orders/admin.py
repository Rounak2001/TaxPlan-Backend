from django.contrib import admin
from .models import Coupon, ServiceOrder, OrderItem


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'is_active', 'used_count', 'usage_limit', 'valid_from', 'valid_until')
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code', 'description')
    readonly_fields = ('used_count', 'created_at')
    list_editable = ('is_active',)


@admin.register(ServiceOrder)
class ServiceOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'original_amount', 'discount_amount', 'total_amount', 'coupon', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'razorpay_order_id', 'coupon__code')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'service_title', 'price', 'quantity')
    search_fields = ('service_title', 'order__id')
