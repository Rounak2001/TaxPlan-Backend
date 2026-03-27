from django.db import models
from django.conf import settings
from django.utils import timezone


class Coupon(models.Model):
    """Discount coupon that clients can apply at checkout."""
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount'),
    ]

    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.CharField(max_length=255, blank=True, help_text="Internal note, e.g. 'Diwali 2026 promo'")
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text="Percentage (e.g. 10 for 10%) or fixed amount in INR")
    min_purchase_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Minimum cart total required to use this coupon")
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Cap on discount for percentage coupons (optional)")
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    usage_limit = models.PositiveIntegerField(default=1, help_text="Total times this coupon can be redeemed")
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()}: {self.discount_value})"

    @property
    def is_valid(self):
        now = timezone.now()
        return self.is_active and self.valid_from <= now <= self.valid_until and self.used_count < self.usage_limit

    def calculate_discount(self, cart_total):
        """Return the absolute discount amount for a given cart total."""
        from decimal import Decimal
        cart_total = Decimal(str(cart_total))

        if cart_total < self.min_purchase_amount:
            return Decimal("0.00")

        if self.discount_type == 'percentage':
            discount = (cart_total * self.discount_value) / Decimal("100")
            if self.max_discount_amount:
                discount = min(discount, self.max_discount_amount)
        else:
            discount = min(self.discount_value, cart_total)

        return discount.quantize(Decimal("0.01"))


class ServiceOrder(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='service_orders'
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Coupon / discount tracking
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    original_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Cart total before coupon discount")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Absolute discount applied")

    # Razorpay payment fields
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order {self.id} - {self.user.username} ({self.status})"

class OrderItem(models.Model):
    order = models.ForeignKey(
        ServiceOrder,
        on_delete=models.CASCADE,
        related_name='items'
    )
    
    # Link to consultant service
    service = models.ForeignKey(
        'consultants.Service',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_items'
    )
    
    # Consultant selection
    SELECTION_MODE_CHOICES = [
        ('auto', 'Auto-Assigned'),
        ('manual', 'Manually Chosen'),
    ]
    selected_consultant = models.ForeignKey(
        'consultants.ConsultantServiceProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='order_items_selected',
        help_text="Consultant chosen by client (manual) or system (auto)"
    )
    selection_mode = models.CharField(
        max_length=10, choices=SELECTION_MODE_CHOICES, default='auto'
    )

    # Keep existing fields for backward compatibility
    category = models.CharField(max_length=100)
    service_title = models.CharField(max_length=255)
    variant_name = models.CharField(max_length=255, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.service_title} ({self.order.id})"
