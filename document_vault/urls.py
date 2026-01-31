from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DocumentViewSet, SharedReportViewSet

router = DefaultRouter()
router.register(r'documents', DocumentViewSet, basename='document')
router.register(r'shared-reports', SharedReportViewSet, basename='shared-report')

urlpatterns = [
    path('', include(router.urls)),
]

