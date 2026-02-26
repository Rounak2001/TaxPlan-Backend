"""
Auto-credential generation service.
Checks if a consultant application meets all criteria and auto-generates credentials.
"""
import logging
from .models import (
    ConsultantApplication,
    IdentityDocument,
    ConsultantDocument,
    ConsultantCredential,
    UserSession,
    VideoResponse,
)

logger = logging.getLogger(__name__)


def check_and_auto_generate_credentials(application):
    """
    Checks if an application meets all criteria to automatically receive credentials:
      1. Has not already received credentials.
      2. Has at least one IdentityDocument and all are 'Verified'.
      3. Has at least one ConsultantDocument and all are 'Verified'.
      4. Latest completed assessment session has MCQ score >= 30.
      5. All VideoResponses in that session are 'completed' and total ai_score >= 15.
    If all conditions are met, generates and emails credentials.
    """
    app_label = f"Application {application.id} ({application.email})"

    # 1. Already has credentials?
    if ConsultantCredential.objects.filter(application=application).exists():
        logger.info(f"{app_label}: already has credentials. Skipping.")
        return False, "Credentials already generated"

    # 2. Identity Documents — all must exist and be Verified
    id_docs = IdentityDocument.objects.filter(application=application)
    if not id_docs.exists():
        logger.info(f"{app_label}: no identity documents. Skipping.")
        return False, "No identity documents"

    if id_docs.exclude(verification_status='Verified').exists():
        logger.info(f"{app_label}: has unverified identity documents. Skipping.")
        return False, "Unverified identity documents"

    # 3. Consultant Documents — all must exist and be Verified
    qual_docs = ConsultantDocument.objects.filter(application=application)
    if not qual_docs.exists():
        logger.info(f"{app_label}: no qualification documents. Skipping.")
        return False, "No qualification documents"

    if qual_docs.exclude(verification_status='Verified').exists():
        logger.info(f"{app_label}: has unverified qualification documents. Skipping.")
        return False, "Unverified qualification documents"

    # 4. Assessment — latest completed session with MCQ score >= 30
    latest_session = (
        UserSession.objects
        .filter(application=application, status='completed')
        .order_by('-end_time')
        .first()
    )
    if not latest_session:
        logger.info(f"{app_label}: no completed assessment session. Skipping.")
        return False, "No completed assessment session"

    mcq_score = latest_session.score or 0
    if mcq_score < 30:
        logger.info(f"{app_label}: MCQ score {mcq_score} < 30. Skipping.")
        return False, f"MCQ score {mcq_score} below threshold (30)"

    # 5. Video responses — all must be completed, total score >= 15
    video_responses = VideoResponse.objects.filter(session=latest_session)
    if video_responses.filter(ai_status='pending').exists() or \
       video_responses.filter(ai_status='processing').exists():
        logger.info(f"{app_label}: has pending/processing video evaluations. Skipping.")
        return False, "Incomplete video evaluations"

    if video_responses.filter(ai_status='failed').exists():
        logger.info(f"{app_label}: has failed video evaluations. Skipping.")
        return False, "Failed video evaluations"

    video_score = sum(vr.ai_score for vr in video_responses if vr.ai_score is not None)
    if video_score < 15:
        logger.info(f"{app_label}: video score {video_score} < 15. Skipping.")
        return False, f"Video score {video_score} below threshold (15)"

    # All conditions met — generate credentials
    logger.info(
        f"{app_label}: ALL conditions met (MCQ: {mcq_score}, Video: {video_score}). "
        f"Auto-generating credentials."
    )

    from .views.admin_panel import _generate_and_send_credentials
    success, result = _generate_and_send_credentials(application)

    if success:
        logger.info(f"{app_label}: credentials auto-generated successfully.")
        return True, "Auto-generated credentials successfully"
    else:
        logger.error(f"{app_label}: auto-generation failed: {result}")
        return False, f"Auto-generation failed: {result}"
