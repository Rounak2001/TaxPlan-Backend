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
        
    except Exception as e:
        logger.error(f"Failed to evaluate VideoResponse ID {video_response_id}: {e}")
        video_response.ai_status = 'failed'
        video_response.save(update_fields=['ai_status'])
        raise e
