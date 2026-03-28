from django.test import TestCase

from consultant_onboarding.models import ConsultantApplication
from consultant_onboarding.serializers import ApplicationSerializer, OnboardingSerializer


class OnboardingSerializerFieldTests(TestCase):
    def test_bio_is_not_exposed_in_application_serializer(self):
        self.assertNotIn('bio', ApplicationSerializer().fields)

    def test_bio_is_not_accepted_by_onboarding_serializer(self):
        self.assertNotIn('bio', OnboardingSerializer().fields)

    def test_onboarding_serializer_persists_experience_years_exactly(self):
        application = ConsultantApplication.objects.create(email='experience-check@example.com')
        serializer = OnboardingSerializer(
            instance=application,
            data={
                'first_name': 'Aarav',
                'middle_name': '',
                'last_name': 'Sharma',
                'age': 29,
                'dob': '1997-01-15',
                'phone_number': '+919876543210',
                'address_line1': '11 Residency Road',
                'address_line2': 'Block A',
                'city': 'Bengaluru',
                'state': 'Karnataka',
                'pincode': '560001',
                'practice_type': 'Individual',
                'qualification': 'CA',
                'experience_years': 11,
                'certifications': 'DISA',
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        saved = serializer.save()
        self.assertEqual(saved.experience_years, 11)
        application.refresh_from_db()
        self.assertEqual(application.experience_years, 11)
