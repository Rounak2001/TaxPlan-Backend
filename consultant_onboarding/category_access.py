from __future__ import annotations

from typing import Iterable


ASSESSMENT_CATEGORY_ORDER = ("itr", "gstr", "scrutiny", "registrations")
MAIN_ASSESSMENT_CATEGORIES = {"itr", "gstr", "scrutiny"}
REGISTRATIONS_CATEGORY = "registrations"


# Maps onboarding service identifiers to the live consultant service titles.
# Most entries are a direct label match; consultation titles use the closest
# live catalog equivalents available in the main platform.
ONBOARDING_SERVICE_ID_TO_LIVE_TITLE = {
    "itr_salary_filing": "ITR Salary Filing",
    "itr_individual_business_filing": "ITR Individual Business Filing",
    "itr_llp_filing": "ITR LLP Filing",
    "itr_nri_filing": "ITR NRI Filing",
    "itr_partnership_filing": "ITR Partnership Filing",
    "itr_company_filing": "ITR Company Filing",
    "itr_trust_filing": "ITR Trust Filing",
    "tds_monthly_payment": "TDS Monthly Payment",
    "tds_quarterly_filing": "TDS Quarterly Filing",
    "tds_revised_quarterly_filing": "TDS Revised Quarterly Filing",
    "tds_sale_of_property_26qb": "Sale of Property (26QB)",
    "itr_general_consultation": "Tax Consultation",
    "gstr_monthly": "GSTR-1 & GSTR-3B (Monthly)",
    "gstr_quarterly": "GSTR-1 & GSTR-3B (Quarterly)",
    "gstr_cmp_08": "GSTR CMP-08",
    "gstr_9": "GSTR-9",
    "gstr_9c": "GSTR-9C",
    "gstr_4": "GSTR-4 (Annual Return)",
    "gstr_10": "GSTR-10 (Final Return)",
    "gstr_general_consultation": "Compliance Advice",
    "itr_appeal": "ITR Appeal",
    "itr_regular_assessment": "ITR Regular Assessment",
    "itr_tribunal": "ITR Tribunal",
    "tds_appeal": "TDS Appeal",
    "tds_regular_assessment": "TDS Regular Assessment",
    "tds_tribunal": "TDS Tribunal",
    "gst_appeal": "GST Appeal",
    "gst_regular_assessment": "GST Regular Assessment",
    "gst_tribunal": "GST Tribunal",
    "pan_application": "PAN Application",
    "tan_registration": "TAN Registration",
    "aadhaar_validation": "Aadhaar Validation",
    "msme_registration": "MSME Registration",
    "iec": "Import Export Code (IEC)",
    "partnership_firm_registration": "Partnership Firm Registration",
    "llp_registration": "LLP Registration",
    "pvt_ltd_registration": "Private Limited Company Registration",
    "startup_india_registration": "Startup India Registration",
    "trust_formation": "Trust Formation",
    "reg_12a": "12A Registration",
    "reg_80g": "80G Registration",
    "dsc": "DSC (Digital Signature Certificate)",
    "huf_pan": "HUF PAN",
    "nri_pan": "NRI PAN",
    "foreign_entity_registration": "Foreign Entity Registration",
}


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def normalize_category_slugs(values: Iterable[str]) -> list[str]:
    return [slug for slug in _ordered_unique(values) if slug in ASSESSMENT_CATEGORY_ORDER]


def apply_registration_auto_unlock(category_slugs: Iterable[str]) -> list[str]:
    normalized = normalize_category_slugs(category_slugs)
    unlocked = set(normalized)
    if unlocked.intersection(MAIN_ASSESSMENT_CATEGORIES):
        unlocked.add(REGISTRATIONS_CATEGORY)
    return [slug for slug in ASSESSMENT_CATEGORY_ORDER if slug in unlocked]


def get_available_assessment_categories(unlocked_categories: Iterable[str]) -> list[str]:
    unlocked = set(normalize_category_slugs(unlocked_categories))
    return [slug for slug in ASSESSMENT_CATEGORY_ORDER if slug in MAIN_ASSESSMENT_CATEGORIES and slug not in unlocked]


def get_live_titles_for_onboarding_service_ids(service_ids: Iterable[str]) -> list[str]:
    titles = []
    seen = set()
    for service_id in service_ids or []:
        title = ONBOARDING_SERVICE_ID_TO_LIVE_TITLE.get(str(service_id or "").strip())
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def extract_selected_service_ids(selection_details) -> list[str]:
    if not isinstance(selection_details, dict):
        return []

    service_ids = []
    seen = set()
    for detail in selection_details.values():
        if not isinstance(detail, dict):
            continue
        for service_id in detail.get("selected_service_ids") or []:
            normalized = str(service_id or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                service_ids.append(normalized)
    return service_ids


def get_unlock_category_slugs_for_service(service) -> list[str]:
    category_name = str(getattr(getattr(service, "category", None), "name", "") or "").strip().lower()
    title = str(getattr(service, "title", "") or "").strip()

    if category_name == "registrations":
        return [REGISTRATIONS_CATEGORY]

    if category_name == "notices":
        return ["scrutiny"]

    if category_name == "returns":
        if title.startswith("GSTR") or title.startswith("GST"):
            return ["gstr"]
        return ["itr"]

    if category_name == "consultation":
        if title == "Tax Consultation":
            return ["itr"]
        if title == "Compliance Advice":
            return ["gstr"]
        if title == "Business Structuring":
            return [REGISTRATIONS_CATEGORY]

    return []


def is_service_unlocked(service, unlocked_categories: Iterable[str]) -> bool:
    required_categories = get_unlock_category_slugs_for_service(service)
    if not required_categories:
        return True
    unlocked = set(normalize_category_slugs(unlocked_categories))
    return any(category in unlocked for category in required_categories)
