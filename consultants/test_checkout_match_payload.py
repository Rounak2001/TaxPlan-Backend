from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from consultants.models import (
    ConsultantServiceExpertise,
    ConsultantServiceProfile,
    Service,
    ServiceCategory,
)


User = get_user_model()


class CheckoutMatchedConsultantPayloadTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.client_user = User.objects.create_user(
            username="checkout_client",
            email="checkout-client@example.com",
            password="password",
            role=User.CLIENT,
        )

        self.consultant_user = User.objects.create_user(
            username="payload_consultant",
            email="payload-consultant@example.com",
            password="password",
            role=User.CONSULTANT,
            first_name="Kavya",
            last_name="Rao",
        )
        self.consultant_profile = ConsultantServiceProfile.objects.create(
            user=self.consultant_user,
            qualification="CA",
            experience_years=9,
            bio="Specializes in direct tax compliance and advisory.",
            average_rating=Decimal("4.60"),
            total_reviews=18,
            is_active=True,
            max_concurrent_clients=10,
        )

        self.category = ServiceCategory.objects.create(
            name="Returns Payload",
            description="Payload checks",
            is_active=True,
        )
        self.service = Service.objects.create(
            category=self.category,
            title="Payload Service",
            tat="2 days",
            documents_required="PAN",
            is_active=True,
        )
        ConsultantServiceExpertise.objects.create(
            consultant=self.consultant_profile,
            service=self.service,
        )

        self.client.force_authenticate(user=self.client_user)

    def test_match_cart_payload_includes_profile_fields_required_by_checkout(self):
        response = self.client.post(
            "/api/consultants/services/match-cart/",
            {"titles": ["Payload Service"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        consultants = response.data.get("consultants", [])
        self.assertEqual(len(consultants), 1)

        payload = consultants[0]
        self.assertIn("experience_years", payload)
        self.assertIn("qualification", payload)
        self.assertIn("bio", payload)
        self.assertIn("average_rating", payload)

        self.assertEqual(payload["experience_years"], 9)
        self.assertEqual(payload["qualification"], "CA")
        self.assertEqual(payload["bio"], "Specializes in direct tax compliance and advisory.")
        self.assertEqual(payload["average_rating"], 4.6)

    def test_available_consultants_payload_includes_profile_fields_required_by_checkout(self):
        response = self.client.get(
            f"/api/consultants/services/{self.service.id}/available-consultants/",
        )

        self.assertEqual(response.status_code, 200)
        consultants = response.data.get("consultants", [])
        self.assertEqual(len(consultants), 1)

        payload = consultants[0]
        self.assertEqual(payload["experience_years"], 9)
        self.assertEqual(payload["qualification"], "CA")
        self.assertEqual(payload["bio"], "Specializes in direct tax compliance and advisory.")
        self.assertEqual(payload["average_rating"], 4.6)
