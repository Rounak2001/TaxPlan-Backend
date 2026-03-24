from __future__ import annotations

from .assessment_outcome import get_session_assessment_outcome
from .category_access import extract_selected_service_ids, get_live_titles_for_onboarding_service_ids


def _get_live_consultant_profile(application, consultant_profile=None):
    if consultant_profile is not None:
        return consultant_profile

    from core_auth.models import User
    from consultants.models import ConsultantServiceProfile

    consultant_user = User.objects.filter(email=application.email, role=User.CONSULTANT).first()
    if not consultant_user:
        return None
    return ConsultantServiceProfile.objects.filter(user=consultant_user).first()


def sync_passed_sessions_to_consultant(application, consultant_profile=None):
    """
    Seed passed session selections into the live consultant expertise exactly once.
    Sessions are marked as seeded after processing so later manual dashboard edits are
    not overwritten on every page load.
    """
    profile = _get_live_consultant_profile(application, consultant_profile=consultant_profile)
    if profile is None:
        return {
            "profile_found": False,
            "seeded_sessions": 0,
            "created_expertise": 0,
        }

    from consultants.models import ConsultantServiceExpertise, Service

    seeded_sessions = 0
    created_expertise = 0
    passed_sessions = (
        application.assessment_sessions
        .filter(expertise_seeded=False)
        .exclude(status="ongoing")
        .order_by("id")
    )

    for session in passed_sessions:
        outcome = get_session_assessment_outcome(session)
        if not outcome["passed"]:
            continue

        selected_service_ids = extract_selected_service_ids(session.selected_test_details)
        live_titles = get_live_titles_for_onboarding_service_ids(selected_service_ids)
        live_services = Service.objects.filter(title__in=live_titles, is_active=True)

        for service in live_services:
            _, created = ConsultantServiceExpertise.objects.get_or_create(
                consultant=profile,
                service=service,
            )
            if created:
                created_expertise += 1

        session.expertise_seeded = True
        session.save(update_fields=["expertise_seeded"])
        seeded_sessions += 1

    return {
        "profile_found": True,
        "seeded_sessions": seeded_sessions,
        "created_expertise": created_expertise,
    }
