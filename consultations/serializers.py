from rest_framework import serializers
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking
from django.contrib.auth import get_user_model

User = get_user_model()

class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ['id', 'name', 'description']

class WeeklyAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklyAvailability
        fields = ['id', 'day_of_week', 'start_time', 'end_time']

    def create(self, validated_data):
        validated_data['consultant'] = self.context['request'].user
        return super().create(validated_data)

class DateOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = DateOverride
        fields = ['id', 'date', 'is_unavailable', 'start_time', 'end_time']

    def create(self, validated_data):
        validated_data['consultant'] = self.context['request'].user
        return super().create(validated_data)

class ConsultationBookingSerializer(serializers.ModelSerializer):
    consultant_name = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    topic_name = serializers.CharField(source='topic.name', read_only=True)
    
    class Meta:
        model = ConsultationBooking
        fields = [
            'id', 'consultant', 'consultant_name', 'client', 'client_name',
            'topic', 'topic_name', 'booking_date', 'start_time', 'end_time',
            'notes', 'status', 'created_at'
        ]
        read_only_fields = ['client', 'created_at']
    
    def get_consultant_name(self, obj):
        return f"{obj.consultant.first_name} {obj.consultant.last_name}".strip() or obj.consultant.username
    
    def get_client_name(self, obj):
        return f"{obj.client.first_name} {obj.client.last_name}".strip() or obj.client.username
    
    def create(self, validated_data):
        validated_data['client'] = self.context['request'].user
        return super().create(validated_data)
