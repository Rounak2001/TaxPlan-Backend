from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from core_auth.serializers import IsConsultantUser, IsClientUser
from core_auth.models import User, ClientProfile
from django.conf import settings
import requests

def set_auth_cookies(response, user):
    """Helper to set HttpOnly JWT cookies for a user."""
    refresh = RefreshToken.for_user(user)
    
    # Add custom claims to the access token
    refresh['user_role'] = user.role
    refresh['full_name'] = f"{user.first_name} {user.last_name}".strip() or user.username
    refresh['is_phone_verified'] = user.is_phone_verified
    
    access_token = str(refresh.access_token)
    refresh_token = str(refresh)
    
    response.set_cookie(
        key='access_token',
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in production
        samesite='Lax',
        max_age=3600
    )
    response.set_cookie(
        key='refresh_token',
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite='Lax',
        max_age=86400
    )
    return response


class OTPVerifyView(APIView):
    # Basic placeholder for OTP verification URL name resolution in middleware
    def post(self, request):
        return Response({"message": "OTP Verified"}, status=status.HTTP_200_OK)

class CustomTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            access_token = response.data['access']
            refresh_token = response.data['refresh']
            
            response.set_cookie(
                key='access_token',
                value=access_token,
                httponly=True,
                secure=False,
                samesite='Lax',
                max_age=3600
            )
            response.set_cookie(
                key='refresh_token',
                value=refresh_token,
                httponly=True,
                secure=False,
                samesite='Lax',
                max_age=86400
            )
        return response

class GoogleAuthView(APIView):
    """
    Handles Google OAuth login for Clients.
    Verifies the Google ID token and creates/finds a user.
    """
    authentication_classes = []  # Skip JWT auth - we verify Google token instead
    permission_classes = [AllowAny]
    
    def post(self, request):
        id_token = request.data.get('id_token')
        
        if not id_token:
            return Response({'error': 'ID token is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify token with Google
        try:
            google_response = requests.get(
                f'https://oauth2.googleapis.com/tokeninfo?id_token={id_token}'
            )
            
            if google_response.status_code != 200:
                return Response({'error': 'Invalid Google token'}, status=status.HTTP_401_UNAUTHORIZED)
            
            google_data = google_response.json()
            
            # Verify the token is for our app
            if google_data.get('aud') != settings.GOOGLE_CLIENT_ID:
                return Response({'error': 'Token not issued for this app'}, status=status.HTTP_401_UNAUTHORIZED)
            
            email = google_data.get('email')
            first_name = google_data.get('given_name', '')
            last_name = google_data.get('family_name', '')
            
            if not email:
                return Response({'error': 'Email not provided by Google'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Find or create user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email.split('@')[0],
                    'first_name': first_name,
                    'last_name': last_name,
                    'role': User.CLIENT,
                    'is_phone_verified': False,  # Will need phone verification later
                    'phone_number': None,  # Google users don't have phone yet
                }
            )
            
            # Create ClientProfile if new user
            if created:
                ClientProfile.objects.create(user=user)
            
            # Create response with user data
            response_data = {
                'success': True,
                'created': created,
                'role': user.role,
                'full_name': f"{user.first_name} {user.last_name}".strip() or user.username,
            }
            
            response = Response(response_data)
            response = set_auth_cookies(response, user)
            
            return response
            
        except requests.RequestException as e:
            return Response({'error': 'Failed to verify token'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LogoutView(APIView):
    """
    Logout by clearing HttpOnly cookies.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    
    def post(self, request):
        response = Response({'success': True, 'message': 'Logged out successfully'})
        response.delete_cookie('access_token')
        response.delete_cookie('refresh_token')
        return response

class CustomTokenRefreshView(APIView):
    """
    Custom token refresh that reads refresh_token from HttpOnly cookies
    instead of request body, and returns new access_token as cookie.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    
    def post(self, request):
        refresh_token = request.COOKIES.get('refresh_token')
        
        if not refresh_token:
            return Response(
                {'error': 'Refresh token not found'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            # Validate and decode the refresh token
            refresh = RefreshToken(refresh_token)
            
            # Generate new access token
            access_token = str(refresh.access_token)
            
            # Create response
            response = Response({
                'success': True,
                'message': 'Token refreshed successfully'
            })
            
            # Set new access token as HttpOnly cookie
            response.set_cookie(
                key='access_token',
                value=access_token,
                httponly=True,
                secure=False,  # Set to True in production with HTTPS
                samesite='Lax',
                max_age=3600  # 1 hour
            )
            
            return response
            
        except Exception as e:
            return Response(
                {'error': 'Invalid or expired refresh token'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )


class UserDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        data = {
            "full_name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "role": user.role,
            "is_onboarded": user.is_onboarded,
            "is_phone_verified": user.is_phone_verified,
        }

        if user.role == "CONSULTANT":
            try:
                profile = user.consultant_profile
                data["stats"] = {
                    "current_load": profile.current_load,
                    "max_capacity": profile.max_capacity,
                    "services": profile.services,
                }
            except Exception:
                data["stats"] = None
        elif user.role == "CLIENT":
            try:
                profile = user.client_profile
                data["advisor"] = {
                    "name": profile.assigned_consultant.get_full_name() if profile.assigned_consultant else "Assigning Soon...",
                    "pan_linked": profile.pan_number is not None,
                }
            except Exception:
                data["advisor"] = None
        
        return Response(data)


class ClientProfileView(APIView):
    """
    API to get and update client profile details.
    Each client can only access their own profile (data isolation).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get current client's profile data."""
        user = request.user
        if user.role != User.CLIENT:
            return Response({'error': 'Only clients can access this endpoint'}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            profile = user.client_profile
            data = {
                'full_name': f"{user.first_name} {user.last_name}".strip(),
                'email': user.email,
                'phone_number': user.phone_number,
                'pan_number': profile.pan_number,
                'gstin': profile.gstin,
                'gst_username': profile.gst_username,
                'is_onboarded': user.is_onboarded,
                'assigned_consultant': profile.assigned_consultant.get_full_name() if profile.assigned_consultant else None,
            }
            return Response(data)
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client profile not found'}, status=status.HTTP_404_NOT_FOUND)

    def patch(self, request):
        """Update current client's profile data."""
        user = request.user
        if user.role != User.CLIENT:
            return Response({'error': 'Only clients can access this endpoint'}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            profile = user.client_profile
            
            # Update user fields
            if 'first_name' in request.data:
                user.first_name = request.data['first_name']
            if 'last_name' in request.data:
                user.last_name = request.data['last_name']
            if 'phone_number' in request.data:
                user.phone_number = request.data['phone_number']
            if 'is_phone_verified' in request.data:
                user.is_phone_verified = request.data['is_phone_verified']
                # If phone is verified and they are a client, they are mostly onboarded
                if user.is_phone_verified:
                    user.is_onboarded = True
            
            # Update profile fields
            if 'pan_number' in request.data:
                profile.pan_number = request.data['pan_number']
            
            user.save()
            profile.save()
            
            return Response({
                'success': True,
                'message': 'Profile updated successfully',
                'full_name': f"{user.first_name} {user.last_name}".strip(),
                'pan_number': profile.pan_number,
                'phone_number': user.phone_number,
            })
        except ClientProfile.DoesNotExist:
            return Response({'error': 'Client profile not found'}, status=status.HTTP_404_NOT_FOUND)


class ConsultantClientsView(APIView):
    """
    API to get clients assigned to the logged-in consultant.
    Data Isolation: Each consultant can ONLY see their own assigned clients.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Only consultants can access this endpoint
        if user.role != User.CONSULTANT:
            return Response({'error': 'Only consultants can access this endpoint'}, status=status.HTTP_403_FORBIDDEN)
        
        # Get clients assigned to THIS consultant only (data isolation)
        # The filter is: ClientProfile.assigned_consultant == request.user
        assigned_clients = ClientProfile.objects.filter(assigned_consultant=user).select_related('user')
        
        clients_data = []
        for profile in assigned_clients:
            client_user = profile.user
            clients_data.append({
                'id': client_user.id,
                'full_name': f"{client_user.first_name} {client_user.last_name}".strip() or client_user.username,
                'email': client_user.email,
                'phone_number': client_user.phone_number,
                'pan_number': profile.pan_number,
                'gstin': profile.gstin,
                'gst_username': profile.gst_username,
                'is_onboarded': client_user.is_onboarded,
            })
        
        return Response({
            'total_clients': len(clients_data),
            'clients': clients_data,
        })
