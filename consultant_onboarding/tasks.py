import os
import random
import re
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from .services import VideoEvaluator
from .models import VideoResponse
import logging
from google.api_core.exceptions import ResourceExhausted

logger = logging.getLogger(__name__)

def _parse_retry_delay_seconds(message):
    text = str(message or '')
    m = re.search(r'Please retry in ([0-9]+(?:\.[0-9]+)?)s', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r'retry_delay\\s*\\{\\s*seconds:\\s*([0-9]+)', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _is_gemini_quota_error(exc):
    if isinstance(exc, ResourceExhausted):
        return True
    msg = str(exc or '').lower()
    return 'quota' in msg and 'exceeded' in msg and ('429' in msg or 'resourceexhausted' in msg)


@shared_task(bind=True)
def evaluate_video_task(self, video_response_id, question_text):
    """
    Background task to evaluate a video response.
    It asks Gemini to transcribe and evaluate the video in one pass.
    """
    logger.info(f"Starting async evaluation for VideoResponse ID: {video_response_id}")
    
    try:
        video_response = VideoResponse.objects.get(id=video_response_id)
    except VideoResponse.DoesNotExist:
        logger.error(f"VideoResponse ID {video_response_id} not found.")
        return

    evaluator = VideoEvaluator()
    try:
        # Keep status as "processing" while we work (and through retries).
        if video_response.ai_status != 'processing':
            video_response.ai_status = 'processing'
            video_response.save(update_fields=['ai_status'])

        local_video_path = evaluator.download_video_to_temp(video_response)
        try:
            evaluation = evaluator.evaluate_transcript('', question_text, local_video_path)
        finally:
            try:
                os.remove(local_video_path)
            except OSError:
                pass

        if not isinstance(evaluation, dict):
            raise ValueError("Gemini evaluation response must be a JSON object.")

        transcript = evaluation.get('transcript', '')
        if transcript is None:
            transcript = ''
        elif not isinstance(transcript, str):
            transcript = str(transcript)
        transcript = transcript.strip()
        
        # Save results
        video_response.ai_score = int(evaluation.get('score', 0) or 0)
        video_response.ai_transcript = transcript
        video_response.ai_feedback = evaluation
        video_response.ai_status = 'completed'
        video_response.save(update_fields=['ai_score', 'ai_transcript', 'ai_feedback', 'ai_status'])
        logger.info(f"Successfully evaluated VideoResponse ID {video_response_id}")

        # After successful evaluation, check if all conditions are met for auto-credential generation
        try:
            from .credential_service import trigger_auto_credential_check
            application = video_response.session.application
            trigger_auto_credential_check(application, "video_evaluation")
        except Exception as cred_err:
            # Never let credential generation failure break the video evaluation task
            logger.warning(f"Auto-credential check failed (non-fatal): {cred_err}")
        
    except Exception as e:
        if _is_gemini_quota_error(e):
            retry_delay_s = _parse_retry_delay_seconds(e) or 8.0
            max_retries = int(os.getenv('GEMINI_429_MAX_RETRIES', '6'))
            # Respect server-provided delay, add small jitter, cap to 2 minutes.
            countdown = max(3, min(120, int(round(retry_delay_s + random.uniform(0.5, 2.5)))))

            # Keep status in processing and attach a machine-readable reason.
            video_response.ai_status = 'processing'
            video_response.ai_feedback = {
                'error': 'Gemini quota/rate-limit exceeded. Retrying.',
                'code': 'GEMINI_QUOTA_EXCEEDED',
                'retries': int(getattr(self.request, 'retries', 0) or 0),
                'next_retry_in_s': countdown,
            }
            video_response.save(update_fields=['ai_status', 'ai_feedback'])

            try:
                raise self.retry(exc=e, countdown=countdown, max_retries=max_retries)
            except MaxRetriesExceededError:
                logger.error(f"Gemini quota exceeded and max retries reached for VideoResponse ID {video_response_id}: {e}")
                video_response.ai_status = 'failed'
                video_response.ai_feedback = {
                    'error': str(e),
                    'code': 'GEMINI_QUOTA_EXCEEDED',
                }
                video_response.save(update_fields=['ai_status', 'ai_feedback'])
                return

        logger.error(f"Failed to evaluate VideoResponse ID {video_response_id}: {e}")
        video_response.ai_status = 'failed'
        video_response.ai_feedback = {
            'error': str(e),
            'code': 'VIDEO_EVAL_FAILED',
        }
        video_response.save(update_fields=['ai_status', 'ai_feedback'])
        return

@shared_task
def test_mail_task(recipient_email):
    """
    Test task to verify if Celery can send emails.
    """
    from django.core.mail import send_mail
    from django.conf import settings
    import os

    logger.info(f"DEBUG: Celery test_mail_task started for {recipient_email}")
    logger.info(f"DEBUG: EMAIL_HOST_USER in task: {settings.EMAIL_HOST_USER}")
    logger.info(f"DEBUG: EMAIL_HOST in task: {settings.EMAIL_HOST}")
    
    try:
        subject = "Celery Email Diagnostic Test"
        message = "This is a test email sent from a Celery background worker."
        from_email = settings.DEFAULT_FROM_EMAIL
        
        send_mail(subject, message, from_email, [recipient_email], fail_silently=False)
        logger.info(f"DEBUG: Celery test_mail_task SUCCESS for {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"DEBUG: Celery test_mail_task FAILED: {str(e)}")
        return str(e)

