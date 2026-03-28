import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from . import scrutiny as scrutiny_module
from . import video_questions as video_questions_module
from .assessment_outcome import get_application_assessment_outcome
from .authentication import generate_applicant_token
from .credential_service import check_and_auto_generate_credentials, get_auto_credential_blocker
from .expertise_sync import sync_passed_sessions_to_consultant
from .models import (
    ConsultantApplication,
    ConsultantCredential,
    ConsultantDocument,
    IdentityDocument,
    ProctoringSnapshot,
    UserSession,
    VideoResponse,
    Violation,
)
from .views.admin_panel import (
    _ensure_live_consultant_user,
    _generate_and_send_credentials,
    _restore_flagged_assessment_session,
    delete_consultant,
    dev_bootstrap_consultant,
)
from .views.auth import accept_declaration
from .views.test_engine import TestTypeViewSet, UserSessionViewSet
from .utils.name_matching import (
    first_name_present,
    first_last_name,
    first_last_names_match,
    get_latest_verified_identity_name,
)
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

    def test_first_name_present_matches_only_first_name_token(self):
        self.assertTrue(first_name_present("John Michael Doe", "Doe John Fathername"))
        self.assertTrue(first_name_present("  JOHN   DOE ", "surname DOE and JOHN details"))
        self.assertTrue(first_name_present("John Doe", "John Smith Fathername"))
        self.assertFalse(first_name_present("John Doe", "Doe Smith Fathername"))
        self.assertFalse(first_name_present("John Doe", "Jane Smith Fathername"))

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

    @patch("consultant_onboarding.views.admin_panel.send_mail")
    def test_generate_credentials_creates_live_consultant_without_google_id_on_user(self, mocked_send_mail):
        application = self.make_eligible_application(google_id="google-sub-123")
        mocked_send_mail.return_value = 1

        success, result = _generate_and_send_credentials(application)

        self.assertTrue(success)
        consultant_user = User.objects.get(email=application.email)
        self.assertEqual(consultant_user.role, User.CONSULTANT)
        self.assertFalse(hasattr(consultant_user, "google_id"))
        self.assertTrue(ConsultantServiceProfile.objects.filter(user=consultant_user).exists())
        self.assertEqual(
            ConsultantCredential.objects.get(application=application).username,
            result["username"],
        )

    def test_ensure_live_user_keeps_existing_application_username_without_suffix(self):
        application = self.make_eligible_application(email="same-app-username@example.com")
        ConsultantCredential.objects.create(
            application=application,
            username="taxplanadvisor_john_1536",
            password="Password@123",
        )

        user, _profile = _ensure_live_consultant_user(
            application,
            "taxplanadvisor_john_1536",
            password="Password@123",
        )

        self.assertEqual(user.username, "taxplanadvisor_john_1536")

    def test_generate_credentials_rejects_phone_conflict_without_stale_credential_row(self):
        application = self.make_eligible_application(phone_number="+919999999999")
        User.objects.create_user(
            username="existing_client_phone",
            email="existing-client@example.com",
            password="password",
            role=User.CLIENT,
            phone_number="+919999999999",
        )

        success, result = _generate_and_send_credentials(application)

        self.assertFalse(success)
        self.assertIn("phone number", str(result))
        self.assertFalse(ConsultantCredential.objects.filter(application=application).exists())
        self.assertFalse(User.objects.filter(email=application.email, role=User.CONSULTANT).exists())

    @override_settings(DEBUG=True)
    def test_dev_bootstrap_consultant_creates_predictable_debug_account(self):
        request = APIRequestFactory().post(
            "/api/admin-panel/dev/bootstrap-consultant/",
            {"email": "dev-bootstrap@example.com"},
            format="json",
        )

        response = dev_bootstrap_consultant(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["username"].startswith("dev_consultant"))
        self.assertEqual(response.data["passed_categories"], ["itr"])
        self.assertEqual(response.data["auto_unlocked_categories"], ["registrations"])

        application = ConsultantApplication.objects.get(email="dev-bootstrap@example.com")
        consultant_user = User.objects.get(email=application.email)
        self.assertEqual(consultant_user.role, User.CONSULTANT)
        self.assertTrue(
            UserSession.objects.filter(
                application=application,
                status="completed",
                score__gte=35,
            ).exists()
        )
        self.assertTrue(ConsultantCredential.objects.filter(application=application).exists())


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


class RestoreFlaggedAssessmentSessionTests(TestCase):
    def setUp(self):
        self.application = ConsultantApplication.objects.create(
            email="restore-session@example.com",
            first_name="Restore",
            last_name="Candidate",
        )
        self.session = UserSession.objects.create(
            application=self.application,
            selected_domains=["itr"],
            question_set=[{"id": "itr_1"}, {"id": "itr_2"}, {"id": "itr_3"}],
            video_question_set=[{"id": "v_intro"}, {"id": "v_1"}],
            mcq_answers={"itr_1": "A", "itr_2": "B"},
            score=12,
            status="flagged",
            violation_count=4,
            violation_counters={"face": 2, "voice": 2},
            end_time=timezone.now(),
        )

    def test_restore_reopens_session_and_resets_violation_state(self):
        Violation.objects.create(session=self.session, violation_type="face")
        Violation.objects.create(session=self.session, violation_type="voice")

        ProctoringSnapshot.objects.create(
            session=self.session,
            snapshot_id="snap-1",
            image_url="proctoring/fake/snap-1.jpg",
            is_violation=True,
            violation_reason="Multiple faces detected",
            face_count=2,
            match_score=0.0,
        )

        before_outcome = get_application_assessment_outcome(self.application)
        self.assertTrue(before_outcome["disqualified"])
        self.assertEqual(before_outcome["status"], "flagged")

        restored, payload = _restore_flagged_assessment_session(
            self.application,
            session_id=self.session.id,
        )

        self.assertTrue(restored)
        self.assertEqual(payload["session_id"], self.session.id)
        self.assertEqual(payload["answered_mcq_count"], 2)
        self.assertEqual(payload["total_mcq_count"], 3)

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "ongoing")
        self.assertIsNone(self.session.end_time)
        self.assertEqual(self.session.violation_count, 0)
        self.assertEqual(self.session.violation_counters, {})
        self.assertEqual(self.session.mcq_answers, {"itr_1": "A", "itr_2": "B"})

        self.assertEqual(Violation.objects.filter(session=self.session).count(), 0)
        self.assertEqual(ProctoringSnapshot.objects.filter(session=self.session).count(), 0)

        after_outcome = get_application_assessment_outcome(self.application)
        self.assertFalse(after_outcome["disqualified"])

    def test_restore_fails_when_no_flagged_session_exists(self):
        self.session.status = "completed"
        self.session.save(update_fields=["status"])

        restored, message = _restore_flagged_assessment_session(
            self.application,
            session_id=self.session.id,
        )

        self.assertFalse(restored)
        self.assertIn("No flagged assessment session found", message)


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
        session = UserSession.objects.get(id=response.data["id"])
        self.assertEqual(
            session.selected_test_details,
            {},
        )

    def test_completed_itr_session_auto_unlocks_registrations_and_other_main_categories_remain_available(self):
        session = UserSession.objects.create(
            application=self.application,
            selected_domains=["itr"],
            selected_test_details={
                "itr": {
                    "selected_service_ids": ["itr_salary_filing", "itr_general_consultation"],
                }
            },
            question_set=[{"id": 1}],
            video_question_set=[],
            score=35,
            status="completed",
            end_time=timezone.now(),
        )

        outcome = get_application_assessment_outcome(self.application)

        self.assertEqual(session.selected_test_details["itr"]["selected_service_ids"][0], "itr_salary_filing")
        self.assertTrue(outcome["has_passed_assessment"])
        self.assertEqual(outcome["unlocked_categories"], ["itr", "registrations"])
        self.assertEqual(outcome["available_assessment_categories"], ["gstr", "scrutiny"])
        self.assertTrue(outcome["can_start_assessment"])

    def test_additional_unlock_assessment_blocks_already_unlocked_categories(self):
        UserSession.objects.create(
            application=self.application,
            selected_domains=["itr"],
            selected_test_details={
                "itr": {"selected_service_ids": ["itr_salary_filing"]},
            },
            question_set=[{"id": 1}],
            video_question_set=[],
            score=35,
            status="completed",
            end_time=timezone.now(),
        )

        view = UserSessionViewSet.as_view({"post": "create"})
        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {"selected_tests": ["itr"]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already unlocked", response.data["error"])

    def test_additional_unlock_assessment_allows_remaining_main_category(self):
        UserSession.objects.create(
            application=self.application,
            selected_domains=["itr"],
            selected_test_details={
                "itr": {"selected_service_ids": ["itr_salary_filing"]},
            },
            question_set=[{"id": 1}],
            video_question_set=[],
            score=35,
            status="completed",
            end_time=timezone.now(),
        )

        view = UserSessionViewSet.as_view({"post": "create"})
        response = view(
            self.make_request(
                "post",
                "/assessment/sessions/",
                {
                    "selected_tests": ["gstr"],
                    "selected_test_details": [
                        {
                            "slug": "gstr",
                            "selected_service_ids": ["gstr_monthly"],
                        }
                    ],
                },
            )
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["selected_domains"], ["gstr"])

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


class ExpertiseSyncTests(TestCase):
    def test_passed_session_seeds_consultant_expertise_once(self):
        application = ConsultantApplication.objects.create(
            email="seeded-consultant@example.com",
            first_name="Seeded",
            last_name="Consultant",
        )
        session = UserSession.objects.create(
            application=application,
            selected_domains=["itr"],
            selected_test_details={
                "itr": {
                    "selected_service_ids": ["itr_salary_filing", "itr_general_consultation"],
                }
            },
            question_set=[{"id": 1}],
            video_question_set=[],
            score=35,
            status="completed",
            end_time=timezone.now(),
        )

        consultant_user = User.objects.create_user(
            username="seeded_consultant",
            email=application.email,
            password="password",
            role=User.CONSULTANT,
        )
        consultant_profile = ConsultantServiceProfile.objects.create(user=consultant_user, qualification="CA")
        returns_category = ServiceCategory.objects.create(name="Returns", description="Returns", is_active=True)
        consultation_category = ServiceCategory.objects.create(name="Consultation", description="Consultation", is_active=True)
        Service.objects.create(
            category=returns_category,
            title="ITR Salary Filing",
            tat="2 days",
            documents_required="PAN",
        )
        Service.objects.create(
            category=consultation_category,
            title="Tax Consultation",
            tat="Session based",
            documents_required="Notes",
        )

        result = sync_passed_sessions_to_consultant(application, consultant_profile=consultant_profile)

        self.assertTrue(result["profile_found"])
        self.assertEqual(result["seeded_sessions"], 1)
        self.assertEqual(
            sorted(
                ConsultantServiceExpertise.objects.filter(consultant=consultant_profile)
                .values_list("service__title", flat=True)
            ),
            ["ITR Salary Filing", "Tax Consultation"],
        )
        session.refresh_from_db()
        self.assertTrue(session.expertise_seeded)
