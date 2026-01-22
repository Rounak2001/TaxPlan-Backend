from django.db import models
from django.conf import settings
import os
import uuid

def document_file_path(instance, filename):
    """
    Generate a unique file path for uploaded documents.
    Format: vault/client_<id>/<uuid>_<filename>
    """
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}_{filename}"
    return os.path.join('vault', f'client_{instance.client.id}', filename)

class Document(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Request'),
        ('UPLOADED', 'Uploaded'),
        ('VERIFIED', 'Verified'),
        ('REJECTED', 'Rejected'),
    ]

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_documents'
    )
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_documents'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=document_file_path, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} - {self.client.username} ({self.status})"

    class Meta:
        ordering = ['-created_at']
