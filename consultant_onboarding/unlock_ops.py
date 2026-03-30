from __future__ import annotations

from django.utils import timezone

from consultant_onboarding.assessment_outcome import get_application_assessment_outcome
from consultant_onboarding.models import UserSession

MAIN_UNLOCK_DOMAINS = ("itr", "gstr", "scrutiny")


def _normalize_domains(values) -> list[str]:
    ordered = []
    seen = set()
    for value in values or []:
        slug = str(value or "").strip().lower()
        if slug and slug not in seen and slug in MAIN_UNLOCK_DOMAINS:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def derive_domains_from_completed_sessions(application) -> list[str]:
    discovered = set()
    sessions = (
        UserSession.objects
        .filter(application=application)
        .exclude(status="ongoing")
        .order_by("-end_time", "-id")
    )
    for session in sessions:
        for domain in (session.selected_domains or []):
            slug = str(domain or "").strip().lower()
            if slug in MAIN_UNLOCK_DOMAINS:
                discovered.add(slug)
    return [slug for slug in MAIN_UNLOCK_DOMAINS if slug in discovered]


def create_manual_unlock_session(application, domains: list[str], source: str) -> UserSession | None:
    normalized_domains = _normalize_domains(domains)
    if not normalized_domains:
        return None

    return UserSession.objects.create(
        application=application,
        selected_domains=normalized_domains,
        selected_test_details={
            "manual_unlock": {
                "source": source,
                "domains": normalized_domains,
            }
        },
        question_set=[{"id": 1, "domain": normalized_domains[0]}],
        video_question_set=[],
        score=35,
        status="completed",
        end_time=timezone.now(),
    )


def ensure_unlock_from_completed_sessions(application) -> bool:
    """
    Backfill unlock state if assessment outcome has no passed session but
    completed session domains exist. Returns True when a new session was created.
    """
    outcome = get_application_assessment_outcome(application)
    if outcome.get("has_passed_assessment"):
        return False

    domains = derive_domains_from_completed_sessions(application)
    if not domains:
        return False

    return bool(create_manual_unlock_session(application, domains, source="compat_backfill"))


def force_unlock_all_main_categories(application) -> bool:
    """
    Force-unlock ITR/GSTR/Scrutiny by creating one completed admin-marked session.
    Returns True when a new session was created.
    """
    outcome = get_application_assessment_outcome(application)
    unlocked = set(outcome.get("unlocked_categories", []))
    if all(slug in unlocked for slug in MAIN_UNLOCK_DOMAINS):
        return False

    return bool(create_manual_unlock_session(application, list(MAIN_UNLOCK_DOMAINS), source="admin_force_all"))
