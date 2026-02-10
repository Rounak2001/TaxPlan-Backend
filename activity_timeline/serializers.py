from rest_framework import serializers
from .models import Activity
from django.contrib.auth import get_user_model

User = get_user_model()


class ActivitySerializer(serializers.ModelSerializer):
    """Serializer for Activity model with user details"""
    
    actor_name = serializers.SerializerMethodField()
    target_user_name = serializers.SerializerMethodField()
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    time_ago = serializers.SerializerMethodField()
    
    class Meta:
        model = Activity
        fields = [
            'id',
            'actor',
            'actor_name',
            'target_user',
            'target_user_name',
            'activity_type',
            'activity_type_display',
            'title',
            'description',
            'metadata',
            'created_at',
            'time_ago',
            'content_type',
            'object_id',
        ]
        read_only_fields = fields
    
    def get_actor_name(self, obj):
        """Get actor's full name or username"""
        return obj.actor.get_full_name() or obj.actor.username
    
    def get_target_user_name(self, obj):
        """Get target user's full name or username"""
        return obj.target_user.get_full_name() or obj.target_user.username
    
    def get_time_ago(self, obj):
        """Return a human-readable time difference"""
        from django.utils import timezone
        from datetime import timedelta
        
        now = timezone.now()
        diff = now - obj.created_at
        
        if diff < timedelta(minutes=1):
            return "Just now"
        elif diff < timedelta(hours=1):
            minutes = int(diff.total_seconds() / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif diff < timedelta(days=1):
            hours = int(diff.total_seconds() / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif diff < timedelta(days=7):
            days = diff.days
            return f"{days} day{'s' if days != 1 else ''} ago"
        else:
            return obj.created_at.strftime('%b %d, %Y')
