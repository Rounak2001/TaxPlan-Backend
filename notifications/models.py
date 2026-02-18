from django.db import models
from django.conf import settings

class Notification(models.Model):
    CATEGORIES = [
        ('document', 'Document'),
        ('service', 'Service'),
        ('meeting', 'Meeting'),
        ('payment', 'Payment'),
        ('system', 'System'),
    ]

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    category = models.CharField(max_length=20, choices=CATEGORIES, default='system')
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True)  # Frontend route to navigate to
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f"{self.recipient.username} - {self.title}"
