from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import auth, test_engine, face_matching, admin_panel
from .views.documents import UploadDocumentView, DocumentListView

router = DefaultRouter()
router.register(r'test-types', test_engine.TestTypeViewSet, basename='test-types')
router.register(r'sessions', test_engine.UserSessionViewSet, basename='sessions')

urlpatterns = [
    # Auth & Profile Endpoints
    path('auth/google/', auth.google_auth, name='onboarding_google_auth'),
    path('auth/onboarding/complete/', auth.complete_onboarding, name='onboarding_complete'),
    path('auth/profile/', auth.get_user_profile, name='onboarding_profile'),
    path('auth/accept-declaration/', auth.accept_declaration, name='onboarding_accept_declaration'),
    path('auth/logout/', auth.logout, name='onboarding_logout'),
    path('health/', auth.health_check, name='onboarding_health_check'),

    # Auth Documents (files attached to the applicant account)
    path('auth/documents/upload/', auth.upload_document, name='onboarding_upload_document'),
    path('auth/documents/list/', auth.get_user_documents, name='onboarding_get_user_documents'),

    # Identity Documents (for Gemini ID verification)
    path('auth/identity/upload-doc/', auth.upload_identity_document, name='onboarding_upload_identity_document'),

    # Qualification Documents (degree, certificates with Gemini check)
    path('documents/upload/', UploadDocumentView.as_view(), name='onboarding_document_upload'),
    path('documents/list/', DocumentListView.as_view(), name='onboarding_document_list'),

    # Face Verification (Rekognition)
    path('face-verification/upload-photo/', face_matching.upload_photo, name='onboarding_upload_photo'),
    path('face-verification/verify-face/', face_matching.verify_face, name='onboarding_verify_face'),

    # Assessment Engine (MCQ tests, video responses, proctoring)
    path('assessment/', include(router.urls)),

    # Admin Panel API
    path('admin-panel/login/', admin_panel.admin_login, name='admin_panel_login'),
    path('admin-panel/consultants/', admin_panel.consultant_list, name='admin_panel_consultant_list'),
    path('admin-panel/consultants/<int:app_id>/', admin_panel.consultant_detail, name='admin_panel_consultant_detail'),
    path('admin-panel/consultants/<int:app_id>/generate-credentials/', admin_panel.generate_credentials, name='admin_panel_generate_credentials'),
]

