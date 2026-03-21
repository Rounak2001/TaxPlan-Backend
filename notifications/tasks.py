import logging
from celery import shared_task
from .whatsapp_service import send_whatsapp_template
from .whatsapp_webhook import WhatsAppWebhookView

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def send_whatsapp_template_task(self, phone_number, template_name, variables=None):
    """
    Celery task to send a WhatsApp template message asynchronously.
    """
    try:
        success, message = send_whatsapp_template(phone_number, template_name, variables)
        if not success:
            logger.warning(f"Celery task failed to send WA template: {message}. Retrying...")
            raise self.retry(countdown=5 * self.request.retries)
        return success
    except Exception as exc:
        logger.error(f"Error in send_whatsapp_template_task: {exc}")
        raise self.retry(exc=exc, countdown=5 * self.request.retries)

@shared_task(bind=True, max_retries=3)
def send_whatsapp_text_task(self, phone_number, text, message_id=None):
    """
    Celery task to send a direct WhatsApp text message asynchronously.
    Updates the Message.delivery_channel based on the result.
    """
    def _update_message_delivery(msg_id, channel):
        """Update message delivery_channel and broadcast to consultant."""
        if not msg_id:
            return
        try:
            from chat.models import Message
            msg = Message.objects.select_related('conversation').get(id=msg_id)
            msg.delivery_channel = channel
            msg.save(update_fields=['delivery_channel'])
            
            # Broadcast delivery status to the conversation's WebSocket group
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_{msg.conversation.id}",
                {
                    'type': 'delivery_status',
                    'message_id': msg_id,
                    'delivery_channel': channel,
                }
            )
            logger.info(f"Updated message {msg_id} delivery_channel to '{channel}'")
        except Exception as e:
            logger.error(f"Failed to update message delivery status: {e}")

    try:
        webhook_view = WhatsAppWebhookView()
        success, error_data = webhook_view.send_whatsapp_text(phone_number, text)
        
        if not success:
            # Check if it failed due to the 24-hour window (Meta Error 131047)
            error_code = error_data.get('code') if error_data else None
            if error_code == 131047:
                logger.warning(f"Meta 24hr window closed for {phone_number}. Falling back to template via Celery.")
                
                # Try to fetch client name from DB for personalization
                client_name = "Client"
                try:
                    from core_auth.models import User
                    user = User.objects.filter(phone_number__icontains=phone_number[-10:]).first()
                    if user:
                        client_name = user.first_name or user.username
                except Exception as e:
                    logger.error(f"Error fetching user name for WA template: {e}")

                # Truncate content for preview
                preview_parts = text.split("]: ", 1)
                if len(preview_parts) == 2:
                    consultant_name = preview_parts[0].replace("[", "")
                    content = preview_parts[1]
                else:
                    consultant_name = "Consultant"
                    content = text
                
                preview = content[:80] + ('…' if len(content) > 80 else '')
                
                # Send the fallback template asynchronously as well
                send_whatsapp_template_task.delay(
                    phone_number=phone_number,
                    template_name="unread_secure_message",
                    variables=[
                        client_name,
                        consultant_name,
                        preview
                    ]
                )
                _update_message_delivery(message_id, 'wa_template')
                return False
            
            # Other errors, retry
            logger.warning(f"Celery task failed to send WA text: {error_data}. Retrying...")
            if self.request.retries >= self.max_retries - 1:
                _update_message_delivery(message_id, 'wa_failed')
            raise self.retry(countdown=5 * self.request.retries)
            
        _update_message_delivery(message_id, 'wa_text')
        return True
    except Exception as exc:
        logger.error(f"Error in send_whatsapp_text_task: {exc}")
        if self.request.retries >= self.max_retries - 1:
            _update_message_delivery(message_id, 'wa_failed')
        raise self.retry(exc=exc, countdown=5 * self.request.retries)

@shared_task(bind=True, max_retries=3)
def send_service_assignment_email_task(self, client_email, client_name, service_title, amount_paid):
    """
    Celery task to send an email confirming the service assignment and amount paid.
    """
    from django.core.mail import send_mail
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    logger.info(f"Sending service assignment email to {client_email} for service '{service_title}'")
    
    try:
        subject = f"Order Confirmation: {service_title}"
        
        # We can use a simple HTML string or basic text formatting if no template exists
        html_message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2>Service Order Confirmation</h2>
                <p>Hi {client_name},</p>
                <p>Thank you for choosing our services. Your purchase was successful and your service has been assigned to our expert team!</p>
                <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <p style="margin: 0;"><strong>Service Taken:</strong> {service_title}</p>
                    <p style="margin: 5px 0 0 0;"><strong>Amount Paid:</strong> ₹{amount_paid}</p>
                </div>
                <p>You can log into your client dashboard to communicate with your consultant, upload necessary documents, and track your service workflow.</p>
                <br>
                <p>Best regards,<br><strong>TaxPlanAdvisor Team</strong></p>
            </body>
        </html>
        """
        plain_message = strip_tags(html_message)
        from_email = settings.DEFAULT_FROM_EMAIL
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=from_email,
            recipient_list=[client_email],
            fail_silently=False,
            html_message=html_message
        )
        return True
    except Exception as exc:
        logger.error(f"Error sending service assignment email to {client_email}: {exc}")
        raise self.retry(exc=exc, countdown=10 * self.request.retries)
