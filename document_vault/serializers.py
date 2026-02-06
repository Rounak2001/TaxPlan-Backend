from rest_framework import serializers
from .models import Document, SharedReport, LegalNotice, Folder

class FolderSerializer(serializers.ModelSerializer):
    created_by_name = serializers.ReadOnlyField(source='created_by.get_full_name')
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = ['id', 'client', 'name', 'created_by', 'created_by_name', 'is_system', 'created_at', 'document_count']
        read_only_fields = ['client', 'created_by', 'is_system', 'created_at']

    def get_document_count(self, obj):
        return obj.documents.count()

class DocumentSerializer(serializers.ModelSerializer):
    client_name = serializers.ReadOnlyField(source='client.get_full_name')
    consultant_name = serializers.ReadOnlyField(source='consultant.get_full_name')
    folder_name = serializers.ReadOnlyField(source='folder.name')
    
    class Meta:
        model = Document
        fields = [
            'id', 'client', 'client_name', 'consultant', 'consultant_name',
            'folder', 'folder_name', 'title', 'description', 'file', 'status', 
            'created_at', 'uploaded_at'
        ]
        read_only_fields = ['client', 'consultant', 'status', 'created_at', 'uploaded_at']

class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['file']
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
            'title', 'description', 'file', 'report_type', 'created_at'
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
