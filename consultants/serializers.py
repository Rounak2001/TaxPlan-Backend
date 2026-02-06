from rest_framework import serializers
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest
)


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'description', 'is_active']


class ServiceSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Service
        fields = ['id', 'category', 'category_name', 'title', 'price', 'tat', 'documents_required', 'is_active']


class ConsultantServiceProfileSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = ConsultantServiceProfile
        fields = [
            'id', 'user', 'user_email', 'full_name', 'email', 'phone',
            'qualification', 'experience_years', 'certifications',
            'is_active', 'max_concurrent_clients', 'current_client_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['current_client_count', 'created_at', 'updated_at']


class ConsultantServiceExpertiseSerializer(serializers.ModelSerializer):
    service_title = serializers.CharField(source='service.title', read_only=True)
    service_category = serializers.CharField(source='service.category.name', read_only=True)
    
    class Meta:
        model = ConsultantServiceExpertise
        fields = ['id', 'consultant', 'service', 'service_title', 'service_category', 'added_at']
        read_only_fields = ['added_at']


class ClientServiceRequestSerializer(serializers.ModelSerializer):
    # Nested serializers for full object data
    service = ServiceSerializer(read_only=True)
    assigned_consultant = ConsultantServiceProfileSerializer(read_only=True)
    
    # Flat fields for backward compatibility
    client_email = serializers.EmailField(source='client.email', read_only=True)
    
    class Meta:
        model = ClientServiceRequest
        fields = [
            'id', 'client', 'client_email', 
            'service',  # Full service object
            'status', 
            'assigned_consultant',  # Full consultant object
            'assigned_at', 'notes', 'priority',
            'created_at', 'updated_at', 'completed_at'
        ]
        read_only_fields = ['assigned_consultant', 'assigned_at', 'created_at', 'updated_at', 'completed_at']


class ConsultantDashboardSerializer(serializers.Serializer):
    """Serializer for consultant dashboard data"""
    profile = ConsultantServiceProfileSerializer()
    services = ServiceSerializer(many=True)
    assigned_requests = ClientServiceRequestSerializer(many=True)
    stats = serializers.DictField()
