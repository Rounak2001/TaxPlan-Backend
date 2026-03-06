"""Central proctoring policy and response contract.

Single source of truth for backend proctoring thresholds.
Standardized snapshot request/response contract used by frontend.
"""

# Violation thresholds
MAX_SESSION_VIOLATIONS = 9
MAX_VIOLATIONS_PER_TYPE = 3
MAX_TAB_WARNINGS = 3
MAX_WEBCAM_WARNINGS = 3
MAX_FULLSCREEN_EXITS = 3
FULLSCREEN_REENTRY_GRACE_SECONDS = 10
HEAD_POSE_YAW_THRESHOLD = 15
HEAD_POSE_PITCH_THRESHOLD = 18
HEAD_POSE_ROLL_THRESHOLD = 15
HEAD_POSE_SUSTAINED_WINDOW = 5
HEAD_POSE_SUSTAINED_MIN_HITS = 3
GAZE_SUSTAINED_WINDOW = 5
GAZE_SUSTAINED_MIN_HITS = 3

# Device policy
DEVICE_POLICY = 'desktop_or_laptop_only'
DISALLOWED_DEVICE_KEYWORDS = (
    'android',
    'iphone',
    'ipad',
    'ipod',
    'mobile',
    'tablet',
)

# Snapshot request contract
SNAPSHOT_REQUIRED_FIELDS = ('image',)
SNAPSHOT_OPTIONAL_FIELDS = (
    'audio_detected',
    'gaze_violation',
    'pose_yaw',
    'pose_pitch',
    'pose_roll',
    'mouth_state',
    'label_detection_results',
    'fullscreen_state',
    'client_timestamp',
    'snapshot_id',
    'detector_status',
    'webcam_status',
    'mic_status',
)

# Snapshot response contract
STATUS_OK = 'ok'
STATUS_WARNING = 'warning'
STATUS_TERMINATED = 'terminated'


def is_supported_device(user_agent):
    """Allow desktop/laptop browsers and reject common mobile/tablet user agents."""
    if not user_agent:
        return True
    ua = str(user_agent).strip().lower()
    return not any(keyword in ua for keyword in DISALLOWED_DEVICE_KEYWORDS)


def parse_bool(value, default=False):
    """Parse form-data booleans safely with backward-compatible defaults."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'true', '1', 'yes', 'on'}


def policy_payload():
    """Expose proctoring policy for frontend display and enforcement."""
    return {
        'thresholds': {
            'max_session_violations': MAX_SESSION_VIOLATIONS,
            'max_violations_per_type': MAX_VIOLATIONS_PER_TYPE,
            'max_tab_warnings': MAX_TAB_WARNINGS,
            'max_webcam_warnings': MAX_WEBCAM_WARNINGS,
            'max_fullscreen_exits': MAX_FULLSCREEN_EXITS,
            'fullscreen_reentry_grace_seconds': FULLSCREEN_REENTRY_GRACE_SECONDS,
            'head_pose_yaw_threshold': HEAD_POSE_YAW_THRESHOLD,
            'head_pose_pitch_threshold': HEAD_POSE_PITCH_THRESHOLD,
            'head_pose_roll_threshold': HEAD_POSE_ROLL_THRESHOLD,
            'head_pose_sustained_window': HEAD_POSE_SUSTAINED_WINDOW,
            'head_pose_sustained_min_hits': HEAD_POSE_SUSTAINED_MIN_HITS,
            'gaze_sustained_window': GAZE_SUSTAINED_WINDOW,
            'gaze_sustained_min_hits': GAZE_SUSTAINED_MIN_HITS,
        },
        'snapshot_contract': {
            'required_fields': list(SNAPSHOT_REQUIRED_FIELDS),
            'optional_fields': list(SNAPSHOT_OPTIONAL_FIELDS),
        },
        'status_contract': {
            'ok': STATUS_OK,
            'warning': STATUS_WARNING,
            'terminated': STATUS_TERMINATED,
        },
        'actions': {
            'warning': 'Any new violation while total session violations stays below max_session_violations.',
            'termination': 'Violation count reaching max_session_violations, or explicit disqualification rule.',
        },
        'device_policy': {
            'mode': DEVICE_POLICY,
            'allowed': ['desktop', 'laptop'],
            'blocked': ['mobile', 'tablet'],
        },
    }


def proctoring_response(
    status,
    violation_count,
    *,
    violation=False,
    reason=None,
    context=None,
):
    """Build a consistent response payload for proctoring endpoints."""
    payload = {
        'status': status,
        'violation': violation,
        'violation_count': violation_count,
    }
    if reason:
        payload['reason'] = reason
    if context is not None:
        payload['context'] = context
    return payload
