from django.http import JsonResponse
from django.urls import resolve

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
