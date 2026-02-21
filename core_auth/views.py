import re
import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from core_auth.serializers import IsConsultantUser, IsClientUser
from core_auth.models import User, ClientProfile
from core_auth.services.whatsapp_otp import (
    generate_otp, store_otp, send_whatsapp_otp,
    verify_otp as verify_otp_service, can_resend_otp,
    RESEND_COOLDOWN_SECONDS, OTP_EXPIRY_SECONDS
)
from django.conf import settings
import requests

logger = logging.getLogger(__name__)

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
        secure=True,  # Required for SameSite=None
        samesite='None',  # Required for cross-origin cookies
        max_age=3600
    )
    response.set_cookie(
        key='refresh_token',
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite='None',
        max_age=86400
    )
    return response


class SendOTPView(APIView):
    """
    Send OTP to a phone number via WhatsApp.
    Requires authenticated user (must be logged in via Google first).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        phone_number = request.data.get('phone_number', '').strip()

        # Validate: user should provide 10 digits (we add +91 prefix)
        digits_only = re.sub(r'\D', '', phone_number)

        # Handle cases where user sends +91XXXXXXXXXX or 91XXXXXXXXXX or just XXXXXXXXXX
        if len(digits_only) == 12 and digits_only.startswith('91'):
            digits_only = digits_only[2:]  # strip country code
        elif len(digits_only) == 10:
            pass  # already 10 digits
        else:
            return Response(
                {'error': 'Please enter a valid 10-digit Indian mobile number.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate: Indian mobile numbers start with 6-9
        if not re.match(r'^[6-9]\d{9}$', digits_only):
            return Response(
                {'error': 'Please enter a valid Indian mobile number starting with 6-9.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Store as +91XXXXXXXXXX for internal use, 91XXXXXXXXXX for WhatsApp API
        full_phone = f'+91{digits_only}'
        wa_phone = f'91{digits_only}'

        # Check if a different user already has this phone number verified
        existing_user = User.objects.filter(
            phone_number=full_phone, is_phone_verified=True
        ).exclude(id=request.user.id).first()
        if existing_user:
            return Response(
                {'error': 'This phone number is already registered with another account.'},
                status=status.HTTP_409_CONFLICT
            )

        # Rate limiting check
        can_send, reason, wait_seconds = can_resend_otp(full_phone)
        if not can_send:
            return Response(
                {'error': reason, 'cooldown': wait_seconds},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Generate and store OTP
        otp = generate_otp()
        store_otp(full_phone, otp)

        # Send via WhatsApp
        success, message = send_whatsapp_otp(wa_phone, otp)

        if success:
            logger.info(f"OTP sent to {full_phone[-4:]} for user {request.user.id}")
            return Response({
                'success': True,
                'message': 'OTP sent to your WhatsApp number.',
                'cooldown': RESEND_COOLDOWN_SECONDS,
                'expiry': OTP_EXPIRY_SECONDS,
                'phone_display': f'+91 {digits_only[:5]} {digits_only[5:]}',
            })
        else:
            logger.error(f"Failed to send OTP to {full_phone[-4:]}: {message}")
            return Response(
                {'error': message},
                status=status.HTTP_502_BAD_GATEWAY
            )


class VerifyOTPView(APIView):
    """
    Verify OTP and mark phone number as verified.
    Requires authenticated user.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        phone_number = request.data.get('phone_number', '').strip()
        otp = request.data.get('otp', '').strip()

        if not phone_number or not otp:
            return Response(
                {'error': 'Phone number and OTP are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(otp) != 6 or not otp.isdigit():
            return Response(
                {'error': 'OTP must be a 6-digit number.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Normalize phone number to +91XXXXXXXXXX
        digits_only = re.sub(r'\D', '', phone_number)
        if len(digits_only) == 12 and digits_only.startswith('91'):
            digits_only = digits_only[2:]
        if len(digits_only) != 10:
            return Response(
                {'error': 'Invalid phone number format.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        full_phone = f'+91{digits_only}'

        # Verify OTP
        success, message, remaining_attempts = verify_otp_service(full_phone, otp)

        if success:
            # Update user's phone number and verification status
            user = request.user
            user.phone_number = full_phone
            user.is_phone_verified = True
            user.is_onboarded = True
            user.save(update_fields=['phone_number', 'is_phone_verified', 'is_onboarded'])

            logger.info(f"Phone verified for user {user.id}: {full_phone[-4:]}")

            return Response({
                'success': True,
                'verified': True,
                'message': message,
            })
        else:
            return Response({
                'success': False,
                'verified': False,
                'message': message,
                'remaining_attempts': remaining_attempts,
            }, status=status.HTTP_400_BAD_REQUEST)

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
                secure=True,
                samesite='None',
                max_age=3600
            )
            response.set_cookie(
                key='refresh_token',
                value=refresh_token,
                httponly=True,
                secure=True,
                samesite='None',
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
        access_token = request.data.get('access_token')
        
        if not id_token and not access_token:
            return Response({'error': 'ID token or Access token is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        email = None
        first_name = ''
        last_name = ''

        try:
            if id_token:
                # Verify ID token with Google
                google_response = requests.get(
                    f'https://oauth2.googleapis.com/tokeninfo?id_token={id_token}'
                )
                
                if google_response.status_code != 200:
                    return Response({'error': 'Invalid Google ID token'}, status=status.HTTP_401_UNAUTHORIZED)
                
                google_data = google_response.json()
                
                # Verify the token is for our app
                if google_data.get('aud') != settings.GOOGLE_CLIENT_ID:
                    return Response({'error': 'Token not issued for this app'}, status=status.HTTP_401_UNAUTHORIZED)
                
                email = google_data.get('email')
                first_name = google_data.get('given_name', '')
                last_name = google_data.get('family_name', '')

            elif access_token:
                # Verify Access token by fetching user info
                google_response = requests.get(
                    'https://www.googleapis.com/oauth2/v3/userinfo',
                    headers={'Authorization': f'Bearer {access_token}'}
                )

                if google_response.status_code != 200:
                    return Response({'error': 'Invalid Google Access token'}, status=status.HTTP_401_UNAUTHORIZED)
                
                google_data = google_response.json()
                
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
        response.delete_cookie('access_token', samesite='None', secure=True)
        response.delete_cookie('refresh_token', samesite='None', secure=True)
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
                secure=True,
                samesite='None',
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
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "username": user.username,
            "email": user.email,
            "phone": user.phone_number,
            "role": user.role,
            "is_onboarded": user.is_onboarded,
            "is_phone_verified": user.is_phone_verified,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone_number,
        }

        if user.role == "CONSULTANT":
            try:
                profile = user.consultant_service_profile
                from consultants.models import ConsultantServiceExpertise
                services = list(
                    ConsultantServiceExpertise.objects.filter(consultant=profile)
                    .values_list('service__title', flat=True)
                )
                from consultants.models import ClientServiceRequest
                live_client_count = ClientServiceRequest.objects.filter(
                    assigned_consultant=profile
                ).exclude(status__in=['completed', 'cancelled']).values('client').distinct().count()

                data["stats"] = {
                    "current_load": live_client_count,
                    "max_capacity": profile.max_concurrent_clients,
                    "consultation_fee": float(profile.consultation_fee),
                    "qualification": profile.qualification,
                    "experience_years": profile.experience_years,
                    "certifications": profile.certifications,
                    "services": services,
                }
            except Exception:
                data["stats"] = None
        if user.role == "CLIENT":
            try:
                profile = user.client_profile
                data["pan"] = profile.pan_number
                data["gst"] = profile.gstin
                
                advisor = profile.assigned_consultant
                advisor_data = None
                
                if advisor:
                    from consultants.models import ConsultantServiceProfile
                    try:
                        service_profile = advisor.consultant_service_profile
                        advisor_data = {
                            "name": service_profile.full_name,
                            "email": service_profile.email,
                            "phone": service_profile.phone,
                            "qualification": service_profile.qualification,
                            "avatar": "" # Placeholder
                        }
                    except Exception:
                        advisor_data = {
                            "name": advisor.get_full_name() or advisor.username,
                            "email": advisor.email,
                            "phone": advisor.phone_number,
                            "avatar": ""
                        }
                
                data["advisor"] = advisor_data
                data["compliance"] = {
                    "pan_linked": profile.pan_number is not None,
                    "gstin_linked": profile.gstin is not None
                }
            except Exception:
                data["advisor"] = None
                data["compliance"] = None
        
        return Response(data)


class WebSocketTokenView(APIView):
    """
    Returns the access token for WebSocket authentication.
    This is needed because WebSockets can't access HttpOnly cookies directly.
    The frontend calls this endpoint to get the token before connecting to WebSocket.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # Get the access token from the HttpOnly cookie
        access_token = request.COOKIES.get('access_token')
        
        if access_token:
            return Response({'token': access_token})
        
        # If no access token in cookie, generate a new one from refresh token
        refresh_token = request.COOKIES.get('refresh_token')
        if refresh_token:
            try:
                refresh = RefreshToken(refresh_token)
                access_token = str(refresh.access_token)
                return Response({'token': access_token})
            except Exception:
                pass
        
        return Response(
            {'error': 'No valid token found'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )


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
        
        # Get clients assigned via ClientProfile (Primary Consultant)
        primary_clients = ClientProfile.objects.filter(assigned_consultant=user).values_list('user_id', flat=True)
        
        # Get clients assigned via ClientServiceRequest (Service Consultant)
        # Only include clients with active (non-terminal) service requests
        from consultants.models import ClientServiceRequest
        service_clients = ClientServiceRequest.objects.filter(
            assigned_consultant__user=user
        ).exclude(status__in=['completed', 'cancelled']).values_list('client_id', flat=True)
        
        # Combine and get unique client IDs
        client_ids = set(list(primary_clients) + list(service_clients))
        
        # Fetch profiles with user data
        assigned_clients = ClientProfile.objects.filter(user_id__in=client_ids).select_related('user')
        
        # Get all active service requests for these clients assigned to this consultant
        active_requests = ClientServiceRequest.objects.filter(
            client_id__in=client_ids,
            assigned_consultant__user=user
        ).exclude(status__in=['completed', 'cancelled']).select_related('service')

        # Map requests to clients
        client_requests_map = {}
        for req in active_requests:
            if req.client_id not in client_requests_map:
                client_requests_map[req.client_id] = []
            client_requests_map[req.client_id].append({
                'id': req.id,
                'service_title': req.service.title,
                'status': req.status,
                'status_display': req.get_status_display()
            })

        # Get completed requests for earnings calculation
        completed_requests = ClientServiceRequest.objects.filter(
            client_id__in=client_ids,
            assigned_consultant__user=user,
            status='completed'
        ).select_related('service')

        # Map earnings to clients
        client_earnings_map = {}
        for req in completed_requests:
            price = req.service.price or 0
            client_earnings_map[req.client_id] = client_earnings_map.get(req.client_id, 0) + float(price)

        clients_data = []
        for profile in assigned_clients:
            client_user = profile.user
            clients_data.append({
                'id': client_user.id,
                'name': f"{client_user.first_name} {client_user.last_name}".strip() or client_user.username,
                'email': client_user.email,
                'pan': profile.pan_number,
                'gstin': profile.gstin,
                'gst_username': profile.gst_username,
                'status': 'active' if client_user.is_onboarded else 'pending',
                'active_requests': client_requests_map.get(client_user.id, []),
                'earnings': client_earnings_map.get(client_user.id, 0),
                'avatarUrl': '',
                'createdAt': client_user.date_joined.isoformat() if client_user.date_joined else None,
                'consultantId': user.id,
                'lastActivity': None,
            })
        
        return Response(clients_data)
