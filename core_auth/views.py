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
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import requests
MAGIC_LINK_EXPIRY_MINUTES=15
logger = logging.getLogger(__name__)

def set_auth_cookies(response, user):
    """Helper to set HttpOnly JWT cookies for a user using settings-based configuration."""
    refresh = RefreshToken.for_user(user)
    
    # Add custom claims to the access token
    refresh['user_role'] = user.role
    refresh['full_name'] = f"{user.first_name} {user.last_name}".strip() or user.username
    refresh['is_phone_verified'] = user.is_phone_verified
    
    access_token = str(refresh.access_token)
    refresh_token = str(refresh)
    
    # Get config from SIMPLE_JWT or use defaults
    jwt_conf = getattr(settings, 'SIMPLE_JWT', {})
    samesite = jwt_conf.get('AUTH_COOKIE_SAMESITE', 'Lax')
    secure = jwt_conf.get('AUTH_COOKIE_SECURE', True)
    domain = jwt_conf.get('AUTH_COOKIE_DOMAIN', None)
    
    response.set_cookie(
        key='access_token',
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        max_age=3600
    )
    response.set_cookie(
        key='refresh_token',
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        max_age=86400 * 7 # 7 days
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

@method_decorator(csrf_exempt, name='dispatch')
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
        
        # Accept 'token' as an alias for 'id_token' (used by onboarding frontend)
        if not id_token and not access_token:
            token_alias = request.data.get('token')
            if token_alias:
                id_token = token_alias
            else:
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
                
                # Verify the token is for our app - accept multiple client IDs
                # (main SaaS frontend + onboarding frontend may use different OAuth clients)
                allowed_client_ids = [cid for cid in [
                    settings.GOOGLE_CLIENT_ID,
                    getattr(settings, 'GOOGLE_ONBOARDING_CLIENT_ID', None),
                ] if cid]
                token_aud = google_data.get('aud')
                if token_aud not in allowed_client_ids:
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
            
            # Find existing user or create a new client
            try:
                user = User.objects.get(email=email)
                created = False
                
                # OPTION A ENFORCEMENT: Consultants cannot log in via Google
                if user.role == User.CONSULTANT:
                    return Response(
                        {
                            'error': 'Consultants must use their provided username   and password to log in.',
                            'code': 'EMAIL_CONFLICT'
                        }, 
                        status=status.HTTP_403_FORBIDDEN
                    )
            except User.DoesNotExist:
                # OPTION A ENFORCEMENT (early detection): Before creating a new Client,
                # check if this email is already used in the Consultant Onboarding portal.
                # This prevents someone from registering as a Client with an email they 
                # already used to apply as a Consultant (which would conflict later at approval).
                try:
                    from consultant_onboarding.models import ConsultantApplication
                    if ConsultantApplication.objects.filter(
                        email=email
                    ).exclude(status='REJECTED').exists():
                        return Response(
                            {
                                'error': 'This email is already registered in our Consultant Onboarding portal. Please use a different email to sign up as a Client.',
                                'code': 'EMAIL_CONFLICT'
                            },
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Exception:
                    pass  # Non-critical — proceed if consultant_onboarding is unavailable

                # New users signing up via Google on main app default to CLIENT
                user = User.objects.create(
                    email=email,
                    username=email.split('@')[0],
                    first_name=first_name,
                    last_name=last_name,
                    role=User.CLIENT,
                    is_phone_verified=False,
                    phone_number=None
                )
                created = True
            
            # Create ClientProfile if it's a new client
            if created and user.role == User.CLIENT:
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


@method_decorator(csrf_exempt, name='dispatch')
class LogoutView(APIView):
    """
    Logout by clearing HttpOnly cookies.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    
    def post(self, request):
        jwt_conf = getattr(settings, 'SIMPLE_JWT', {})
        samesite = jwt_conf.get('AUTH_COOKIE_SAMESITE', 'Lax')
        domain = jwt_conf.get('AUTH_COOKIE_DOMAIN', None)
        
        response = Response({'success': True, 'message': 'Logged out successfully'})
        response.delete_cookie('access_token', samesite=samesite, domain=domain)
        response.delete_cookie('refresh_token', samesite=samesite, domain=domain)
        return response

@method_decorator(csrf_exempt, name='dispatch')
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
            
            # Rotation logic if enabled
            jwt_conf = getattr(settings, 'SIMPLE_JWT', {})
            rotate_refresh = jwt_conf.get('ROTATE_REFRESH_TOKENS', True)
            
            # Create response
            response = Response({
                'success': True,
                'message': 'Token refreshed successfully'
            })
            
            # Set new access token as HttpOnly cookie
            samesite = jwt_conf.get('AUTH_COOKIE_SAMESITE', 'Lax')
            secure = jwt_conf.get('AUTH_COOKIE_SECURE', True)
            domain = jwt_conf.get('AUTH_COOKIE_DOMAIN', None)
            
            response.set_cookie(
                key='access_token',
                value=str(refresh.access_token),
                httponly=True,
                secure=secure,
                samesite=samesite,
                domain=domain,
                max_age=3600  # 1 hour
            )
            
            # Handle Refresh Token Rotation
            if rotate_refresh:
                # Blacklist the old one if BLACKLIST_AFTER_ROTATION is true
                # simple_jwt handles the blacklist if we use its TokenRefreshView,
                # here we need to manually rotate if we want to follow the settings.
                new_refresh = str(refresh) # RefreshToken(str(refresh)) might be needed for rotation if settings applied
                # Actually str(refresh) will give the SAME token unless blacklist enabled?
                # Let's just issue a COMPLETELY new one for the user to be sure
                new_token = RefreshToken.for_user(User.objects.get(id=refresh.payload.get('user_id')))
                
                response.set_cookie(
                    key='refresh_token',
                    value=str(new_token),
                    httponly=True,
                    secure=secure,
                    samesite=samesite,
                    domain=domain,
                    max_age=86400 * 7
                )
            
            return response
            
        except Exception as e:
            logger.error(f"Refresh Token Error: {str(e)}")
            return Response(
                {'error': 'Invalid or expired refresh token'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )


@method_decorator(csrf_exempt, name='dispatch')
class VerifySessionView(APIView):
    """
    Lightweight stateless session verification endpoint.
    Called by React on every page reload to restore auth state.

    Strategy: decode + cryptographically verify the JWT from the HttpOnly cookie.
    No database hit — custom claims (role, full_name, is_phone_verified) are
    embedded in the token payload by set_auth_cookies() at login time.
    Only falls back to a DB fetch if those claims are somehow absent.
    """
    authentication_classes = []  # Manual cookie extraction — skip DRF middleware
    permission_classes = []

    def get(self, request):
        access_token = request.COOKIES.get('access_token')

        if not access_token:
            return Response(
                {'error': 'Unauthorized. No session cookie.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            from rest_framework_simplejwt.authentication import JWTAuthentication
            from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

            jwt_authenticator = JWTAuthentication()
            # Cryptographic signature verification — fast, no DB required
            validated_token = jwt_authenticator.get_validated_token(access_token)
            payload = validated_token.payload

            # Read custom claims embedded at login by set_auth_cookies()
            user_role = payload.get('user_role')
            full_name = payload.get('full_name')
            is_phone_verified = payload.get('is_phone_verified')
            user_id = payload.get('user_id')
            email = payload.get('email')

            # If any critical claim is missing, do a single targeted DB fetch
            if not user_role or email is None:
                user = jwt_authenticator.get_user(validated_token)
                user_role = user.role
                full_name = f"{user.first_name} {user.last_name}".strip() or user.username
                is_phone_verified = user.is_phone_verified
                user_id = user.id
                email = user.email

            return Response({
                'isAuthenticated': True,
                'user': {
                    'id': user_id,
                    'email': email,
                    'role': user_role,
                    'full_name': full_name,
                    'is_phone_verified': is_phone_verified,
                }
            }, status=status.HTTP_200_OK)

        except Exception:
            # Token expired, tampered with, or invalid
            return Response(
                {'error': 'Session expired or invalid.'},
                status=status.HTTP_401_UNAUTHORIZED
            )


class UserDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .utils import get_active_profile
        auth_user = request.user
        active_user = get_active_profile(request)
        
        # Always use the main account (parent) to list sub-accounts
        main_user = auth_user.parent_account if auth_user.parent_account else auth_user
        
        sub_accounts_data = [
            {
                "id": sub.id,
                "first_name": sub.first_name,
                "last_name": sub.last_name,
                "username": sub.username
            } for sub in main_user.sub_accounts.all()
        ]
        
        data = {
            "id": auth_user.id,
            "first_name": auth_user.first_name,
            "last_name": auth_user.last_name,
            "full_name": f"{auth_user.first_name} {auth_user.last_name}".strip() or auth_user.username,
            "username": auth_user.username,
            "email": auth_user.email,
            "phone": auth_user.phone_number,
            "role": auth_user.role,
            "is_onboarded": auth_user.is_onboarded,
            "is_phone_verified": auth_user.is_phone_verified,
            "sub_accounts": sub_accounts_data,
            "active_profile": {
                "id": active_user.id,
                "first_name": active_user.first_name,
                "last_name": active_user.last_name,
                "full_name": f"{active_user.first_name} {active_user.last_name}".strip() or active_user.username,
                "username": active_user.username,
                "role": active_user.role,
                "is_sub_account": active_user.id != auth_user.id,
            }
        }

        if auth_user.role == "CONSULTANT":
            try:
                profile = auth_user.consultant_service_profile
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
        if auth_user.role == "CLIENT":
            try:
                profile = auth_user.client_profile
                data["pan"] = profile.pan_number
                data["gst"] = profile.gstin
                
                from consultants.utils import get_active_consultant_for_client
                advisor = get_active_consultant_for_client(user)
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


class ActivateProfileView(APIView):
    """
    Netflix-style: Sets an HttpOnly 'active_profile' cookie for sub-account switching.
    POST /api/auth/profiles/activate/  { "profile_id": 5 }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile_id = request.data.get('profile_id')
        if not profile_id:
            return Response({'error': 'profile_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            profile_user = User.objects.get(id=profile_id)
        except User.DoesNotExist:
            return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Security: only allow switching to own sub-accounts
        is_own_subaccount = profile_user.parent_account == request.user
        is_self = profile_user == request.user
        if not (is_own_subaccount or is_self):
            return Response({'error': 'Unauthorized.'}, status=status.HTTP_403_FORBIDDEN)

        response = Response({
            'success': True,
            'active_profile': {
                'id': profile_user.id,
                'first_name': profile_user.first_name,
                'last_name': profile_user.last_name,
                'full_name': f"{profile_user.first_name} {profile_user.last_name}".strip() or profile_user.username,
                'username': profile_user.username,
                'is_sub_account': is_own_subaccount,
            }
        })

        # Set HttpOnly cookie — browser sends this automatically on every request
        response.set_cookie(
            key='active_profile',
            value=str(profile_user.id),
            httponly=True,
            secure=True,
            samesite='None',
            max_age=43200,  # 12 hours, like Netflix
        )
        return response


class DeactivateProfileView(APIView):
    """
    Clears the active_profile cookie, returning to the main account context.
    POST /api/auth/profiles/deactivate/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        response = Response({'success': True, 'message': 'Switched back to main account.'})
        response.delete_cookie('active_profile', samesite='None')
        return response


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
            from consultants.utils import get_active_consultant_for_client
            active_consultant = get_active_consultant_for_client(user)
            data = {
                'full_name': f"{user.first_name} {user.last_name}".strip(),
                'email': user.email,
                'phone_number': user.phone_number,
                'pan_number': profile.pan_number,
                'gstin': profile.gstin,
                'gst_username': profile.gst_username,
                'is_onboarded': user.is_onboarded,
                'assigned_consultant': active_consultant.get_full_name() if active_consultant else None,
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
        
        # Get clients assigned via ClientServiceRequest (active services only)
        from consultants.models import ClientServiceRequest
        service_clients = ClientServiceRequest.objects.filter(
            assigned_consultant__user=user
        ).exclude(status__in=['completed', 'cancelled']).values_list('client_id', flat=True)
        
        # Unique client IDs
        client_ids = set(service_clients)
        
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


from rest_framework.generics import CreateAPIView
from .models import ContactSubmission
from .serializers import ContactSubmissionSerializer

class ContactSubmissionView(CreateAPIView):
    """
    Public endpoint to submit contact/inquiry forms from the landing page.
    """
    queryset = ContactSubmission.objects.all()
    serializer_class = ContactSubmissionSerializer
    permission_classes = [AllowAny]
    authentication_classes = []


# =============================================================================
# Client Email/Password + Magic Link + Forgot Password Views
# =============================================================================

import uuid
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import authenticate
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from core_auth.models import MagicLinkToken

# ===========================================================================
# Custom email sender — bypasses Django's MIME building entirely to guarantee
# base64 encoding. Django's SafeMIMEText uses body_encoding=None for utf-8,
# causing SMTP servers to apply QP (wraps at 76 chars, corrupts URLs).
# ===========================================================================
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText as StdMIMEText
from django.core.mail import get_connection


def _send_base64_html_email(subject, html_body, plain_body, from_email, to_email):
    """Send email with base64-encoded HTML to prevent QP URL corruption."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email

    # Plain text part (QP is fine here — no clickable URLs)
    msg.attach(StdMIMEText(plain_body, 'plain', 'utf-8'))

    # HTML part — force base64 encoding so no line-wrapping can corrupt URLs
    html_b64 = base64.b64encode(html_body.encode('utf-8')).decode('ascii')
    html_part = StdMIMEText('', 'html', 'utf-8')
    del html_part['Content-Transfer-Encoding']
    html_part['Content-Transfer-Encoding'] = 'base64'
    html_part.set_payload(html_b64)
    msg.attach(html_part)

    # Send via Django's SMTP connection (uses EMAIL_HOST, EMAIL_USE_TLS, etc.)
    connection = get_connection()
    connection.open()
    try:
        connection.connection.sendmail(from_email, [to_email], msg.as_string())
    finally:
        connection.close()




def _generate_magic_token(user, purpose='LOGIN'):
    """Generate a secure, single-use token for magic links or password resets."""
    # Invalidate any existing unused tokens for same user + purpose
    MagicLinkToken.objects.filter(
        user=user, purpose=purpose, used=False
    ).update(used=True)

    token = uuid.uuid4().hex  # 32-char hex string — short enough to avoid email QP wrapping
    magic_token = MagicLinkToken.objects.create(
        user=user,
        token=token,
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES),
    )
    return magic_token


@method_decorator(csrf_exempt, name='dispatch')
class ClientRegisterView(APIView):
    """
    Register a new Client with email + password.
    POST /auth/client/register/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')
        first_name = request.data.get('first_name', '').strip()
        last_name = request.data.get('last_name', '').strip()

        # Validate required fields
        if not email or not password:
            return Response(
                {'error': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters long.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if email already exists
        if User.objects.filter(email=email).exists():
            existing_user = User.objects.get(email=email)
            if existing_user.role == User.CONSULTANT:
                return Response(
                    {
                        'error': 'This email is registered as a Consultant. Please use the consultant login.',
                        'code': 'EMAIL_CONFLICT'
                    },
                    status=status.HTTP_403_FORBIDDEN
                )
            # If user exists as client but has no password (created via magic link or Google)
            if not existing_user.has_usable_password():
                existing_user.set_password(password)
                if first_name:
                    existing_user.first_name = first_name
                if last_name:
                    existing_user.last_name = last_name
                existing_user.save()

                response = Response({
                    'success': True,
                    'created': False,
                    'message': 'Password set for existing account.',
                    'role': existing_user.role,
                })
                return set_auth_cookies(response, existing_user)

            return Response(
                {'error': 'An account with this email already exists. Please login instead.'},
                status=status.HTTP_409_CONFLICT
            )

        # Check consultant onboarding conflict
        try:
            from consultant_onboarding.models import ConsultantApplication
            if ConsultantApplication.objects.filter(
                email=email
            ).exclude(status='REJECTED').exists():
                return Response(
                    {
                        'error': 'This email is registered in our Consultant Onboarding portal. Please use a different email.',
                        'code': 'EMAIL_CONFLICT'
                    },
                    status=status.HTTP_403_FORBIDDEN
                )
        except Exception:
            pass

        # Create the user
        username = email.split('@')[0]
        # Ensure unique username
        base_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=User.CLIENT,
            is_phone_verified=False,
        )

        # Create ClientProfile
        ClientProfile.objects.create(user=user)

        logger.info(f"New client registered via email: {user.id} ({email})")

        response = Response({
            'success': True,
            'created': True,
            'role': user.role,
            'full_name': f"{user.first_name} {user.last_name}".strip() or user.username,
        }, status=status.HTTP_201_CREATED)
        return set_auth_cookies(response, user)


@method_decorator(csrf_exempt, name='dispatch')
class ClientEmailLoginView(APIView):
    """
    Login a Client with email + password.
    POST /auth/client/login/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')

        if not email or not password:
            return Response(
                {'error': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find user by email first
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {'error': 'No account found with this email. Please sign up first.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if user.role == User.CONSULTANT:
            return Response(
                {
                    'error': 'Consultants must use the consultant login section.',
                    'code': 'EMAIL_CONFLICT'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if not user.has_usable_password():
            return Response(
                {'error': 'This account was created via Google or Magic Link. Please use those methods to login, or set a password via "Forgot Password".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Authenticate
        authenticated_user = authenticate(username=user.username, password=password)
        if authenticated_user is None:
            return Response(
                {'error': 'Invalid password. Please try again.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        logger.info(f"Client email login: {user.id} ({email})")

        response = Response({
            'success': True,
            'role': user.role,
            'full_name': f"{user.first_name} {user.last_name}".strip() or user.username,
            'is_phone_verified': user.is_phone_verified,
        })
        return set_auth_cookies(response, authenticated_user)


@method_decorator(csrf_exempt, name='dispatch')
class SendMagicLinkView(APIView):
    """
    Send a magic link login email.
    POST /auth/magic-link/send/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email', '').strip().lower()

        if not email:
            return Response(
                {'error': 'Email is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Rate limit: max 1 magic link per email per 60 seconds
        recent_token = MagicLinkToken.objects.filter(
            user__email=email,
            purpose=MagicLinkToken.LOGIN,
            created_at__gte=timezone.now() - timedelta(seconds=60),
        ).first()
        if recent_token:
            return Response(
                {'error': 'A magic link was recently sent. Please wait a minute before requesting another.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Find or create user
        try:
            user = User.objects.get(email=email)
            if user.role == User.CONSULTANT:
                return Response(
                    {'error': 'Consultants must use username and password to login.'},
                    status=status.HTTP_403_FORBIDDEN
                )
        except User.DoesNotExist:
            # Check consultant onboarding conflict
            try:
                from consultant_onboarding.models import ConsultantApplication
                if ConsultantApplication.objects.filter(
                    email=email
                ).exclude(status='REJECTED').exists():
                    return Response(
                        {
                            'error': 'This email is registered in our Consultant Onboarding portal. Please use a different email.',
                            'code': 'EMAIL_CONFLICT'
                        },
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Exception:
                pass

            # Auto-create client account (no password)
            username = email.split('@')[0]
            base_username = username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            user = User.objects.create(
                username=username,
                email=email,
                role=User.CLIENT,
                is_phone_verified=False,
            )
            ClientProfile.objects.create(user=user)
            logger.info(f"Auto-created client via magic link: {user.id} ({email})")

        # Generate token
        magic_token = _generate_magic_token(user, purpose='LOGIN')

        # Build magic link URL — use path param to avoid QP encoding corrupting '=' in ?token=
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:8080')
        magic_link = f"{frontend_url}/auth/magic-link/verify/{magic_token.token}"

        # Send email
        try:
            subject = 'Your TaxPlan Advisor Login Link'
            html_message = render_to_string('core_auth/magic_link_email.html', {
                'user': user,
                'magic_link': magic_link,
                'expiry_minutes': MAGIC_LINK_EXPIRY_MINUTES,
            })
            plain_message = strip_tags(html_message)

            _send_base64_html_email(
                subject=subject,
                html_body=html_message,
                plain_body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to_email=email,
            )

            logger.info(f"Magic link sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send magic link to {email}: {e}")
            return Response(
                {'error': 'Failed to send email. Please try again later.'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        return Response({
            'success': True,
            'message': 'Magic link sent! Check your email inbox.',
        })


@method_decorator(csrf_exempt, name='dispatch')
class VerifyMagicLinkView(APIView):
    """
    Verify a magic link token and log the user in.
    POST /auth/magic-link/verify/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token', '').strip()

        if not token:
            return Response(
                {'error': 'Token is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            magic_token = MagicLinkToken.objects.select_related('user').get(
                token=token, purpose=MagicLinkToken.LOGIN
            )
        except MagicLinkToken.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired link. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not magic_token.is_valid:
            return Response(
                {'error': 'This link has expired or already been used. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Mark as used
        magic_token.used = True
        magic_token.save(update_fields=['used'])

        user = magic_token.user
        logger.info(f"Magic link verified for user {user.id} ({user.email})")

        response = Response({
            'success': True,
            'role': user.role,
            'full_name': f"{user.first_name} {user.last_name}".strip() or user.username,
            'is_phone_verified': user.is_phone_verified,
        })
        return set_auth_cookies(response, user)


@method_decorator(csrf_exempt, name='dispatch')
class ForgotPasswordView(APIView):
    """
    Send a password reset email.
    POST /auth/forgot-password/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        login_id = request.data.get('email', '').strip().lower()

        if not login_id:
            return Response(
                {'error': 'Email or username is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Always return success to prevent enumeration
        success_response = Response({
            'success': True,
            'message': 'If an account exists with this email, a password reset link has been sent.',
        })

        from django.db.models import Q
        try:
            # Check by email or username
            user = User.objects.get(Q(email=login_id) | Q(username=login_id))
        except User.DoesNotExist:
            return success_response

        # Allow consultants and clients, don't block consultants anymore
        # but ensure they have an email address to send to.
        if not user.email:
            # Cannot send reset link if no email is configured
            return success_response

        # Rate limit: max 1 reset per email per 60 seconds
        recent_token = MagicLinkToken.objects.filter(
            user=user,
            purpose=MagicLinkToken.PASSWORD_RESET,
            created_at__gte=timezone.now() - timedelta(seconds=60),
        ).first()
        if recent_token:
            return Response(
                {'error': 'A reset link was recently sent. Please wait a minute before requesting another.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Generate token
        magic_token = _generate_magic_token(user, purpose='PASSWORD_RESET')

        # Build reset URL — use path param to avoid QP encoding corrupting '=' in ?token=
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:8080')
        reset_link = f"{frontend_url}/auth/reset-password/{magic_token.token}"

        # Send email
        try:
            subject = 'Reset Your TaxPlan Advisor Password'
            html_message = render_to_string('core_auth/password_reset_email.html', {
                'user': user,
                'reset_link': reset_link,
                'expiry_minutes':MAGIC_LINK_EXPIRY_MINUTES ,
            })
            plain_message = strip_tags(html_message)

            _send_base64_html_email(
                subject=subject,
                html_body=html_message,
                plain_body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to_email=user.email,
            )
            logger.info(f"Password reset email sent to {user.email} (User {user.id})")
        except Exception as e:
            logger.error(f"Failed to send password reset email to {user.email}: {e}")
            return Response(
                {'error': 'Failed to send email. Please try again later.'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        return success_response


@method_decorator(csrf_exempt, name='dispatch')
class ResetPasswordView(APIView):
    """
    Reset password using a token from the reset email.
    POST /auth/reset-password/
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token', '').strip()
        new_password = request.data.get('new_password', '')

        logger.debug(f"[ResetPassword] token received: '{token}' (len={len(token)})")
        logger.debug(f"[ResetPassword] new_password present: {bool(new_password)}")

        if not token or not new_password:
            logger.warning(f"[ResetPassword] Missing token or password. token={bool(token)}, pw={bool(new_password)}")
            return Response(
                {'error': 'Token and new password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(new_password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters long.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            magic_token = MagicLinkToken.objects.select_related('user').get(
                token=token, purpose=MagicLinkToken.PASSWORD_RESET
            )
        except MagicLinkToken.DoesNotExist:
            # Log what tokens DO exist for debugging
            existing = list(MagicLinkToken.objects.filter(
                purpose=MagicLinkToken.PASSWORD_RESET, used=False
            ).values_list('token', flat=True))
            logger.error(f"[ResetPassword] Token not found in DB. Received='{token}'. Existing tokens: {existing}")
            return Response(
                {'error': 'Invalid or expired reset link. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST
            )


        if not magic_token.is_valid:
            return Response(
                {'error': 'This reset link has expired or already been used. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Mark as used
        magic_token.used = True
        magic_token.save(update_fields=['used'])

        # Set new password
        user = magic_token.user
        user.set_password(new_password)
        user.save(update_fields=['password'])

        logger.info(f"Password reset completed for user {user.id} ({user.email})")

        return Response({
            'success': True,
            'message': 'Password has been reset successfully. You can now login with your new password.',
        })


# ─── Sub-Account Management ─────────────────────────────────────────────────
from rest_framework.viewsets import ModelViewSet
from core_auth.serializers import SubAccountSerializer

class SubAccountViewSet(ModelViewSet):
    """
    ViewSet for managing family sub-accounts.
    - LIST: Returns sub-accounts owned by the authenticated user.
    - CREATE: Creates a new sub-account linked to the authenticated user.
    - UPDATE/PARTIAL_UPDATE: Edits a sub-account's name.
    - DELETE: Removes a sub-account.
    """
    serializer_class = SubAccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(parent_account=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
