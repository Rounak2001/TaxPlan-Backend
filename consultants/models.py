from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ConsultantServiceProfile(models.Model):
    """Extended profile for consultants with service-specific professional details"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='consultant_service_profile')
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15)
    
    # Professional details
    qualification = models.CharField(max_length=255)
    experience_years = models.IntegerField()
    certifications = models.TextField(blank=True)  # JSON or comma-separated
    
    # Availability
    is_active = models.BooleanField(default=True)
    max_concurrent_clients = models.IntegerField(default=5)
    current_client_count = models.IntegerField(default=0)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.full_name} - {self.email}"


class ServiceCategory(models.Model):
    """Main service categories (Income Tax, GST, Registration, etc.)"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name_plural = "Service Categories"
    
    def __str__(self):
        return self.name


class Service(models.Model):
    """Individual services within categories"""
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='services')
    title = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tat = models.CharField(max_length=100)  # Turnaround time
    documents_required = models.TextField()
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['category', 'title']
    
    def __str__(self):
        return f"{self.category.name} - {self.title}"


class ConsultantServiceExpertise(models.Model):
    """Many-to-Many relationship between consultants and services they offer"""
    consultant = models.ForeignKey(ConsultantServiceProfile, on_delete=models.CASCADE, related_name='service_expertise')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='consultants')
    
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['consultant', 'service']
        verbose_name_plural = "Consultant Service Expertise"
    
    def __str__(self):
        return f"{self.consultant.full_name} - {self.service.title}"


class ClientServiceRequest(models.Model):
    """Tracks client service requests and their status"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending Assignment'),
        ('assigned', 'Assigned to Consultant'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='service_requests')
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Assignment details
    assigned_consultant = models.ForeignKey(
        ConsultantServiceProfile, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='assigned_requests'
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    
    # Additional info
    notes = models.TextField(blank=True)
    priority = models.IntegerField(default=0)  # Higher = more urgent
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.client.email} - {self.service.title} ({self.status})"
