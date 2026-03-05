from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ConsultantServiceProfile(models.Model):
    """Extended profile for consultants with service-specific professional details"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='consultant_service_profile')
    
    qualification = models.CharField(max_length=255, blank=True)
    experience_years = models.IntegerField(default=0)
    certifications = models.TextField(blank=True)  # JSON or comma-separated
    bio = models.TextField(blank=True, help_text="Short professional biography displayed to clients")
    
    # Consultation fee (moved from core_auth.ConsultantProfile)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=200.00)
    
    # Availability
    is_active = models.BooleanField(default=True)
    max_concurrent_clients = models.IntegerField(default=5)
    current_client_count = models.IntegerField(default=0)
    last_assigned_at = models.DateTimeField(null=True, blank=True)
    
    # Ratings & Reviews
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00, help_text="Average rating from 1 to 5")
    total_reviews = models.IntegerField(default=0)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def full_name(self):
        """Delegate to User model — no more duplicate data."""
        return self.user.get_full_name() or self.user.username
    
    @property
    def email(self):
        """Delegate to User model."""
        return self.user.email
    
    @property
    def phone(self):
        """Delegate to User model."""
        return self.user.phone_number or ''
    
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
        ('assigned', 'Consultant Assigned'),
        ('doc_pending', 'Documents Pending'),
        ('under_review', 'Under Review'),
        ('wip', 'Work In Progress'),
        ('under_query', 'Clarification Needed'),
        ('final_review', 'Final Review'),
        ('filed', 'Work Filed/Submitted'),
        ('revision_pending', 'Revision Requested'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    # Centralized list of statuses considered "active" or "in-progress"
    # Services in these states should have active document synchronization and vault management.
    ACTIVE_STATUSES = [
        'assigned', 
        'doc_pending', 
        'under_review', 
        'wip', 
        'under_query', 
        'final_review', 
        'filed', 
        'revision_pending'
    ]

    client = models.ForeignKey(
User, on_delete=models.CASCADE, related_name='service_requests')
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
    revision_notes = models.TextField(blank=True, null=True)
    priority = models.IntegerField(default=0)  # Higher = more urgent
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.client.email} - {self.service.title} ({self.status})"


class ConsultantReview(models.Model):
    """Stores client reviews for a completed service"""
    consultant = models.ForeignKey(ConsultantServiceProfile, on_delete=models.CASCADE, related_name='reviews')
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='consultant_reviews')
    service_request = models.OneToOneField(ClientServiceRequest, on_delete=models.CASCADE, related_name='review')
    
    rating = models.IntegerField(choices=[(i, i) for i in range(1, 6)], help_text="Rating from 1 to 5")
    review_text = models.TextField(blank=True, help_text="Optional text review from the client")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['client', 'service_request']
        
    def __str__(self):
        return f"Review by {self.client.email} for {self.consultant.full_name} ({self.rating} stars)"

