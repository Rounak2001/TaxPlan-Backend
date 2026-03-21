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


from rest_framework import serializers
from .models import ContactSubmission

class ContactSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactSubmission
        fields = ['full_name', 'email', 'phone', 'inquiry_type', 'message']

from django.contrib.auth import get_user_model
User = get_user_model()

class SubAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'username']
        read_only_fields = ['id', 'username']

    def create(self, validated_data):
        import uuid
        request = self.context.get('request')
        
        # Auto-generate a username
        # e.g., ritesh_sub_abc12
        base_username = request.user.username.split('@')[0]
        random_suffix = uuid.uuid4().hex[:5]
        username = f"{base_username}_sub_{random_suffix}"
        
        validated_data['username'] = username
        validated_data['parent_account'] = request.user
        validated_data['role'] = User.CLIENT
        
        # Create user with unusable password
        user = User(**validated_data)
        user.set_unusable_password()
        user.save()
        
        # Create ClientProfile for the sub-account
        from .models import ClientProfile
        ClientProfile.objects.create(user=user)
        
        return user
