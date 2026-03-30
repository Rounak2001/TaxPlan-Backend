from rest_framework import serializers
from django.db.utils import OperationalError, ProgrammingError
from .models import Document, SharedReport, LegalNotice, Folder, DocumentAccess

class FolderSerializer(serializers.ModelSerializer):
    created_by_name = serializers.ReadOnlyField(source='created_by.get_full_name')
    document_count = serializers.SerializerMethodField()
    verified_count = serializers.SerializerMethodField()
    unverified_count = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = ['id', 'client', 'name', 'created_by', 'created_by_name', 'is_system', 'created_at', 'document_count', 'verified_count', 'unverified_count']
        read_only_fields = ['client', 'created_by', 'is_system', 'created_at']

    def get_document_count(self, obj):
        return obj.documents.count()

    def get_verified_count(self, obj):
        return obj.documents.filter(status='VERIFIED').count()

    def get_unverified_count(self, obj):
        return obj.documents.exclude(status='VERIFIED').count()

class DocumentSerializer(serializers.ModelSerializer):
    client_name = serializers.ReadOnlyField(source='client.get_full_name')
    consultant_name = serializers.ReadOnlyField(source='consultant.get_full_name')
    folder_name = serializers.ReadOnlyField(source='folder.name')
    granted_consultant_ids = serializers.SerializerMethodField()
    has_access = serializers.SerializerMethodField()
    
    class Meta:
        model = Document
        fields = [
            'id', 'client', 'client_name', 'consultant', 'consultant_name',
            'folder', 'folder_name', 'title', 'description', 'file', 'file_password', 'status', 
            'created_at', 'uploaded_at', 'granted_consultant_ids', 'has_access'
        ]
        read_only_fields = ['client', 'consultant', 'status', 'created_at', 'uploaded_at']

    def get_granted_consultant_ids(self, obj):
        try:
            return list(obj.access_grants.values_list('consultant_id', flat=True))
        except (ProgrammingError, OperationalError):
            # Graceful fallback if migration for vault_document_access is not applied yet.
            return []

    def get_has_access(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.role != 'CONSULTANT':
            return True
        try:
            return obj.access_grants.filter(consultant_id=user.id).exists()
        except (ProgrammingError, OperationalError):
            return False

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)

        # For consultants without access grant, expose only high-level metadata.
        if user and user.is_authenticated and user.role == 'CONSULTANT' and not data.get('has_access', False):
            data['file'] = None
            data['file_password'] = None
            data['description'] = None
            data['granted_consultant_ids'] = []

        return data

class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['file', 'file_password']
        extra_kwargs = {
            'file': {'required': True}
        }


class SharedReportSerializer(serializers.ModelSerializer):
    client_name = serializers.ReadOnlyField(source='client.get_full_name')
    consultant_name = serializers.ReadOnlyField(source='consultant.get_full_name')
    
    class Meta:
        model = SharedReport
        fields = [
            'id', 'consultant', 'consultant_name', 'client', 'client_name',
            'title', 'description', 'file', 'report_type', 'is_read', 'created_at'
        ]
        read_only_fields = ['consultant', 'created_at']


class LegalNoticeSerializer(serializers.ModelSerializer):
    client_name = serializers.ReadOnlyField(source='client.get_full_name')
    consultant_name = serializers.ReadOnlyField(source='consultant.get_full_name')
    uploaded_by_name = serializers.ReadOnlyField(source='uploaded_by.get_full_name')
    
    class Meta:
        model = LegalNotice
        fields = [
            'id', 'client', 'client_name', 'consultant', 'consultant_name',
            'title', 'description', 'file', 'source', 'notice_type', 
            'priority', 'uploaded_by', 'uploaded_by_name', 'due_date', 
            'is_resolved', 'created_at'
        ]
        read_only_fields = ['consultant', 'uploaded_by', 'created_at']
