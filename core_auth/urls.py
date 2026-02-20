from django.urls import path
from core_auth.views import (
    SendOTPView, VerifyOTPView,
    CustomTokenObtainPairView, UserDashboardView, GoogleAuthView,
    ClientProfileView, LogoutView, ConsultantClientsView,
    CustomTokenRefreshView, WebSocketTokenView
)

urlpatterns = [
    path('auth/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/token/websocket/', WebSocketTokenView.as_view(), name='websocket_token'),
    path('auth/send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('auth/dashboard/', UserDashboardView.as_view(), name='user-dashboard'),
    path('auth/google/', GoogleAuthView.as_view(), name='google-auth'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('client/profile/', ClientProfileView.as_view(), name='client-profile'),
    path('consultant/clients/', ConsultantClientsView.as_view(), name='consultant-clients'),

]
