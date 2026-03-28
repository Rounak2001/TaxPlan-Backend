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
<<<<<<< HEAD
    list_display = ('id', 'user', 'status', 'original_amount', 'discount_amount', 'total_amount', 'coupon', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'razorpay_order_id', 'coupon__code')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(OrderItem)
>>>>>>> 010e372d0f96270b1228b9bf0df32e13e7c4423a
