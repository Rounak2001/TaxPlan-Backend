"""
Django PRODUCTION settings for TaxPlanAdvisor SaaS.
Import this in settings.py when deploying to production.

Usage in settings.py:
    # At the bottom of settings.py
    import os
    if os.getenv('DJANGO_ENV') == 'production':
        from .settings_prod import *
"""

import os
from pathlib import Path

# =============================================================================
# SECURITY SETTINGS
# =============================================================================

DEBUG = False

# IMPORTANT: Set these in your .env file
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']  # Required - will error if not set

# Add your domain(s) here - includes frontend for WebSocket origin validation
ALLOWED_HOSTS = [
    os.getenv('ALLOWED_HOST', 'main.taxplanadvisor.co'),
    'taxplanadvisor.vercel.app',  # Frontend for WebSocket origin validation
    'localhost',
    '127.0.0.1',
]

# HTTPS/SSL Settings
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS Settings (commented out initially - enable after confirming HTTPS works)
# SECURE_HSTS_SECONDS = 31536000  # 1 year
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True

# Cookie Settings for Cross-Origin (Vercel frontend + EC2 backend)
SESSION_COOKIE_SAMESITE = 'None'
CSRF_COOKIE_SAMESITE = 'None'

# =============================================================================
# CORS SETTINGS
# =============================================================================

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    os.getenv('FRONTEND_URL', 'https://taxplanadvisor.vercel.app'),
]

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# =============================================================================
# DATABASE (PostgreSQL for Production)
# =============================================================================
import dj_database_url
import os

DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL'),
        conn_max_age=600
    )
}


# =============================================================================
# REDIS / CHANNEL LAYERS (for WebSocket)
# =============================================================================

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [os.environ['REDIS_URL']],
            'capacity': 1500,
            'expiry': 10,
        },
    },
}

# =============================================================================
# STATIC & MEDIA FILES
# =============================================================================

# Static files - served by Nginx or Whitenoise
STATIC_URL = '/static/'
STATIC_ROOT = '/var/www/taxplanadvisor/static/'

# Media files - already using S3, no changes needed
# AWS credentials from environment (.env)

# =============================================================================
# JWT SETTINGS FOR PRODUCTION
# =============================================================================

from datetime import timedelta

SIMPLE_JWT = {
    'SIGNING_KEY': os.environ['JWT_SIGNING_KEY'],
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),  # Shorter for production
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'TOKEN_OBTAIN_SERIALIZER': 'core_auth.serializers.CustomTokenObtainPairSerializer',
    
    # Cookie settings for HttpOnly tokens
    'AUTH_COOKIE': 'access_token',
    'AUTH_COOKIE_SECURE': True,
    'AUTH_COOKIE_HTTP_ONLY': True,
    'AUTH_COOKIE_SAMESITE': 'None',
}

# =============================================================================
# LOGGING
# =============================================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': '/var/log/taxplanadvisor/django.log',
            'formatter': 'verbose',
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['file', 'console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'WARNING',
            'propagate': True,
        },
        'chat': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# =============================================================================
# EMAIL - Already configured with Gmail SMTP
# =============================================================================

# No changes needed, using os.getenv() already

# =============================================================================
# CACHING (Optional - add if needed)
# =============================================================================

# CACHES = {
#     'default': {
#         'BACKEND': 'django.core.cache.backends.redis.RedisCache',
#         'LOCATION': os.environ['REDIS_URL'],
#     }
# }
