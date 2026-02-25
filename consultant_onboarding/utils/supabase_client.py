from supabase import create_client, Client
from django.conf import settings

def get_supabase_client() -> Client:
    url: str = settings.SUPABASE_URL
    if not url.endswith('/'):
        url += '/'
    key: str = settings.SUPABASE_KEY
    return create_client(url, key)
