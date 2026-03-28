from django.contrib import admin

from .models import Coupon, OrderItem, ServiceOrder


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "discount_type",
        "discount_value",
        "min_purchase_amount",
        "max_discount_amount",
        "used_count",
        "usage_limit",
        "is_active",
        "valid_from",
        "valid_until",
    ]
    list_filter = ["is_active", "discount_type"]
    search_fields = ["code", "description"]
    readonly_fields = ["used_count", "created_at"]
    ordering = ["-created_at"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["service", "category", "service_title", "variant_name", "price", "quantity"]


@admin.register(ServiceOrder)
class ServiceOrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "original_amount",
        "discount_amount",
        "total_amount",
        "coupon",
        "from_booking",
        "initiated_by",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("user__email", "user__username", "razorpay_order_id", "coupon__code")
    readonly_fields = ("created_at",)
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "service_title", "category", "price", "quantity", "selection_mode")
    list_filter = ("selection_mode",)
    search_fields = ("service_title", "category", "order__user__email")

