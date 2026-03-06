from django.db.models import Count

from .models import ProctoringSnapshot, Violation


def compute_proctoring_risk_summary(session):
    """Compute a simple, deterministic proctoring risk score + escalation policy."""
    snapshots_qs = ProctoringSnapshot.objects.filter(session=session)
    total_snapshots = snapshots_qs.count()
    violation_snapshots = snapshots_qs.filter(is_violation=True).count()

    violation_counts_rows = (
        Violation.objects
        .filter(session=session)
        .values('violation_type')
        .annotate(count=Count('id'))
    )
    violation_counts = {row['violation_type']: int(row['count']) for row in violation_counts_rows}

    face_count = int(violation_counts.get('face', 0))
    voice_count = int(violation_counts.get('voice', 0))
    pose_count = int(violation_counts.get('pose', 0))
    gaze_count = int(violation_counts.get('gaze', 0))
    tab_count = int(violation_counts.get('tab_switch', 0))

    fallback_count = 0
    for snap in snapshots_qs.only('rule_outcomes').iterator():
        outcomes = snap.rule_outcomes or {}
        processing_meta = outcomes.get('processing_meta', {}) if isinstance(outcomes, dict) else {}
        if bool(processing_meta.get('server_fallback_applied')):
            fallback_count += 1

    risk_score = 0.0
    risk_score += min(40.0, violation_snapshots * 4.0)
    risk_score += min(24.0, face_count * 6.0)
    risk_score += min(15.0, voice_count * 5.0)
    risk_score += min(12.0, pose_count * 4.0)
    risk_score += min(9.0, gaze_count * 3.0)
    risk_score += min(6.0, tab_count * 2.0)

    if session.status == 'flagged':
        risk_score = 100.0

    if session.status == 'flagged' or risk_score >= 80:
        escalation = 'auto_flag'
    elif risk_score >= 45:
        escalation = 'manual_review'
    else:
        escalation = 'clear'

    return {
        'risk_score': round(risk_score, 2),
        'escalation_policy': escalation,
        'signals': {
            'total_snapshots': total_snapshots,
            'violation_snapshots': violation_snapshots,
            'violation_counts': violation_counts,
            'server_fallback_count': fallback_count,
        },
    }
