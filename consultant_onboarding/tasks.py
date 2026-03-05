from celery import shared_task
from .services import VideoEvaluator
from .models import VideoResponse
import logging

logger = logging.getLogger(__name__)

@shared_task
def evaluate_video_task(video_response_id, question_text):
    """
    Background task to evaluate a video response.
    It transcribes the video using AWS Transcribe and evaluates the transcript via Gemini API.
    """
    logger.info(f"Starting async evaluation for VideoResponse ID: {video_response_id}")
    
    try:
        video_response = VideoResponse.objects.get(id=video_response_id)
    except VideoResponse.DoesNotExist:
        logger.error(f"VideoResponse ID {video_response_id} not found.")
        return

    # Update status to processing (should already be set by view, but good practice here too)
    video_response.ai_status = 'processing'
    video_response.save(update_fields=['ai_status'])

    evaluator = VideoEvaluator()
    try:
        # Run transcription + Gemini evaluation
        result = evaluator.process_video(video_response, question_text)
        
        # Save results
        video_response.ai_transcript = result.get('transcript', '')
        video_response.ai_score = result.get('score', 0)
        video_response.ai_feedback = result.get('feedback', {})
        video_response.ai_status = 'completed'
        video_response.save()
        logger.info(f"Successfully evaluated VideoResponse ID {video_response_id}")

        # After successful evaluation, check if all conditions are met for auto-credential generation
        try:
            from .credential_service import check_and_auto_generate_credentials
            application = video_response.session.application
            success, msg = check_and_auto_generate_credentials(application)
            if success:
                logger.info(f"Auto-credentials triggered for {application.email} after video eval.")
            else:
                logger.debug(f"Auto-credential check for {application.email}: {msg}")
        except Exception as cred_err:
            # Never let credential generation failure break the video evaluation task
            logger.warning(f"Auto-credential check failed (non-fatal): {cred_err}")
        
    except Exception as e:
        logger.error(f"Failed to evaluate VideoResponse ID {video_response_id}: {e}")
        video_response.ai_status = 'failed'
        video_response.save(update_fields=['ai_status'])
        raise e

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

