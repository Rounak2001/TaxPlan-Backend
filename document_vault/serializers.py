from rest_framework import serializers
from .models import Document, SharedReport

class DocumentSerializer(serializers.ModelSerializer):
    client_name = serializers.ReadOnlyField(source='client.get_full_name')
    consultant_name = serializers.ReadOnlyField(source='consultant.get_full_name')
    
    class Meta:
        model = Document
        fields = [
            'id', 'client', 'client_name', 'consultant', 'consultant_name',
            'title', 'description', 'file', 'status', 
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
