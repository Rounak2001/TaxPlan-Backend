from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from consultant_onboarding.models import ConsultantApplication
from core_auth.models import MagicLinkToken, User


class EmailChangeFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='consultant_email_change',
            email='consultant-email@example.com',
            password='password',
            role=User.CONSULTANT,
            phone_number='+919876543210',
            is_phone_verified=True,
        )
        self.application = ConsultantApplication.objects.create(
            email=self.user.email,
            first_name='Priya',
            last_name='Sharma',
            phone_number=self.user.phone_number,
            is_phone_verified=True,
        )
        self.client.force_authenticate(user=self.user)

    @patch('core_auth.views._send_base64_html_email')
    def test_request_email_change_uses_backend_confirm_link(self, mocked_send):
        response = self.client.post(
            reverse('email-change-request'),
            {'email': 'new-consultant@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        token = MagicLinkToken.objects.get(
            user=self.user,
            purpose=MagicLinkToken.EMAIL_CHANGE,
            used=False,
        )
        plain_body = mocked_send.call_args.kwargs['plain_body']
        self.assertIn(f'/api/auth/email-change/confirm/{token.token}/', plain_body)
        self.assertNotIn('/auth/email-change/verify/', plain_body)

    def test_confirm_email_change_updates_user_and_application(self):
        token = MagicLinkToken.objects.create(
            user=self.user,
            token='confirm-email-token',
            purpose=MagicLinkToken.EMAIL_CHANGE,
            pending_email='confirmed-consultant@example.com',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        response = self.client.get(reverse('email-change-confirm', args=[token.token]))

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.application.refresh_from_db()
        token.refresh_from_db()

        self.assertEqual(self.user.email, 'confirmed-consultant@example.com')
        self.assertEqual(self.application.email, 'confirmed-consultant@example.com')
        self.assertTrue(token.used)
        self.assertIn(b'Email verified', response.content)
        self.assertIn(b'Close this tab now', response.content)
        self.assertNotIn(b'http-equiv="refresh"', response.content)

    def test_post_verify_email_change_updates_user_and_application(self):
        token = MagicLinkToken.objects.create(
            user=self.user,
            token='post-verify-email-token',
            purpose=MagicLinkToken.EMAIL_CHANGE,
            pending_email='verified-via-post@example.com',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        response = self.client.post(
            reverse('email-change-verify'),
            {'token': token.token},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.application.refresh_from_db()
        token.refresh_from_db()

        self.assertEqual(self.user.email, 'verified-via-post@example.com')
        self.assertEqual(self.application.email, 'verified-via-post@example.com')
        self.assertTrue(token.used)
