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
        path = getattr(request, 'path', '') or ''
        onboarding_prefixes = (
            '/api/onboarding/',
            '/api/auth/onboarding/',
            '/api/auth/profile/',
            '/api/auth/accept-declaration/',
            '/api/auth/logout/',
            '/api/auth/documents/',
            '/api/auth/identity/',
            '/api/documents/',
            '/api/face-verification/',
            '/api/assessment/',
            '/api/admin-panel/',
            '/api/health/',
        )
        if not any(path.startswith(prefix) for prefix in onboarding_prefixes):
            return None

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

        # Use SimpleNamespace instead of an inline class to avoid Python's
        # class-body scoping issue: inside a class body, `application = application`
        # looks up 'application' in class scope first (where it doesn't exist yet),
        # causing: NameError: name 'application' is not defined.
        import types
        mock_user = types.SimpleNamespace(
            is_authenticated=True,
            application=application,
            id=application.id,
            email=application.email,
        )

        return (mock_user, token)


class IsApplicant(BasePermission):
    """
    Allows access only to authenticated applicants.

    ONLY accepts applicant_token (cookie or Bearer header) resolved by
    ApplicantAuthentication.  The main SaaS JWT is intentionally NOT
    accepted here — the two auth systems must stay completely separate
    so that being logged into the main SaaS app doesn't silently
    cross-authenticate into the onboarding portal.
    """
    def has_permission(self, request, view):
        return hasattr(request, 'application') and request.application is not None
