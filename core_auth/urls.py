from django.urls import path
from core_auth.views import (
    SendOTPView, VerifyOTPView,
    CustomTokenObtainPairView, UserDashboardView, GoogleAuthView,
    ClientProfileView, LogoutView, ConsultantClientsView,
    CustomTokenRefreshView, WebSocketTokenView, ContactSubmissionView,
    ClientRegisterView, ClientEmailLoginView,
    SendMagicLinkView, VerifyMagicLinkView,
    ForgotPasswordView, ResetPasswordView,
    VerifySessionView
)
from consultant_onboarding.views import auth as onboarding_auth
from consultant_onboarding.views import face_matching as onboarding_face
from consultant_onboarding.views.documents import UploadDocumentView as OnboardingDocumentUploadView


urlpatterns = [
    path('auth/verify-session/', VerifySessionView.as_view(), name='verify-session'),
    path('auth/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/token/websocket/', WebSocketTokenView.as_view(), name='websocket_token'),
    path('auth/send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('auth/dashboard/', UserDashboardView.as_view(), name='user-dashboard'),
    path('auth/profile/', onboarding_auth.get_user_profile, name='user-profile'),  # onboarding profile with step flags
    path('auth/google/', GoogleAuthView.as_view(), name='google-auth'),

    # Client Email/Password Auth
    path('auth/client/register/', ClientRegisterView.as_view(), name='client-register'),
    path('auth/client/login/', ClientEmailLoginView.as_view(), name='client-email-login'),

    # Magic Link Auth
    path('auth/magic-link/send/', SendMagicLinkView.as_view(), name='magic-link-send'),
    path('auth/magic-link/verify/', VerifyMagicLinkView.as_view(), name='magic-link-verify'),

    # Forgot / Reset Password
    path('auth/forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('auth/reset-password/', ResetPasswordView.as_view(), name='reset-password'),

    path('auth/onboarding/', onboarding_auth.complete_onboarding, name='onboarding-complete-alias'),
    path('auth/onboarding/send-otp/', onboarding_auth.send_phone_otp, name='onboarding-send-otp'),
    path('auth/onboarding/verify-otp/', onboarding_auth.verify_phone_otp, name='onboarding-verify-otp'),
    path('auth/accept-declaration/', onboarding_auth.accept_declaration, name='accept-declaration-alias'),
    path('auth/identity/upload-doc/', onboarding_auth.upload_identity_document, name='identity-upload-alias'),
    path('auth/documents/list/', onboarding_auth.get_user_documents, name='documents-list-alias'),
    path('documents/upload/', OnboardingDocumentUploadView.as_view(), name='onboarding-doc-upload-alias'),
    path('face-verification/users/<int:user_id>/upload-photo/', onboarding_face.upload_photo, name='face-upload-alias'),
    path('face-verification/users/<int:user_id>/verify-face/', onboarding_face.verify_face, name='face-verify-alias'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('client/profile/', ClientProfileView.as_view(), name='client-profile'),
    path('consultant/clients/', ConsultantClientsView.as_view(), name='consultant-clients'),
    path('contact/', ContactSubmissionView.as_view(), name='contact-submission'),
]
