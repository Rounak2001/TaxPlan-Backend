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


def shared_report_file_path(instance, filename):
    """
    Generate a unique file path for shared reports.
    Format: reports/client_<id>/<uuid>_<filename>
    """
    filename = f"{uuid.uuid4()}_{filename}"
    return os.path.join('reports', f'client_{instance.client.id}', filename)


class SharedReport(models.Model):
    """
    Reports shared by consultants with their clients.
    """
    REPORT_TYPE_CHOICES = [
        ('CMA', 'CMA Report'),
        ('GST', 'GST Report'),
        ('TAX', 'Tax Report'),
        ('AUDIT', 'Audit Report'),
        ('OTHER', 'Other Document'),
    ]

    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shared_reports'
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_reports'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=shared_report_file_path)
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES, default='OTHER')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - Shared with {self.client.username}"

    class Meta:
        ordering = ['-created_at']

