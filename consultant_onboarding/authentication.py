import jwt
import datetime
from django.conf import settings
from rest_framework import authentication
from rest_framework import exceptions
from rest_framework.permissions import BasePermission
from .models import ConsultantApplication

def generate_applicant_token(application):
    """
    Generates a JWT token for the applicant.
    This is separate from the main app's token system.
    """
    payload = {
        'application_id': application.id,
        'email': application.email,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        'iat': datetime.datetime.utcnow(),
        'type': 'applicant_token'
    }
    
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')
    return token


class ApplicantAuthentication(authentication.BaseAuthentication):
    """
    Custom authentication for applicants using the onboarding portal.

    Reads the applicant_token cookie (or Bearer header) and sets request.application.
    Returns None if no applicant_token is found, letting other auth classes run.
    """
    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        
        # Check cookie first, then auth header
        token = request.COOKIES.get('applicant_token')
        
        if not token and auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            
        if not token:
            return None
            
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Applicant token has expired')
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed('Invalid applicant token')
            
        if payload.get('type') != 'applicant_token':
            return None
            
        try:
            application = ConsultantApplication.objects.get(id=payload['application_id'])
        except ConsultantApplication.DoesNotExist:
            raise exceptions.AuthenticationFailed('Application not found')
            
        request.application = application
        
        class MockApplicantUser:
            is_authenticated = True
            application = application
            id = application.id
            email = application.email
        
        return (MockApplicantUser(), token)


class IsApplicant(BasePermission):
    """
    Allows access only to authenticated applicants.

    Works with both:
    - applicant_token cookie/header → request.application set by ApplicantAuthentication
    - Main SaaS User JWT → looks up ConsultantApplication by the user's email as a fallback.
      This lets the aliased endpoints at /api/auth/ work when the user signed in via
      the main Google auth flow rather than the dedicated onboarding flow.
    """
    def has_permission(self, request, view):
        # Fast path: applicant_token auth already resolved the application
        if hasattr(request, 'application') and request.application is not None:
            return True
        
        # Fallback: main SaaS JWT authenticated user – find their ConsultantApplication by email
        if hasattr(request, 'user') and request.user and request.user.is_authenticated:
            try:
                application = ConsultantApplication.objects.get(email=request.user.email)
                request.application = application
                return True
            except ConsultantApplication.DoesNotExist:
                pass
        
        return False
