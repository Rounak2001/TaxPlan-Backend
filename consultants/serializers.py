import re

from rest_framework import serializers
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest,
    ConsultantReview
)
from service_orders.models import OrderItem


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'description', 'is_active']


class ServiceSerializer(serializers.ModelSerializer):
    category = ServiceCategorySerializer(read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Service
        fields = ['id', 'category', 'category_name', 'title', 'price', 'tat', 'documents_required', 'is_active']


class ConsultantServiceProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    email = serializers.EmailField(source='user.email', read_only=True)
    phone = serializers.CharField(source='user.phone_number', read_only=True)
    age = serializers.IntegerField(source='application.age', read_only=True, allow_null=True)
    dob = serializers.DateField(source='application.dob', read_only=True, allow_null=True)
    address_line1 = serializers.CharField(source='application.address_line1', required=False)
    address_line2 = serializers.CharField(source='application.address_line2', required=False, allow_blank=True)
    city = serializers.CharField(source='application.city', required=False)
    state = serializers.CharField(source='application.state', required=False)
    pincode = serializers.CharField(source='application.pincode', required=False)
    practice_type = serializers.CharField(source='application.practice_type', read_only=True, allow_blank=True, allow_null=True)
    
    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def validate_pan_number(self, value):
        if value in (None, ''):
            return ''
        normalized = str(value).strip().upper()
        if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', normalized):
            raise serializers.ValidationError('PAN must be in valid format (e.g. ABCDE1234F).')
        return normalized

    def validate_gstin(self, value):
        if value in (None, ''):
            return ''
        normalized = str(value).strip().upper()
        if not re.match(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$', normalized):
            raise serializers.ValidationError('GSTIN must be a valid 15-character GSTIN.')
        return normalized

    def validate_address_line1(self, value):
        return value.strip()

    def validate_address_line2(self, value):
        return value.strip()

    def validate_city(self, value):
        return value.strip()

    def validate_state(self, value):
        return value.strip()

    def validate_pincode(self, value):
        return value.strip()

    def update(self, instance, validated_data):
        application_data = validated_data.pop('application', {})

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if validated_data:
            instance.save()

        if application_data:
            application = instance.application
            if not application:
                raise serializers.ValidationError({
                    'detail': 'Linked onboarding application not found for this consultant.',
                })

            for attr, value in application_data.items():
                setattr(application, attr, value)
            application.save()
            instance._application_cache = application

        return instance
    
    class Meta:
        model = ConsultantServiceProfile
        fields = [
            'id', 'user', 'full_name', 'email', 'phone',
            'age', 'dob',
            'address_line1', 'address_line2', 'city', 'state', 'pincode',
            'practice_type',
            'qualification', 'experience_years', 'certifications', 'pan_number', 'gstin',
            'bio', 'consultation_fee',
            'is_active', 'max_concurrent_clients', 'current_client_count',
            'average_rating', 'total_reviews',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'user', 'full_name', 'email', 'phone',
            'age', 'dob', 'practice_type',
            'current_client_count', 'average_rating', 'total_reviews',
            'created_at', 'updated_at',
        ]


class ConsultantReviewSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()
    client_email = serializers.EmailField(source='client.email', read_only=True)
    
    def get_client_name(self, obj):
        return obj.client.get_full_name() or obj.client.username
        
    class Meta:
        model = ConsultantReview
        fields = ['id', 'consultant', 'client', 'client_name', 'client_email', 'service_request', 'rating', 'review_text', 'created_at']
        read_only_fields = ['consultant', 'client', 'created_at']



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
    client_name = serializers.SerializerMethodField()
    order_variant_name = serializers.SerializerMethodField()
    
    def get_client_name(self, obj):
        return obj.client.get_full_name() or obj.client.username

    def get_order_variant_name(self, obj):
        match = re.search(r'order #(\d+)', obj.notes or '')
        if not match:
            return ''

        order_id = match.group(1)
        items = OrderItem.objects.filter(order_id=order_id)

        if obj.service_id:
            matched_item = items.filter(service_id=obj.service_id).order_by('-id').first()
            if matched_item:
                return matched_item.variant_name or ''

        service_title = getattr(obj.service, 'title', '')
        if service_title:
            matched_item = items.filter(service_title=service_title).order_by('-id').first()
            if matched_item:
                return matched_item.variant_name or ''

        fallback_item = items.order_by('-id').first()
        return fallback_item.variant_name or '' if fallback_item else ''
    
    class Meta:
        model = ClientServiceRequest
        fields = [
            'id', 'client', 'client_email', 'client_name', 
            'service',  # Full service object
            'status', 
            'assigned_consultant',  # Full consultant object
            'order_variant_name',
            'assigned_at', 'notes', 'revision_notes', 'priority',
            'has_review',
            'created_at', 'updated_at', 'completed_at'
        ]
        read_only_fields = ['assigned_consultant', 'assigned_at', 'created_at', 'updated_at', 'completed_at']

    has_review = serializers.SerializerMethodField()
    
    def get_has_review(self, obj):
        return hasattr(obj, 'review')


class ConsultantDashboardSerializer(serializers.Serializer):
    """Serializer for consultant dashboard data"""
    profile = ConsultantServiceProfileSerializer()
    services = ServiceSerializer(many=True)
    assigned_requests = ClientServiceRequestSerializer(many=True)
    stats = serializers.DictField()
