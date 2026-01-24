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
    services = models.JSONField(default=list)  # e.g., ["GST", "ITR"]

    def __str__(self):
        return f"Consultant: {self.user.username}"

class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile')
    assigned_consultant = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        limit_choices_to={'role': User.CONSULTANT},
        related_name='assigned_clients'
    )
    pan_number = models.CharField(max_length=10, null=True, blank=True)
    gstin = models.CharField(max_length=15, null=True, blank=True)
    gst_username = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Client: {self.user.username}"
