from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ServiceCategoryViewSet,
    ServiceViewSet,
    ConsultantServiceProfileViewSet,
    ConsultantServiceExpertiseViewSet,
    ClientServiceRequestViewSet,
    ConsultantReviewViewSet
)

router = DefaultRouter()
router.register(r'categories', ServiceCategoryViewSet, basename='service-category')
router.register(r'services', ServiceViewSet, basename='service')
router.register(r'consultant-profiles', ConsultantServiceProfileViewSet, basename='consultant-profile')
router.register(r'expertise', ConsultantServiceExpertiseViewSet, basename='consultant-expertise')
router.register(r'requests', ClientServiceRequestViewSet, basename='service-request')
router.register(r'reviews', ConsultantReviewViewSet, basename='consultant-review')

urlpatterns = [
    path('', include(router.urls)),
]
