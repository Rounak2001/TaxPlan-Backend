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

class Folder(models.Model):
    """
    Logical grouping for documents in a client's vault.
    """
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vault_folders'
    )
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_folders'
    )
    is_system = models.BooleanField(default=False, help_text="System folders cannot be renamed or deleted")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vault_folders'
        unique_together = ('client', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.client.username})"


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
    folder = models.ForeignKey(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=document_file_path, null=True, blank=True)
    file_password = models.CharField(max_length=255, null=True, blank=True)
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


def legal_notice_file_path(instance, filename):
    """
    Generate a unique file path for legal notices.
    Format: notices/client_<id>/<uuid>_<filename>
    """
    filename = f"{uuid.uuid4()}_{filename}"
    return os.path.join('notices', f'client_{instance.client.id}', filename)


class LegalNotice(models.Model):
    """
    Legal notices, orders, or communications shared between consultant and client.
    """
    SOURCE_CHOICES = [
        ('INCOME_TAX', 'Income Tax Dept'),
        ('GST', 'GST Department'),
        ('MCA', 'Ministry of Corporate Affairs'),
        ('OTHER', 'Other Authority'),
    ]
    
    TYPE_CHOICES = [
        ('NOTICE', 'Notice'),
        ('ORDER', 'Order'),
        ('COMMUNICATION', 'General Communication'),
    ]
    
    PRIORITY_CHOICES = [
        ('URGENT', 'Urgent'),
        ('HIGH', 'High'),
        ('MEDIUM', 'Medium'),
        ('LOW', 'Low'),
    ]

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='legal_notices'
    )
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='managed_notices'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    file = models.FileField(upload_to=legal_notice_file_path)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='OTHER')
    notice_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='NOTICE')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_notices'
    )
    
    due_date = models.DateField(null=True, blank=True)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.client.username} ({self.priority})"

    class Meta:
        ordering = ['-created_at']

