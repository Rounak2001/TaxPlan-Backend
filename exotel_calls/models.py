from django.db import models
from django.conf import settings


class CallLog(models.Model):
    """
    Logs each call initiated via the Exotel API.
    Stores complete call details from Exotel callbacks.
    """
    STATUS_CHOICES = [
        ('initiated', 'Initiated'),
        ('ringing', 'Ringing'),
        ('in-progress', 'In Progress'),
        ('completed', 'Completed'),
        ('busy', 'Busy'),
        ('failed', 'Failed'),
        ('no-answer', 'No Answer'),
        ('canceled', 'Canceled'),
    ]
    
    OUTCOME_CHOICES = [
        ('connected', 'Connected - Successful'),
        ('voicemail', 'Left Voicemail'),
        ('no_answer', 'No Answer'),
        ('busy', 'Line Busy'),
        ('callback', 'Callback Requested'),
        ('interested', 'Interested - Follow Up'),
        ('not_interested', 'Not Interested'),
        ('wrong_number', 'Wrong Number'),
        ('other', 'Other'),
    ]
    
    # Caller (Consultant) - FK to User
    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='calls_made'
    )
    # Callee (Client) - FK to User
    callee = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='calls_received'
    )
    
    # Exotel specific fields
    exotel_sid = models.CharField(max_length=100, blank=True, null=True, help_text="Call SID from Exotel")
    
    # Call metadata
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='initiated')
    duration = models.IntegerField(default=0, help_text="Duration in seconds")
    recording_url = models.URLField(blank=True, null=True, max_length=500)
    
    # Exotel detailed data (from callback)
    from_number = models.CharField(max_length=20, blank=True, null=True, help_text="Masked for privacy")
    to_number = models.CharField(max_length=20, blank=True, null=True, help_text="Masked for privacy")
    price = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True, help_text="Call cost")
    start_time = models.DateTimeField(blank=True, null=True)
    end_time = models.DateTimeField(blank=True, null=True)
    
    # Consultant notes (post-call)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, help_text="Notes from the consultant")
    follow_up_date = models.DateField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Call Log'
        verbose_name_plural = 'Call Logs'
    
    def __str__(self):
        return f"Call from {self.caller} to {self.callee} - {self.status}"
    
    @property
    def duration_display(self):
        """Returns duration in MM:SS format."""
        mins = self.duration // 60
        secs = self.duration % 60
        return f"{mins:02d}:{secs:02d}"

