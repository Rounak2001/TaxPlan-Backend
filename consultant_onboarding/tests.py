import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from . import scrutiny as scrutiny_module
from . import video_questions as video_questions_module
from .authentication import generate_applicant_token
from .credential_service import check_and_auto_generate_credentials, get_auto_credential_blocker
from .models import ConsultantApplication, ConsultantDocument, IdentityDocument, UserSession, VideoResponse
from .views.admin_panel import delete_consultant
from .views.auth import accept_declaration
from .views.test_engine import TestTypeViewSet, UserSessionViewSet
from .utils.name_matching import first_last_name, first_last_names_match, get_latest_verified_identity_name
from consultants.models import ClientServiceRequest, ConsultantServiceExpertise, ConsultantServiceProfile, Service, ServiceCategory
from consultations.models import Topic
from core_auth.models import ClientProfile, User
from document_vault.models import Document


class NameMatchingTests(TestCase):
    def test_first_last_name_ignores_middle_names(self):
        self.assertEqual(first_last_name("John Michael Doe"), "john doe")
        self.assertTrue(first_last_names_match("John Michael Doe", "John Doe"))
        self.assertTrue(first_last_names_match("  JOHN   DOE ", "John A. Doe"))
        self.assertFalse(first_last_names_match("John Doe", "Jane Doe"))

    def test_get_latest_verified_identity_name_reads_latest_verified_document(self):
        application = ConsultantApplication.objects.create(email="identity@example.com")
        IdentityDocument.objects.create(
            application=application,
            file_path="old-id.png",
            verification_status="Invalid",
            gemini_raw_response=json.dumps({"extracted_name": "Wrong Name"}),
        )
        IdentityDocument.objects.create(
            application=application,
            file_path="verified-id.png",
            verification_status="Verified",
            gemini_raw_response=json.dumps({"extracted_name": "John Michael Doe"}),
        )

        self.assertEqual(get_latest_verified_identity_name(application), "John Michael Doe")


class AutoCredentialGenerationTests(TestCase):
    def make_eligible_application(self, **overrides):
        defaults = {
            "email": "consultant@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "+919999999999",
            "is_phone_verified": True,
            "dob": date(1995, 1, 10),
            "address_line1": "123 Market Street",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400001",
            "has_accepted_declaration": True,
            "is_verified": True,
        }
        defaults.update(overrides)
        application = ConsultantApplication.objects.create(**defaults)

        IdentityDocument.objects.create(
            application=application,
            file_path="identity/id.png",
            verification_status="Verified",
            gemini_raw_response=json.dumps({"extracted_name": "John Michael Doe"}),
        )
        ConsultantDocument.objects.create(
            application=application,
            qualification_type="Education",
            document_type="bachelors_degree",
            file_path="docs/bachelors.pdf",
            verification_status="Verified",
            gemini_raw_response=json.dumps(
                {
                    "extracted_name": "John Doe",
                    "degree_field": "Bachelor of Commerce",
                    "is_target_field": True,
                }
            ),
        )
        ConsultantDocument.objects.create(
            application=application,
            qualification_type="Education",
            document_type="certificate",
            file_path="docs/certificate.pdf",
            verification_status="Verified",
            gemini_raw_response=json.dumps({"extracted_name": "John Doe"}),
        )

        session = UserSession.objects.create(
            application=application,
            question_set=[{"id": 1}, {"id": 2}],
            video_question_set=[{"id": "q1"}, {"id": "q2"}, {"id": "q3"}],
            score=35,
            status="completed",
            end_time=timezone.now(),
        )
        for question_id in ("q1", "q2", "q3"):
            VideoResponse.objects.create(
                session=session,
                question_identifier=question_id,
                video_file=f"videos/{question_id}.webm",
                ai_status="completed",
                ai_score=5,
            )

        return application

    @patch("consultant_onboarding.views.admin_panel._generate_and_send_credentials")
    def test_auto_generation_requires_face_verification(self, mocked_generator):
        application = self.make_eligible_application(is_verified=False)

        success, reason = check_and_auto_generate_credentials(application)

        self.assertFalse(success)
        self.assertEqual(reason, "Face verification incomplete")
        mocked_generator.assert_not_called()

    @patch("consultant_onboarding.views.admin_panel._generate_and_send_credentials")
    def test_auto_generation_requires_verified_bachelors_document(self, mocked_generator):
        application = self.make_eligible_application()
        ConsultantDocument.objects.filter(application=application, document_type="bachelors_degree").delete()

        blocker = get_auto_credential_blocker(application)

        self.assertEqual(blocker, "No bachelor's degree document")
        mocked_generator.assert_not_called()

    @patch("consultant_onboarding.views.admin_panel._generate_and_send_credentials")
    def test_auto_generation_succeeds_when_all_requirements_are_met(self, mocked_generator):
        application = self.make_eligible_application()
        mocked_generator.return_value = (True, {"username": "taxplanadvisor_john_1234"})

        success, reason = check_and_auto_generate_credentials(application)

        self.assertTrue(success)
        self.assertEqual(reason, "Auto-generated credentials successfully")
        mocked_generator.assert_called_once_with(application)


class DeleteConsultantEndpointTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.application = ConsultantApplication.objects.create(
            email="delete-consultant@example.com",
            first_name="Delete",
            last_name="Consultant",
        )
        self.consultant_user = User.objects.create_user(
            username="delete_consultant_user",
            email=self.application.email,
            password="password",
            role=User.CONSULTANT,
        )
        self.consultant_profile = ConsultantServiceProfile.objects.create(
            user=self.consultant_user,
            qualification="CA",
        )
        self.category = ServiceCategory.objects.create(
            name="Delete Category",
            description="Delete flow coverage",
            is_active=True,
        )
        self.service = Service.objects.create(
            category=self.category,
            title="Delete Flow Service",
            tat="2 days",
            documents_required="PAN, Aadhaar",
        )
        self.precise_topic = Topic.objects.create(
            name="Delete Flow Topic",
            category=self.category,
            service=self.service,
        )
        self.broad_topic = Topic.objects.create(
            name="Delete Flow Broad Topic",
            category=self.category,
        )
        ConsultantServiceExpertise.objects.create(
            consultant=self.consultant_profile,
            service=self.service,
        )
        ClientProfile.objects.create(user=self.consultant_user)
        ClientServiceRequest.objects.create(
            client=self.consultant_user,
            service=self.service,
            assigned_consultant=self.consultant_profile,
            status="assigned",
        )
        Document.objects.create(
            client=self.consultant_user,
            title="Delete Flow Pending Document",
            description=f"Required for {self.service.title}",
            status="PENDING",
        )

    def test_delete_consultant_endpoint_returns_json_and_removes_live_consultant(self):
        request = self.factory.delete(f"/api/admin-panel/consultants/{self.application.id}/delete/")

        response = delete_consultant(request, self.application.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], self.application.email)
        self.assertFalse(
            ConsultantApplication.objects.filter(id=self.application.id).exists()
        )
        self.assertFalse(
            User.objects.filter(id=self.consultant_user.id).exists()
        )
        self.assertEqual(ClientServiceRequest.objects.count(), 0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(self.precise_topic.consultants.count(), 0)
        self.assertEqual(self.broad_topic.consultants.count(), 0)


class AssessmentDomainSelectionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.application = ConsultantApplication.objects.create(
            email="assessment-categories@example.com",
            first_name="Assess",
            last_name="Ment",
        )
        self.token = generate_applicant_token(self.application)

    def make_request(self, method, path, data=None):
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {self.token}",
            "HTTP_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
        }
        request_factory = getattr(self.factory, method)
        return request_factory(path, data=data, format="json", **headers)

    def test_test_type_list_includes_registrations_in_expected_order(self):
        view = TestTypeViewSet.as_view({"get": "list"})

        response = view(self.make_request("get", "/assessment/test-types/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["slug"] for item in response.data],
            ["itr", "gstr", "scrutiny", "registrations"],
        )

    def test_registrations_only_session_is_rejected(self):
        view = UserSessionViewSet.as_view({"post": "create"})

        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {"selected_tests": ["registrations"]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Registrations can only be selected", response.data["error"])

    def test_registrations_with_itr_creates_balanced_session(self):
        view = UserSessionViewSet.as_view({"post": "create"})

        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {"selected_tests": ["itr", "registrations"]},
            )
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["selected_domains"], ["itr", "registrations"])
        self.assertEqual(len(response.data["questions"]), 50)
        self.assertEqual(len(response.data["video_questions"]), 5)

        domain_counts = {}
        for question in response.data["questions"]:
            domain_counts[question["domain"]] = domain_counts.get(question["domain"], 0) + 1

        self.assertEqual(domain_counts, {"itr": 25, "registrations": 25})

    def test_scrutiny_income_tax_selection_scopes_mcqs_and_videos(self):
        view = UserSessionViewSet.as_view({"post": "create"})

        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {
                    "selected_tests": ["scrutiny"],
                    "selected_test_details": [
                        {
                            "slug": "scrutiny",
                            "selected_service_ids": [
                                "itr_appeal",
                                "tds_regular_assessment",
                            ],
                        }
                    ],
                },
            )
        )

        self.assertEqual(response.status_code, 201)
        allowed_scopes = {scrutiny_module.SCRUTINY_SCOPE_INCOME_TAX_TDS}
        for question in response.data["questions"]:
            question_scope = scrutiny_module.classify_scrutiny_question(
                {
                    "id": question.get("source_id"),
                    "question": question.get("question"),
                    "options": question.get("options") or {},
                }
            )
            self.assertIn(question_scope, allowed_scopes)

        allowed_videos = set(
            video_questions_module.get_scoped_scrutiny_video_questions(
                scrutiny_module.SCRUTINY_SCOPE_INCOME_TAX_TDS
            )
        )
        for video_question in response.data["video_questions"][1:]:
            self.assertIn(video_question["text"], allowed_videos)

    def test_scrutiny_gstr_selection_scopes_mcqs_and_videos(self):
        view = UserSessionViewSet.as_view({"post": "create"})

        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {
                    "selected_tests": ["scrutiny"],
                    "selected_test_details": [
                        {
                            "slug": "scrutiny",
                            "selected_service_ids": [
                                "gst_appeal",
                                "gst_regular_assessment",
                            ],
                        }
                    ],
                },
            )
        )

        self.assertEqual(response.status_code, 201)
        allowed_scopes = {scrutiny_module.SCRUTINY_SCOPE_GSTR}
        for question in response.data["questions"]:
            question_scope = scrutiny_module.classify_scrutiny_question(
                {
                    "id": question.get("source_id"),
                    "question": question.get("question"),
                    "options": question.get("options") or {},
                }
            )
            self.assertIn(question_scope, allowed_scopes)

        allowed_videos = set(
            video_questions_module.get_scoped_scrutiny_video_questions(
                scrutiny_module.SCRUTINY_SCOPE_GSTR
            )
        )
        for video_question in response.data["video_questions"][1:]:
            self.assertIn(video_question["text"], allowed_videos)

    def test_proctoring_policy_uses_applicant_auth_even_with_invalid_main_app_cookie(self):
        view = UserSessionViewSet.as_view({"get": "proctoring_policy"})
        request = self.make_request("get", "/assessment/sessions/proctoring_policy/")
        request.COOKIES["access_token"] = "invalid-main-app-cookie"

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("thresholds", response.data)

    def test_accept_declaration_uses_applicant_auth_even_with_invalid_main_app_cookie(self):
        request = self.make_request("post", "/auth/accept-declaration/")
        request.COOKIES["access_token"] = "invalid-main-app-cookie"

        response = accept_declaration(request)

        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertTrue(self.application.has_accepted_declaration)
