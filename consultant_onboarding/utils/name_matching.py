import json
import re
from difflib import SequenceMatcher

from ..models import IdentityDocument


def normalize_name(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def first_last_name(value):
    tokens = normalize_name(value).split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0]} {tokens[-1]}"


def first_last_similarity_pct(left, right):
    left_norm = first_last_name(left)
    right_norm = first_last_name(right)
    if not left_norm or not right_norm:
        return 0
    return int(round(SequenceMatcher(None, left_norm, right_norm).ratio() * 100))


def first_last_names_match(left, right):
    left_norm = first_last_name(left)
    right_norm = first_last_name(right)
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm


def first_name_present(left, right):
    """
    Check whether the first-name token from `left` exists anywhere in `right`.
    """
    left_tokens = normalize_name(left).split()
    candidate_parts = set(normalize_name(right).split())
    if not left_tokens or not candidate_parts:
        return False
    return left_tokens[0] in candidate_parts


def first_last_name_parts_present(left, right):
    """
    Backward-compatible alias for first-name-only matching used by bachelor's validation.
    """
    return first_name_present(left, right)


def _load_json_object(raw_value):
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        loaded = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def get_latest_verified_identity_name(application):
    identity_doc = (
        IdentityDocument.objects
        .filter(application=application, verification_status__iexact="Verified")
        .order_by("-uploaded_at", "-id")
        .first()
    )
    if not identity_doc:
        return ""

    parsed = _load_json_object(identity_doc.gemini_raw_response)
    return str(parsed.get("extracted_name") or "").strip()
