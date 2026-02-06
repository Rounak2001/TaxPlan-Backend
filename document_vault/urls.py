from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DocumentViewSet, SharedReportViewSet, LegalNoticeViewSet, FolderViewSet

router = DefaultRouter()
router.register(r'folders', FolderViewSet, basename='folder')
router.register(r'documents', DocumentViewSet, basename='document')
router.register(r'shared-reports', SharedReportViewSet, basename='shared-report')
router.register(r'legal-notices', LegalNoticeViewSet, basename='legal-notice')

urlpatterns = [
    path('', include(router.urls)),
]

