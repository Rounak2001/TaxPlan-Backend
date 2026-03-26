from django.db import models
from django.conf import settings
from consultants.models import ClientServiceRequest

class Ticket(models.Model):
    CATEGORY_CHOICES = [
        ('platform_issue', 'Platform Issue'),
        ('billing', 'Billing'),
        ('consultant_complaint', 'Consultant Complaint'),
        ('client_complaint', 'Client Complaint'),
        ('service_delay', 'Service Delay'),
        ('other', 'Other'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('reopened', 'Reopened'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tickets')
    ticket_id = models.CharField(max_length=20, unique=True, blank=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    related_service = models.ForeignKey(ClientServiceRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name='related_tickets')
    subject = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    
    admin_notes = models.TextField(blank=True, help_text="Internal notes for staff only.")
    resolution = models.TextField(blank=True, help_text="Visible to the user when status is resolved.")
    admin_viewed_at = models.DateTimeField(null=True, blank=True)
    attachment = models.FileField(upload_to='ticket_attachments/', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.ticket_id} - {self.subject}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.ticket_id:
            self.ticket_id = f"TKT-{str(self.pk).zfill(5)}"
            super().save(update_fields=['ticket_id'])


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    is_admin_reply = models.BooleanField(default=False)
    message = models.TextField()
    attachment = models.FileField(upload_to='ticket_attachments/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Comment by {self.author} on {self.ticket.ticket_id}"

    def save(self, *args, **kwargs):
        if not self.pk:
            self.is_admin_reply = self.author.is_staff or self.author.is_superuser
        super().save(*args, **kwargs)
