from django.test import TestCase

from consultant_onboarding.serializers import ApplicationSerializer, OnboardingSerializer


class OnboardingSerializerFieldTests(TestCase):
    def test_bio_is_not_exposed_in_application_serializer(self):
        self.assertNotIn('bio', ApplicationSerializer().fields)

    def test_bio_is_not_accepted_by_onboarding_serializer(self):
        self.assertNotIn('bio', OnboardingSerializer().fields)
