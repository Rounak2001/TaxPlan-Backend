from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('flat', 'Flat Amount'),
    ]

    code = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    min_purchase_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    usage_limit = models.PositiveIntegerField(default=0, help_text='0 = unlimited')
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.discount_type}: {self.discount_value})"

    def is_valid(self, cart_total: Decimal) -> tuple[bool, str]:
        """Returns (is_valid, error_message)."""
        now = timezone.now()
        if not self.is_active:
            return False, 'This coupon is inactive.'
        if now < self.valid_from:
            return False, 'This coupon is not yet valid.'
        if now > self.valid_until:
            return False, 'This coupon has expired.'
        if self.usage_limit > 0 and self.used_count >= self.usage_limit:
            return False, 'This coupon has reached its usage limit.'
        if cart_total < self.min_purchase_amount:
            return False, f'Minimum purchase of ₹{self.min_purchase_amount} required.'
        return True, ''

    def calculate_discount(self, cart_total: Decimal) -> Decimal:
        """Returns discount amount (never exceeds cart_total)."""
        if self.discount_type == 'percentage':
            discount = (cart_total * self.discount_value / Decimal('100')).quantize(Decimal('0.01'))
        else:
            discount = self.discount_value

        if self.max_discount_amount is not None:
            discount = min(discount, self.max_discount_amount)

        return min(discount, cart_total)


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

    # Coupon / discount fields
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
    )
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    original_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Razorpay payment fields
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)

    # Additional in-call service payment metadata
    from_booking = models.ForeignKey(
        'consultations.ConsultationBooking',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='additional_service_orders',
        help_text='Set when this order was initiated during a live consultation call',
    )
    is_additional = models.BooleanField(
        default=False,
        help_text='True if this order was initiated by a consultant during a live consultation',
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='initiated_additional_orders',
        help_text='Consultant who requested this additional payment',
    )

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
