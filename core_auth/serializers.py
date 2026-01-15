from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import permissions

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token['user_role'] = user.role
        token['full_name'] = f"{user.first_name} {user.last_name}".strip() or user.username
        token['is_phone_verified'] = user.is_phone_verified

        return token

class IsConsultantUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'CONSULTANT')

class IsClientUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'CLIENT')
