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
