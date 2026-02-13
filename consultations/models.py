from django.db import models
from django.conf import settings

class Topic(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    consultants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='topics',
        limit_choices_to={'role': 'CONSULTANT'},
        blank=True
    )

    def __str__(self):
        return self.name

class WeeklyAvailability(models.Model):
    DAY_CHOICES = [
        (0, 'Sunday'),
        (1, 'Monday'),
        (2, 'Tuesday'),
        (3, 'Wednesday'),
        (4, 'Thursday'),
        (5, 'Friday'),
        (6, 'Saturday'),
    ]
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='weekly_schedules'
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        return f"{self.consultant.username} - {self.get_day_of_week_display()} ({self.start_time}-{self.end_time})"

class DateOverride(models.Model):
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='date_overrides'
    )
    date = models.DateField()
    is_unavailable = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ['date', 'start_time']

    def __str__(self):
        if self.is_unavailable:
            return f"{self.consultant.username} - {self.date} (Unavailable)"
        return f"{self.consultant.username} - {self.date} ({self.start_time}-{self.end_time})"


class ConsultationBooking(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]
    
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='consultant_bookings',
        limit_choices_to={'role': 'CONSULTANT'}
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='client_bookings',
        limit_choices_to={'role': 'CLIENT'}
    )
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT)
    booking_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    notes = models.TextField(blank=True, help_text="Client's query or meeting details")
    
    # Payment fields
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Email tracking fields
    confirmation_sent = models.BooleanField(default=False, help_text="Whether confirmation email was sent")
    reminder_sent = models.BooleanField(default=False, help_text="Whether 24-hour reminder was sent")
    
    # Recording fields
    recording_id = models.CharField(max_length=100, blank=True, null=True, help_text="TaskID for recording")
    recording_url = models.URLField(max_length=500, blank=True, null=True, help_text="Final S3 recording URL")
    meeting_link = models.URLField(max_length=500, blank=True, null=True, help_text="Google Meet meeting link")
    bot_triggered = models.BooleanField(default=False, help_text="Whether the recording bot was launched")
    bot_recorded = models.BooleanField(default=False, help_text="Whether the recording was successfully started")
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-booking_date', '-start_time']

    def __str__(self):
        return f"{self.client.username} → {self.consultant.username} on {self.booking_date} ({self.start_time}-{self.end_time})"
