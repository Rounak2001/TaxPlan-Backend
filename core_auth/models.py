import uuid

from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ADMIN = 'ADMIN'
    CONSULTANT = 'CONSULTANT'
    CLIENT = 'CLIENT'
    
    ROLE_CHOICES = [
        (ADMIN, 'Admin'),
        (CONSULTANT, 'Consultant'),
        (CLIENT, 'Client'),
    ]
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=CLIENT)
    phone_number = models.CharField(max_length=15, unique=True, null=True, blank=True)
    is_phone_verified = models.BooleanField(default=False)
    is_onboarded = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.username} ({self.role})"

class ConsultantProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='consultant_profile')
    max_capacity = models.IntegerField(default=10)
    current_load = models.IntegerField(default=0)
    current_load = models.IntegerField(default=0)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=200.00)

    def __str__(self):
        return f"Consultant: {self.user.username}"

class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile')
    pan_number = models.CharField(max_length=10, null=True, blank=True)
    gstin = models.CharField(max_length=15, null=True, blank=True)
    gst_username = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Client: {self.user.username}"


class ContactSubmission(models.Model):
    STATUS_CHOICES = (
        ('NEW', 'New'),
        ('IN_PROGRESS', 'In Progress'),
        ('RESOLVED', 'Resolved'),
    )

    INQUIRY_CHOICES = (
        ('Feedback', 'Feedback'),
        ('Sales', 'Sales'),
        ('Support', 'Support'),
        ('Partnership', 'Partnership'),
        ('Other', 'Other')
    )

    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    inquiry_type = models.CharField(max_length=50, choices=INQUIRY_CHOICES, default='Other')
    message = models.TextField(max_length=2000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.inquiry_type}) - {self.status}"
    
    class Meta:
        ordering = ['-created_at']


class MagicLinkToken(models.Model):
    """
    Short-lived, single-use tokens for magic link login and password reset.
    """
    LOGIN = 'LOGIN'
    PASSWORD_RESET = 'PASSWORD_RESET'
    PURPOSE_CHOICES = [
        (LOGIN, 'Magic Link Login'),
        (PASSWORD_RESET, 'Password Reset'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='magic_link_tokens')
    token = models.CharField(max_length=64, unique=True, db_index=True)  # 32-char hex UUID
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=LOGIN)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"MagicLinkToken({self.purpose}) for {self.user.email} - {'used' if self.used else 'active'}"

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        return not self.used and not self.is_expired
