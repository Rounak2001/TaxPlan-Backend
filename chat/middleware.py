"""
JWT Authentication Middleware for Django Channels WebSocket connections.
Authenticates users via JWT token passed as query parameter.
"""

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from urllib.parse import parse_qs
import logging

logger = logging.getLogger(__name__)


@database_sync_to_async
def get_user_from_token(token_str):
    """
    Validate JWT token and return the associated user.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    try:
        token = AccessToken(token_str)
        user_id = token.payload.get('user_id')
        if user_id:
            return User.objects.get(id=user_id)
    except (InvalidToken, TokenError) as e:
        logger.warning(f"WebSocket Token Error: {e}")
    except User.DoesNotExist:
        logger.warning(f"WebSocket User Does Not Exist for token")
    except Exception as e:
        logger.error(f"WebSocket Unexpected Auth Error: {e}")
    
    return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    Custom middleware that authenticates WebSocket connections using JWT.
    Token should be passed as a query parameter: ws://...?token=<jwt_token>
    """
    
    async def __call__(self, scope, receive, send):
        # Parse query string for token
        query_string = scope.get('query_string', b'').decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get('token', [])
        
        if token_list:
            token = token_list[0]
            user = await get_user_from_token(token)
            scope['user'] = user
            logger.info(f"WebSocket Auth: user={getattr(user, 'username', 'anonymous')}, authenticated={user.is_authenticated}")
        else:
            scope['user'] = AnonymousUser()
            logger.warning("WebSocket Connection attempt without token")
        
        return await super().__call__(scope, receive, send)
