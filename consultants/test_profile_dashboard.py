from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from consultant_onboarding.models import ConsultantApplication
from consultants.models import ConsultantServiceProfile
from core_auth.models import User


class ConsultantProfileDashboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='consultant_profile_dashboard',
            email='consultant-profile@example.com',
            password='password',
            role=User.CONSULTANT,
            phone_number='+919876543210',
            is_phone_verified=True,
            first_name='Riya',
            last_name='Kapoor',
        )
        self.profile = ConsultantServiceProfile.objects.create(
            user=self.user,
            qualification='CA',
            experience_years=7,
            certifications='DISA',
            pan_number='ABCDE1234F',
            gstin='27ABCDE1234F1Z5',
            bio='Existing profile bio',
            consultation_fee=Decimal('2500.00'),
            average_rating=Decimal('4.75'),
            total_reviews=12,
        )
        self.application = ConsultantApplication.objects.create(
            email=self.user.email,
            first_name='Riya',
            last_name='Kapoor',
            phone_number=self.user.phone_number,
            is_phone_verified=True,
            age=31,
            dob=date(1995, 2, 14),
            address_line1='221B Baker Street',
            address_line2='Near Central Park',
            city='Mumbai',
            state='Maharashtra',
            pincode='400001',
            practice_type='Individual',
            qualification='CA',
            experience_years=7,
            certifications='DISA',
        )
        self.client.force_authenticate(user=self.user)

    def test_dashboard_profile_includes_application_backed_fields_and_rating(self):
        response = self.client.get(reverse('consultant-profile-dashboard'))

        self.assertEqual(response.status_code, 200)
        profile_data = response.data['profile']

        self.assertEqual(profile_data['bio'], 'Existing profile bio')
        self.assertEqual(profile_data['phone'], '+919876543210')
        self.assertEqual(profile_data['email'], 'consultant-profile@example.com')
        self.assertEqual(profile_data['age'], 31)
        self.assertEqual(profile_data['dob'], '1995-02-14')
        self.assertEqual(profile_data['address_line1'], '221B Baker Street')
        self.assertEqual(profile_data['address_line2'], 'Near Central Park')
        self.assertEqual(profile_data['city'], 'Mumbai')
        self.assertEqual(profile_data['state'], 'Maharashtra')
        self.assertEqual(profile_data['pincode'], '400001')
        self.assertEqual(profile_data['practice_type'], 'Individual')
        self.assertEqual(profile_data['pan_number'], 'ABCDE1234F')
        self.assertEqual(profile_data['gstin'], '27ABCDE1234F1Z5')
        self.assertEqual(profile_data['average_rating'], '4.75')
        self.assertEqual(profile_data['total_reviews'], 12)

    def test_profile_patch_updates_bio_and_address_without_touching_read_only_fields(self):
        response = self.client.patch(
            reverse('consultant-profile-detail', args=[self.profile.id]),
            {
                'bio': 'Updated from dashboard',
                'address_line1': '42 Marine Drive',
                'address_line2': 'Suite 8',
                'city': 'Pune',
                'state': 'Maharashtra',
                'pincode': '411001',
                'age': 99,
                'dob': '2000-01-01',
                'practice_type': 'Changed',
                'email': 'blocked-change@example.com',
                'phone': '+911111111111',
                'pan_number': 'PQRSX6789T',
                'gstin': '29PQRSX6789T1Z2',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)

        self.profile.refresh_from_db()
        self.application.refresh_from_db()
        self.user.refresh_from_db()

        self.assertEqual(self.profile.bio, 'Updated from dashboard')
        self.assertEqual(self.profile.pan_number, 'PQRSX6789T')
        self.assertEqual(self.profile.gstin, '29PQRSX6789T1Z2')
        self.assertEqual(self.application.address_line1, '42 Marine Drive')
        self.assertEqual(self.application.address_line2, 'Suite 8')
        self.assertEqual(self.application.city, 'Pune')
        self.assertEqual(self.application.state, 'Maharashtra')
        self.assertEqual(self.application.pincode, '411001')
        self.assertEqual(self.application.age, 31)
        self.assertEqual(str(self.application.dob), '1995-02-14')
        self.assertEqual(self.application.practice_type, 'Individual')
        self.assertEqual(self.user.email, 'consultant-profile@example.com')
        self.assertEqual(self.user.phone_number, '+919876543210')

    def test_profile_patch_rejects_invalid_pan_and_gstin(self):
        response = self.client.patch(
            reverse('consultant-profile-detail', args=[self.profile.id]),
            {
                'pan_number': 'INVALIDPAN',
                'gstin': 'INVALIDGSTIN',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('pan_number', response.data)
        self.assertIn('gstin', response.data)
