import os
import django # type: ignore

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django # type: ignore
django.setup()

from django.contrib.auth import get_user_model # type: ignore
from consultations.models import Topic # type: ignore

from typing import List, Dict, Any

User = get_user_model()

CONSULTATION_CATEGORIES: List[Dict[str, Any]] = [
  {
    "id": "returns",
    "title": "Returns",
    "subtitle": "ITR, GSTR, and TDS filing services",
    "groups": [
      {
        "title": "ITR",
        "items": [
          { "id": "itr_salary", "name": "ITR Salary Filing", "description": "Salaried income filing with deductions and refund optimization.", "details": "Form 16 review, deduction checks, and accurate return preparation.", "price": 399 },
          { "id": "itr_individual_business", "name": "ITR Individual Business Filing", "description": "For proprietors/freelancers with business or professional income.", "details": "Profit computation support with compliant reporting and filing.", "price": 799 },
          { "id": "itr_llp", "name": "ITR LLP Filing", "description": "Return filing for LLPs with partner details and compliance support.", "details": "LLP income reporting with partner disclosures and filing workflow.", "price": 1099 },
          { "id": "itr_nri", "name": "ITR NRI Filing", "description": "For NRIs with India income and DTAA considerations.", "details": "Residential-status checks, DTAA inputs, and income disclosure support.", "price": 999 },
          { "id": "itr_partnership", "name": "ITR Partnership Filing", "description": "For partnership firms with partner interest/remuneration handling.", "details": "Partner remuneration/interest handling and ITR preparation support.", "price": 999 },
          { "id": "itr_company", "name": "ITR Company Filing", "description": "Structured company tax filing and documentation support.", "details": "Company tax computation, schedules mapping, and return filing support.", "price": 1299 },
          { "id": "itr_trust", "name": "ITR Trust Filing", "description": "ITR-7 support for trusts/NGOs with compliance guidance.", "details": "Trust income/exemption reporting with ITR-7 filing guidance.", "price": 1499 },
        ],
      },
      {
        "title": "GSTR",
        "items": [
          { "id": "gstr_1_3b_monthly", "name": "GSTR 1 and 3B (Monthly)", "description": "Monthly GST return filing for regular taxpayers.", "details": "Sales/purchase summary validation and monthly filing assistance.", "price": 699 },
          { "id": "gstr_1_3b_quarterly", "name": "GSTR 1 and 3B (Quarterly)", "description": "Quarterly filing support for eligible taxpayers.", "details": "Quarterly return preparation and compliant submission guidance.", "price": 599 },
          { "id": "gstr_cmp_4", "name": "GSTR CMP 4", "description": "Composition statement preparation and filing support.", "details": "Composition taxpayer statement support with basic compliance checks.", "price": 399 },
          { "id": "gstr_9", "name": "GSTR 9", "description": "Annual GST return filing with consolidated reporting.", "details": "Annual GST consolidation and filing support for eligible entities.", "price": 899 },
          { "id": "gstr_9c", "name": "GSTR 9C", "description": "Reconciliation statement support for annual compliance.", "details": "Turnover/tax reconciliation assistance for audit-linked compliance.", "price": 1099 },
          { "id": "gstr_4_annual", "name": "GSTR 4 (Annual return)", "description": "Annual return filing for composition taxpayers.", "details": "Annual composition return support with required statement inputs.", "price": 499 },
          { "id": "gstr_10_last", "name": "GSTR 10 (Last Return)", "description": "Final return support for GST cancellation cases.", "details": "Final return workflow support after GST cancellation events.", "price": 599 },
        ],
      },
      {
        "title": "TDS",
        "items": [
          { "id": "tds_monthly_payment", "name": "TDS Monthly Payment", "description": "Monthly TDS deposit guidance and tracking support.", "details": "Monthly challan/deposit process guidance with due-date tracking.", "price": 199 },
          { "id": "tds_quarterly_filing", "name": "TDS Quarterly Filing", "description": "Quarterly TDS statement filing with validation.", "details": "Quarterly statement filing support with basic mismatch validation.", "price": 399 },
          { "id": "tds_revised_quarterly_filing", "name": "TDS Revised Quarterly Filing", "description": "Correction filing support for revised TDS statements.", "details": "Correction statement support for notices, mismatches, or edits.", "price": 499 },
          { "id": "sale_of_property_26qb", "name": "Sale of Property (26QB)", "description": "Property TDS filing support for applicable transactions.", "details": "Buyer/seller property TDS compliance support using Form 26QB.", "price": 699 },
        ],
      },
    ],
  },
  {
    "id": "registrations",
    "title": "Registrations",
    "subtitle": "Business and compliance registrations",
    "groups": [
      {
        "title": "Registration Services",
        "items": [
          { "id": "pan_application", "name": "PAN Application", "description": "New PAN application or correction support.", "details": "Application support with document checklist and correction workflow.", "price": 199 },
          { "id": "tan_registration", "name": "TAN Registration", "description": "TAN setup for TDS compliance workflows.", "details": "Mandatory TAN setup for entities deducting tax at source.", "price": 399 },
          { "id": "aadhaar_validation", "name": "Aadhaar Validation", "description": "Aadhaar verification/linking support.", "details": "Identity validation support for tax profile consistency.", "price": 149 },
          { "id": "msme_registration", "name": "MSME Registration", "description": "Udyam registration support for eligible businesses.", "details": "Udyam onboarding support with activity mapping and submission.", "price": 399 },
          { "id": "iec", "name": "Import Export Code (IEC)", "description": "IEC setup support for import/export businesses.", "details": "Directorate filing support for cross-border trade enablement.", "price": 699 },
          { "id": "partnership_firm_registration", "name": "Partnership Firm Registration", "description": "Partnership deed and registration workflow support.", "details": "Deed drafting checkpoints and registration submission guidance.", "price": 1299 },
          { "id": "llp_registration", "name": "LLP Registration", "description": "LLP incorporation and base compliance support.", "details": "Name, incorporation, and initial compliance workflow support.", "price": 1499 },
          { "id": "pvt_ltd_registration", "name": "Private Limited Company Registration", "description": "Company incorporation setup and filing support.", "details": "Incorporation support including documentation and filing sequence.", "price": 1999 },
          { "id": "startup_india_registration", "name": "Startup India Registration", "description": "Startup recognition application support.", "details": "Recognition application support with eligibility/annexure guidance.", "price": 599 },
          { "id": "trust_formation", "name": "Trust Formation", "description": "Trust setup and deed-level guidance.", "details": "Trust deed structuring and registration process support.", "price": 1499 },
          { "id": "reg_12a", "name": "12A Registration", "description": "Tax exemption registration support under section 12A.", "details": "NGO tax-exemption application prep with supporting compliance docs.", "price": 1299 },
          { "id": "reg_80g", "name": "80G Registration", "description": "Registration enabling donor deduction eligibility.", "details": "80G filing support so donor contributions qualify for tax deductions.", "price": 1299 },
          { "id": "dsc", "name": "DSC (Digital Signature Certificate)", "description": "DSC issuance/renewal support for filings.", "details": "Class-based DSC issuance/renewal support for secure e-filings.", "price": 699 },
          { "id": "huf_pan", "name": "HUF PAN", "description": "PAN support for Hindu Undivided Families.", "details": "HUF PAN application support with karta/member document checks.", "price": 299 },
          { "id": "nri_pan", "name": "NRI PAN", "description": "PAN support for NRIs with compliant documentation.", "details": "NRI PAN support with overseas address/document compliance guidance.", "price": 399 },
          { "id": "foreign_entity_registration", "name": "Foreign Entity Registration", "description": "India registration support for foreign entities.", "details": "Entry-structure and registration support for foreign-owned entities.", "price": 1499 },
        ],
      },
    ],
  },
  {
    "id": "notices",
    "title": "Notices",
    "subtitle": "Notice response and appeal handling",
    "groups": [
      {
        "title": "Notice and Appeal Services",
        "items": [
          { "id": "income_tax_notice_response", "name": "Income Tax Notice Response", "description": "Drafting and submission support within notice timelines.", "details": "Notice analysis, draft response, and submission guidance support.", "price": 899 },
          { "id": "gst_notice_response", "name": "GST Notice Response", "description": "Reply preparation with reconciliation support.", "details": "Mismatch review, reconciliation inputs, and response drafting.", "price": 999 },
          { "id": "tds_notice_response", "name": "TDS Notice Response", "description": "Clarification and response filing support.", "details": "TDS mismatch clarification workflow with response preparation.", "price": 799 },
          { "id": "income_tax_appeal", "name": "Income Tax Appeal", "description": "Appeal filing support with grounds and documentation.", "details": "Grounds-of-appeal drafting support with filing checklist guidance.", "price": 1499 },
          { "id": "gst_appeal", "name": "GST Appeal", "description": "GST appeal drafting and process support.", "details": "Appeal filing assistance with supporting statement/document flow.", "price": 1499 },
          { "id": "tribunal_representation", "name": "Tribunal Representation", "description": "Advanced litigation support for tribunal-level cases.", "details": "Structured support for complex hearings and tribunal submissions.", "price": 2499 },
          { "id": "regular_assessment_handling", "name": "Regular Assessment Handling", "description": "End-to-end support for ongoing assessment workflows.", "details": "Assessment cycle handling with periodic query-response assistance.", "price": 1099 },
        ],
      },
    ],
  },
  {
    "id": "consultation",
    "title": "Consultation",
    "subtitle": "General advisory and strategy sessions",
    "groups": [
      {
        "title": "Consultation Topics",
        "items": [
          { "id": "tax_consultation", "name": "Tax Consultation", "description": "Discuss tax-saving and filing strategy with an expert.", "details": "1:1 advisory focused on filing approach, deductions, and planning.", "price": 299 },
          { "id": "compliance_advice", "name": "Compliance Advice", "description": "Understand obligations and create a practical compliance plan.", "details": "Actionable compliance roadmap based on your entity and activities.", "price": 299 },
          { "id": "business_structuring", "name": "Business Structuring", "description": "Get guidance on entity and tax structure for growth.", "details": "Entity/tax-structure discussion for scalability and compliance fit.", "price": 499 },
        ],
      },
    ],
  },
]

def run():
    consultants = list(User.objects.filter(role='CONSULTANT'))
    created_count = 0
    updated_count = 0
    
    # We ignore previous ones if they don't match exactly.
    for category in CONSULTATION_CATEGORIES:
        for group in category["groups"]:
            for item in group["items"]: # type: ignore
                # Check if it exists by name to avoid duplicates
                topic, created = Topic.objects.get_or_create( # type: ignore
                    name=item["name"], # type: ignore
                    defaults={"description": item["description"]} # type: ignore
                )
                
                if not created:
                    topic.description = item["description"] # type: ignore
                    topic.save()
                    updated_count += 1
                else:
                    created_count += 1
                    
                # Ensure all consultants are linked to this topic
                for c in consultants:
                    topic.consultants.add(c)

    print(f"Migration complete: Created {created_count} topics, Updated {updated_count} topics. All consultants assigned.")

if __name__ == '__main__':
    run()
