from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

User = settings.AUTH_USER_MODEL


class Activity(models.Model):
    """
    Central activity tracking model using Django's ContentType framework
    for polymorphic relationships to any model (Document, Service, CallLog, etc.)
    """
    ACTIVITY_TYPES = [
        ('document_upload', 'Document Uploaded'),
        ('document_verify', 'Document Verified'),
        ('document_reject', 'Document Rejected'),
        ('service_new', 'New Service Request'),
        ('service_status', 'Service Status Changed'),
        ('service_complete', 'Service Completed'),
        ('call_made', 'Call Made'),
        ('call_received', 'Call Received'),
        ('system_reminder', 'System Reminder'),
        ('profile_update', 'Profile Updated'),
    ]
    
    # Who performed the activity (consultant, client, or system)
    actor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='activities_performed',
        help_text="User who performed this activity"
    )
    
    # Who the activity is about (usually the client)
    target_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='activities_received',
        help_text="User this activity is about"
    )
    
    # Activity metadata
    activity_type = models.CharField(max_length=30, choices=ACTIVITY_TYPES)
    title = models.CharField(max_length=255, help_text="Brief activity description")
    description = models.TextField(blank=True, null=True, help_text="Detailed description")
    
    # Generic foreign key to related object (Document, Service, CallLog, etc.)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    
    # Additional metadata (JSON field for flexibility)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional activity data (duration, status, etc.)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        db_table = 'activity_timeline'
        ordering = ['-created_at']
        verbose_name = 'Activity'
        verbose_name_plural = 'Activities'
        indexes = [
            models.Index(fields=['actor', '-created_at']),
            models.Index(fields=['target_user', '-created_at']),
            models.Index(fields=['activity_type', '-created_at']),
            models.Index(fields=['content_type', 'object_id']),
        ]
    
    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.title} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
