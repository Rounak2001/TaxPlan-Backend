from .models import UserSession, VideoResponse

MCQ_PASS_THRESHOLD = 30
VIDEO_PASS_THRESHOLD = 15
VIDEO_SCORE_PER_QUESTION = 5
MAX_FAILED_ATTEMPTS = 2


def get_session_assessment_outcome(session):
    if not session:
        return {
            'session_id': None,
            'status': 'not_started',
            'flagged': False,
            'passed': False,
            'failed': False,
            'review_pending': False,
            'hide_marks': False,
            'mcq_score': 0,
            'mcq_total': 0,
            'mcq_passed': False,
            'video_score': 0,
            'video_total_possible': 0,
            'video_expected': 0,
            'video_received': 0,
            'video_completed': 0,
            'video_evaluation_complete': False,
            'video_passed': False,
            'video_failed': False,
            'failure_reasons': [],
            'session': None,
        }

    video_responses = list(VideoResponse.objects.filter(session=session))
    expected_videos = len(session.video_question_set or [])
    received_videos = len(video_responses)
    completed_videos = sum(1 for vr in video_responses if vr.ai_status == 'completed')
    pending_videos = any(vr.ai_status in {'pending', 'processing'} for vr in video_responses)
    failed_videos = any(vr.ai_status == 'failed' for vr in video_responses)

    video_score = sum((vr.ai_score or 0) for vr in video_responses if vr.ai_score is not None)
    video_total_possible = expected_videos * VIDEO_SCORE_PER_QUESTION
    mcq_score = session.score or 0
    mcq_total = len(session.question_set or [])
    flagged = session.status == 'flagged'

    video_evaluation_complete = expected_videos > 0 and received_videos >= expected_videos and not pending_videos
    if expected_videos == 0:
        video_evaluation_complete = True

    review_pending = (not flagged) and not video_evaluation_complete
    mcq_passed = mcq_score >= MCQ_PASS_THRESHOLD
    video_passed = (expected_videos == 0) or (
        video_evaluation_complete and not failed_videos and video_score >= VIDEO_PASS_THRESHOLD
    )

    passed = (not flagged) and (not review_pending) and mcq_passed and video_passed
    failed = (not flagged) and (not review_pending) and not passed

    failure_reasons = []
    if failed:
        if mcq_score < MCQ_PASS_THRESHOLD:
            failure_reasons.append("Your MCQ assessment did not meet the required review criteria.")
        if failed_videos:
            failure_reasons.append("One or more video answers could not be evaluated.")
        elif expected_videos > 0 and video_score < VIDEO_PASS_THRESHOLD:
            failure_reasons.append("Your video assessment did not meet the required review criteria.")

    if flagged:
        status = 'flagged'
    elif review_pending:
        status = 'review_pending'
    elif passed:
        status = 'passed'
    else:
        status = 'failed'

    return {
        'session_id': session.id,
        'status': status,
        'flagged': flagged,
        'passed': passed,
        'failed': failed,
        'review_pending': review_pending,
        'hide_marks': flagged,
        'mcq_score': mcq_score,
        'mcq_total': mcq_total,
        'mcq_passed': mcq_passed,
        'video_score': video_score,
        'video_total_possible': video_total_possible,
        'video_expected': expected_videos,
        'video_received': received_videos,
        'video_completed': completed_videos,
        'video_evaluation_complete': video_evaluation_complete,
        'video_passed': video_passed,
        'video_failed': failed_videos,
        'failure_reasons': failure_reasons,
        'session': session,
    }


def get_application_assessment_outcome(application):
    sessions = list(
        UserSession.objects
        .filter(application=application)
        .exclude(status='ongoing')
        .order_by('-end_time', '-id')
    )

    latest_session = sessions[0] if sessions else None
    latest = get_session_assessment_outcome(latest_session)

    failed_attempts = 0
    flagged = False
    for session in sessions:
        outcome = get_session_assessment_outcome(session)
        if outcome['flagged']:
            flagged = True
            break
        if outcome['failed']:
            failed_attempts += 1

    disqualified = flagged or failed_attempts >= MAX_FAILED_ATTEMPTS
    attempts_remaining = 0 if disqualified else max(0, MAX_FAILED_ATTEMPTS - failed_attempts)

    latest.update({
        'disqualified': disqualified,
        'failed_attempts': failed_attempts,
        'attempts_remaining': attempts_remaining,
        'has_completed_session': latest_session is not None,
        'has_passed_assessment': latest['passed'],
    })
    return latest
