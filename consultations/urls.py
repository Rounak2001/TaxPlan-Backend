from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TopicViewSet, WeeklyAvailabilityViewSet, DateOverrideViewSet,
    ConsultationBookingViewSet, available_consultants, 
    consultants_by_date, consultant_slots
)

router = DefaultRouter()
router.register(r'topics', TopicViewSet, basename='topic')
router.register(r'weekly-availability', WeeklyAvailabilityViewSet, basename='weekly-availability')
router.register(r'date-overrides', DateOverrideViewSet, basename='date-override')
router.register(r'bookings', ConsultationBookingViewSet, basename='booking')

urlpatterns = [
    path('', include(router.urls)),
    path('consultants-by-date/', consultants_by_date, name='consultants-by-date'),
    path('consultant-slots/', consultant_slots, name='consultant-slots'),
    path('available-consultants/', available_consultants, name='available-consultants'),
]
