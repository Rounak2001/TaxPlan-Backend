from django.contrib.auth import get_user_model

User = get_user_model()


def _coerce_real_user(candidate):
    if isinstance(candidate, User):
        return candidate

    candidate_id = getattr(candidate, 'id', None)
    try:
        user_id = int(candidate_id) if candidate_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    if user_id:
        user = User.objects.filter(id=user_id).first()
        if user:
            return user

    email = getattr(candidate, 'email', None)
    if email:
        return User.objects.filter(email=email).first()

    return None

def get_active_profile(request):
    """
    Netflix-style profile switching via HttpOnly cookie.
    Reads the 'active_profile' cookie set by the /api/auth/profiles/activate/ endpoint.
    Falls back to request.user (main account) if cookie is absent or invalid.
    """
    request_user = _coerce_real_user(getattr(request, 'user', None)) or getattr(request, 'user', None)
    profile_id = (
        request.COOKIES.get('active_profile') or
        request.query_params.get('profile_id') or   # fallback during transition
        request.GET.get('profile_id')
    )
    
    if not profile_id:
        return request_user
    
    try:
        active_user = User.objects.get(id=profile_id)
        owner_user = _coerce_real_user(getattr(request, 'user', None))
        # Security: only allow if it's the logged-in user themselves,
        # or a direct sub-account (parent_account == logged-in user)
        if owner_user and (active_user == owner_user or active_user.parent_account_id == owner_user.id):
            return active_user
        if active_user == request_user or active_user.parent_account == request_user:
            return active_user
    except User.DoesNotExist:
        pass
    
    return request_user


def resolve_authenticated_user(request):
    """
    Resolve the current authenticated actor to a real User model instance.
    Handles dev/onboarding auth objects (e.g. SimpleNamespace) by falling back
    to id/email lookups.
    """
    return _coerce_real_user(get_active_profile(request)) or _coerce_real_user(getattr(request, 'user', None))


def resolve_authenticated_user_id(request):
    user = resolve_authenticated_user(request)
    return getattr(user, 'id', None)
