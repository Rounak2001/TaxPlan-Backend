"""
Auto-credential generation service.
Checks whether a consultant application has fully cleared onboarding and, if so,
auto-generates consultant credentials.
"""
import logging

from .assessment_outcome import get_application_assessment_outcome
from .models import ConsultantCredential, ConsultantDocument, IdentityDocument

logger = logging.getLogger(__name__)


def _has_only_verified_identity_docs(application):
    id_docs = IdentityDocument.objects.filter(application=application)
    if not id_docs.exists():
        return False, "No identity documents"
    if id_docs.exclude(verification_status__iexact="Verified").exists():
        return False, "Unverified identity documents"
    return True, None


def _has_verified_qualification_docs(application):
    qual_docs = ConsultantDocument.objects.filter(application=application)
    if not qual_docs.exists():
        return False, "No qualification documents"

    bachelors_docs = qual_docs.filter(document_type="bachelors_degree")
    if not bachelors_docs.exists():
        return False, "No bachelor's degree document"
    if bachelors_docs.exclude(verification_status__iexact="Verified").exists():
        return False, "Bachelor's degree not verified"

    if qual_docs.exclude(verification_status__iexact="Verified").exists():
        return False, "Unverified qualification documents"
    return True, None


def get_auto_credential_blocker(application):
    if ConsultantCredential.objects.filter(application=application).exists():
        return "Credentials already generated"

    if not application.has_accepted_declaration:
        return "Declaration not accepted"

    if not application.is_onboarded:
        return "Profile onboarding incomplete"

    if not application.is_phone_verified:
        return "Phone not verified"

    if not application.is_verified:
        return "Face verification incomplete"

    identity_ok, identity_reason = _has_only_verified_identity_docs(application)
    if not identity_ok:
        return identity_reason

    docs_ok, docs_reason = _has_verified_qualification_docs(application)
    if not docs_ok:
        return docs_reason

    assessment = get_application_assessment_outcome(application)
    if assessment["review_pending"]:
        return "Assessment review pending"
    if not assessment["has_completed_session"]:
        return "No completed assessment session"
    if not assessment["has_passed_assessment"]:
        return f"Assessment not passed ({assessment['status']})"

    return None


def check_and_auto_generate_credentials(application):
    """
    Generate credentials automatically once the applicant has fully completed and
    passed all required onboarding checks.
    """
    app_label = f"Application {application.id} ({application.email})"
    blocker = get_auto_credential_blocker(application)
    if blocker:
        logger.info("%s: auto-credential check blocked: %s", app_label, blocker)
        return False, blocker

    assessment = get_application_assessment_outcome(application)
    logger.info(
        "%s: all onboarding checks cleared (assessment=%s, mcq=%s, video=%s). Auto-generating credentials.",
        app_label,
        assessment["status"],
        assessment["mcq_score"],
        assessment["video_score"],
    )

    from .views.admin_panel import _generate_and_send_credentials

    success, result = _generate_and_send_credentials(application)
    if success:
        logger.info("%s: credentials auto-generated successfully.", app_label)
        return True, "Auto-generated credentials successfully"

    logger.error("%s: auto-generation failed: %s", app_label, result)
    return False, f"Auto-generation failed: {result}"


def trigger_auto_credential_check(application, source_label="unknown"):
    """
    Best-effort wrapper for checkpoint hooks. Never raises back into the caller.
    """
    try:
        success, message = check_and_auto_generate_credentials(application)
        if success:
            logger.info(
                "Auto-credentials triggered for %s from %s.",
                application.email,
                source_label,
            )
        else:
            logger.debug(
                "Auto-credential check for %s from %s: %s",
                application.email,
                source_label,
                message,
            )
        return success, message
    except Exception as exc:
        logger.warning(
            "Auto-credential check failed for %s from %s: %s",
            application.email,
            source_label,
            exc,
        )
        return False, str(exc)
