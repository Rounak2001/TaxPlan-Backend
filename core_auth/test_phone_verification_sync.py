from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from consultant_onboarding.models import ConsultantApplication
from core_auth.models import User


class ConsultantPhoneVerificationSyncTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='consultant_phone_sync',
            email='consultant-phone@example.com',
            password='password',
            role=User.CONSULTANT,
            phone_number='+919876543210',
            is_phone_verified=False,
        )
        self.application = ConsultantApplication.objects.create(
            email=self.user.email,
            first_name='Asha',
            last_name='Mehta',
            phone_number='+919876543210',
            is_phone_verified=False,
        )
        self.client.force_authenticate(user=self.user)

    @patch('core_auth.views.verify_otp_service', return_value=(True, 'Phone verified.', 2))
    def test_verify_otp_updates_user_and_application_phone(self, _mock_verify):
        response = self.client.post(
            reverse('verify-otp'),
            {
                'phone_number': '9123456789',
                'otp': '123456',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.application.refresh_from_db()

        self.assertEqual(self.user.phone_number, '+919123456789')
        self.assertTrue(self.user.is_phone_verified)
        self.assertEqual(self.application.phone_number, '+919123456789')
        self.assertTrue(self.application.is_phone_verified)
