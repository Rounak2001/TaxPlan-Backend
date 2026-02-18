from rest_framework import serializers
from .models import Notification
from django.utils import timezone
from datetime import timedelta

class NotificationSerializer(serializers.ModelSerializer):
    time_ago = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id',
            'recipient',
            'category',
            'title',
            'message',
            'link',
            'is_read',
            'created_at',
            'time_ago',
        ]
        read_only_fields = ['id', 'created_at', 'recipient']

    def get_time_ago(self, obj):
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(minutes=1):
            return "Just now"
        elif diff < timedelta(hours=1):
            minutes = int(diff.total_seconds() / 60)
            return f"{minutes} min ago"
        elif diff < timedelta(days=1):
            hours = int(diff.total_seconds() / 3600)
            return f"{hours} h ago"
        elif diff < timedelta(days=7):
            days = diff.days
            return f"{days} d ago"
        else:
            return obj.created_at.strftime('%b %d')
