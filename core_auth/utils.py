from django.contrib.auth import get_user_model

User = get_user_model()

def get_active_profile(request):
    """
    Netflix-style profile switching via HttpOnly cookie.
    Reads the 'active_profile' cookie set by the /api/auth/profiles/activate/ endpoint.
    Falls back to request.user (main account) if cookie is absent or invalid.
    """
    profile_id = (
        request.COOKIES.get('active_profile') or
        request.query_params.get('profile_id') or   # fallback during transition
        request.GET.get('profile_id')
    )
    
    if not profile_id:
        return request.user
    
    try:
        active_user = User.objects.get(id=profile_id)
        # Security: only allow if it's the logged-in user themselves,
        # or a direct sub-account (parent_account == logged-in user)
        if active_user == request.user or active_user.parent_account == request.user:
            return active_user
    except User.DoesNotExist:
        pass
    
    return request.user
