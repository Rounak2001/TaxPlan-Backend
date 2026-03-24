import uuid
from django.db import models
from django.conf import settings


class Conversation(models.Model):
    """
    Represents a chat conversation between a consultant and a client.
    Each consultant-client pair can have only one conversation (UniqueConstraint).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='consultant_conversations',
        limit_choices_to={'role': 'CONSULTANT'}
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='client_conversations',
        limit_choices_to={'role': 'CLIENT'}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['consultant', 'client'],
                name='unique_consultant_client_conversation'
            )
        ]
        ordering = ['-updated_at']

    def __str__(self):
        return f"Conversation: {self.consultant.username} <-> {self.client.username}"


class Message(models.Model):
    """
    Represents a single message within a conversation.
    """
    DELIVERY_CHANNEL_CHOICES = [
        ('dashboard', 'Dashboard Only'),
        ('wa_pending', 'WhatsApp Pending'),
        ('wa_text', 'WhatsApp Text Sent'),
        ('wa_template', 'WhatsApp Template Fallback'),
        ('wa_failed', 'WhatsApp Failed'),
    ]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages'
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    delivery_channel = models.CharField(
        max_length=20,
        choices=DELIVERY_CHANNEL_CHOICES,
        default='dashboard',
        help_text='How this message was delivered to the recipient'
    )

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"Message from {self.sender.username} at {self.timestamp}"
