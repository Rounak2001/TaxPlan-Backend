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
def send_whatsapp_text_task(self, phone_number, text):
    """
    Celery task to send a direct WhatsApp text message asynchronously.
    """
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
                    # Strip any non-digits for cleaner search if needed or use the formatted phone
                    # Usually stored as +91... in DB
                    search_phone = f"+{phone_number}" if not phone_number.startswith('+') else phone_number
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
                return False
            
            # Other errors, retry
            logger.warning(f"Celery task failed to send WA text: {error_data}. Retrying...")
            raise self.retry(countdown=5 * self.request.retries)
            
        return True
    except Exception as exc:
        logger.error(f"Error in send_whatsapp_text_task: {exc}")
        raise self.retry(exc=exc, countdown=5 * self.request.retries)
