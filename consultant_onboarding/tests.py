import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .credential_service import check_and_auto_generate_credentials, get_auto_credential_blocker
from .models import ConsultantApplication, ConsultantDocument, IdentityDocument, UserSession, VideoResponse
from .utils.name_matching import first_last_name, first_last_names_match, get_latest_verified_identity_name


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
