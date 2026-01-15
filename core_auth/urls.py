from django.urls import path
from core_auth.views import (
    OTPVerifyView, 
    CustomTokenObtainPairView, UserDashboardView, GoogleAuthView,
    ClientProfileView, LogoutView, ConsultantClientsView,
    CustomTokenRefreshView
)

urlpatterns = [
    path('auth/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/verify-otp/', OTPVerifyView.as_view(), name='verify-otp'),
    path('auth/dashboard/', UserDashboardView.as_view(), name='user-dashboard'),
    path('auth/google/', GoogleAuthView.as_view(), name='google-auth'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('client/profile/', ClientProfileView.as_view(), name='client-profile'),
    path('consultant/clients/', ConsultantClientsView.as_view(), name='consultant-clients'),

]
