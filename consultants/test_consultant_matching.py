from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from consultants.models import (
    ConsultantServiceProfile,
    ConsultantServiceExpertise,
    ServiceCategory,
    Service
)
from core_auth.models import ClientProfile

User = get_user_model()

class ConsultantMatchingTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        
        # Create users
        self.consultant1_user = User.objects.create_user(username="consultant1", email="c1@test.com", password="password", role="CONSULTANT")
        self.consultant2_user = User.objects.create_user(username="consultant2", email="c2@test.com", password="password", role="CONSULTANT")
        self.admin_user = User.objects.create_user(username="agent", email="agent@test.com", password="password", role="AGENT")
        
        # Create profiles
        self.c1_profile = ConsultantServiceProfile.objects.create(user=self.consultant1_user, qualification="CA", is_active=True, max_concurrent_clients=10)
        self.c2_profile = ConsultantServiceProfile.objects.create(user=self.consultant2_user, qualification="Lawyer", is_active=True, max_concurrent_clients=10)
        
        # Create service data
        self.category = ServiceCategory.objects.create(name="Legal", description="Legal Services", is_active=True)
        self.service_pan = Service.objects.create(category=self.category, title="PAN Application", is_active=True, tat="2-3 days", documents_required="PAN, Aadhaar")
        self.service_aadhaar = Service.objects.create(category=self.category, title="Aadhaar Validation", is_active=True, tat="1 day", documents_required="Aadhaar")
        self.service_itr = Service.objects.create(category=self.category, title="ITR Filing", is_active=True, tat="5 days", documents_required="Form 16")
        
        # Consultant 1 has PAN and Aadhaar
        ConsultantServiceExpertise.objects.create(consultant=self.c1_profile, service=self.service_pan)
        ConsultantServiceExpertise.objects.create(consultant=self.c1_profile, service=self.service_aadhaar)
        
        # Consultant 2 has ONLY PAN
        ConsultantServiceExpertise.objects.create(consultant=self.c2_profile, service=self.service_pan)
        
        # Authenticate as admin/agent to access the API
        self.client.force_authenticate(user=self.admin_user)

    def test_single_service_match(self):
        """Verify consultants who provide the single requested service are returned."""
        response = self.client.post('/api/consultants/services/match-cart/', {'titles': ["PAN Application"]}, format='json')
        self.assertEqual(response.status_code, 200)
        consultant_ids = [c['id'] for c in response.data['consultants']]
        self.assertIn(self.c1_profile.id, consultant_ids)
        self.assertIn(self.c2_profile.id, consultant_ids)
        self.assertEqual(len(consultant_ids), 2)

    def test_dual_service_match(self):
        """Verify only consultants providing BOTH services are returned."""
        response = self.client.post('/api/consultants/services/match-cart/', {'titles': ["PAN Application", "Aadhaar Validation"]}, format='json')
        self.assertEqual(response.status_code, 200)
        consultant_ids = [c['id'] for c in response.data['consultants']]
        self.assertIn(self.c1_profile.id, consultant_ids)
        self.assertNotIn(self.c2_profile.id, consultant_ids) # C2 only has PAN
        self.assertEqual(len(consultant_ids), 1)

    def test_no_match(self):
        """Verify empty list if no one provides all requested services."""
        response = self.client.post('/api/consultants/services/match-cart/', {'titles': ["PAN Application", "ITR Filing"]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['consultants']), 0)

    def test_case_insensitivity(self):
        """Verify matching works regardless of case."""
        response = self.client.post('/api/consultants/services/match-cart/', {'titles': ["pan application"]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['consultants']), 2)
