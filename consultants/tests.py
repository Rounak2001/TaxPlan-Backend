from django.test import TestCase
from django.contrib.auth import get_user_model
from consultants.models import (
    ConsultantServiceProfile,
    ConsultantServiceExpertise,
    ClientServiceRequest,
    ConsultantReview,
    ServiceCategory,
    Service
)
from core_auth.models import ClientProfile
from consultations.models import Topic

User = get_user_model()

class ConsultantReviewTestCase(TestCase):
    def setUp(self):
        # Create users
        self.consultant_user = User.objects.create_user(username="consultant", email="consultant@test.com", password="password", role="CONSULTANT")
        self.client_user = User.objects.create_user(username="client", email="client@test.com", password="password", role="CLIENT")
        
        # Create profiles
        ClientProfile.objects.create(user=self.client_user)
        self.consultant_profile = ConsultantServiceProfile.objects.create(
            user=self.consultant_user,
            qualification="CA"
        )
        
        # Create service data
        self.category = ServiceCategory.objects.create(name="Tax", description="Tax Services", is_active=True)
        self.service = Service.objects.create(category=self.category, title="ITR Filing")
        
        # Consultant expertise
        ConsultantServiceExpertise.objects.create(
            consultant=self.consultant_profile,
            service=self.service
        )
        
        # Create service request
        self.service_request = ClientServiceRequest.objects.create(
            client=self.client_user,
            service=self.service,
            assigned_consultant=self.consultant_profile,
            status='completed'
        )

    def test_review_creation_updates_consultant_rating(self):
        # Initial check
        self.assertEqual(self.consultant_profile.average_rating, 0.0)
        self.assertEqual(self.consultant_profile.total_reviews, 0)
        
        # Create review 1
        review1 = ConsultantReview.objects.create(
            consultant=self.consultant_profile,
            client=self.client_user,
            service_request=self.service_request,
            rating=4,
            review_text="Good service."
        )
        
        self.consultant_profile.refresh_from_db()
        self.assertEqual(self.consultant_profile.average_rating, 4.0)
        self.assertEqual(self.consultant_profile.total_reviews, 1)
        
        # Create another service request and review
        client_user2 = User.objects.create_user(username="client2", email="client2@test.com", password="password", role="CLIENT")
        service_request2 = ClientServiceRequest.objects.create(
            client=client_user2,
            service=self.service,
            assigned_consultant=self.consultant_profile,
            status='completed'
        )
        ConsultantReview.objects.create(
            consultant=self.consultant_profile,
            client=client_user2,
            service_request=service_request2,
            rating=5,
            review_text="Excellent!"
        )
        
        self.consultant_profile.refresh_from_db()
        self.assertEqual(self.consultant_profile.average_rating, 4.5)
        self.assertEqual(self.consultant_profile.total_reviews, 2)

    def test_review_deletion_updates_consultant_rating(self):
        review = ConsultantReview.objects.create(
            consultant=self.consultant_profile,
            client=self.client_user,
            service_request=self.service_request,
            rating=5
        )
        self.consultant_profile.refresh_from_db()
        self.assertEqual(self.consultant_profile.total_reviews, 1)
        
        # Delete review
        review.delete()
        
        self.consultant_profile.refresh_from_db()
        self.assertEqual(self.consultant_profile.average_rating, 0.0)
        self.assertEqual(self.consultant_profile.total_reviews, 0)


class ConsultantCascadeDeleteSignalTestCase(TestCase):
    def setUp(self):
        self.consultant_user = User.objects.create_user(
            username="consultant_delete",
            email="consultant-delete@test.com",
            password="password",
            role="CONSULTANT",
        )
        self.consultant_profile = ConsultantServiceProfile.objects.create(
            user=self.consultant_user,
            qualification="CA",
        )
        self.category = ServiceCategory.objects.create(
            name="Cascade Tax",
            description="Cascade test services",
            is_active=True,
        )
        self.service = Service.objects.create(
            category=self.category,
            title="Cascade ITR Filing",
            tat="2 days",
            documents_required="PAN, Aadhaar",
        )
        self.precise_topic = Topic.objects.create(
            name="Cascade ITR Topic",
            category=self.category,
            service=self.service,
        )
        self.broad_topic = Topic.objects.create(
            name="Cascade Tax Broad Topic",
            category=self.category,
        )
        self.precise_topic.consultants.add(self.consultant_user)
        self.broad_topic.consultants.add(self.consultant_user)
        ConsultantServiceExpertise.objects.create(
            consultant=self.consultant_profile,
            service=self.service,
        )

    def test_deleting_consultant_user_does_not_crash_expertise_post_delete_signal(self):
        self.consultant_user.delete()

        self.assertFalse(
            ConsultantServiceProfile.objects.filter(id=self.consultant_profile.id).exists()
        )
        self.assertFalse(
            ConsultantServiceExpertise.objects.filter(service=self.service).exists()
        )
        self.assertEqual(self.precise_topic.consultants.count(), 0)
        self.assertEqual(self.broad_topic.consultants.count(), 0)
