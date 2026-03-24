from django.http import JsonResponse
from django.urls import resolve


class DisableCSRFForAPIMiddleware:
    """
    Disables CSRF enforcement for all /api/ routes.

    This is safe because:
    1. Authentication is done via HttpOnly JWT cookies (not session cookies)
       — so CSRF is no longer the right protection mechanism.
    2. CORS is configured to only allow trusted origins.
    3. @method_decorator(csrf_exempt) is unreliable under Daphne/ASGI;
       middleware-level exemption is the authoritative approach.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            setattr(request, '_dont_enforce_csrf_checks', True)
        return self.get_response(request)


class PreOnboardingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip phone verification for admin URLs and superusers
        if request.path.startswith('/admin/'):
            return self.get_response(request)
        
        if hasattr(request, 'user') and request.user.is_authenticated:
            # Skip for superusers/staff
            if request.user.is_superuser or request.user.is_staff:
                return self.get_response(request)
            
            if not request.user.is_phone_verified:
                # List of allowed paths for unverified users
                allowed_urls = [
                    'send-otp',
                    'verify-otp',
                    'token_obtain_pair',
                    'token_refresh',
                    'google-auth',
                    'client-profile',
                ]
                
                # Resolve the current URL name
                try:
                    current_url_name = resolve(request.path_info).url_name
                except Exception:
                    current_url_name = None

                if current_url_name not in allowed_urls:
                    return JsonResponse(
                        {'error': 'Phone verification required.', 'code': 'phone_unverified'}, 
                        status=403
                    )

        response = self.get_response(request)
        return response

