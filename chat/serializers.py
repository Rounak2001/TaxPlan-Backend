"""
Serializers for Chat models.
"""

from rest_framework import serializers
from .models import Conversation, Message
from django.contrib.auth import get_user_model

User = get_user_model()


class UserMinimalSerializer(serializers.ModelSerializer):
    """Minimal user info for chat context."""
    full_name = serializers.ReadOnlyField(source='get_full_name')
    
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'full_name', 'role']


class MessageSerializer(serializers.ModelSerializer):
    """Serializer for Message model."""
    sender = UserMinimalSerializer(read_only=True)
    sender_id = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = ['id', 'conversation', 'sender', 'sender_id', 'content', 'timestamp', 'is_read']
        read_only_fields = ['id', 'timestamp']
    
    def get_sender_id(self, obj):
        """Return sender's ID for message alignment."""
        return obj.sender_id


class ConversationSerializer(serializers.ModelSerializer):
    """Serializer for Conversation model with last message preview."""
    consultant = UserMinimalSerializer(read_only=True)
    client = UserMinimalSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Conversation
        fields = ['id', 'consultant', 'client', 'created_at', 'updated_at', 'last_message', 'unread_count']
    
    def get_last_message(self, obj):
        """Get the most recent message in the conversation."""
        last_msg = obj.messages.order_by('-timestamp').first()
        if last_msg:
            return {
                'content': last_msg.content[:100],  # Truncate for preview
                'timestamp': last_msg.timestamp.isoformat(),
                'sender_id': last_msg.sender_id,
            }
        return None
    
    def get_unread_count(self, obj):
        """Get count of unread messages for the current user."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.messages.filter(is_read=False).exclude(sender=request.user).count()
        return 0


class ConversationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new conversation."""
    client_id = serializers.IntegerField(write_only=True, required=False)
    consultant_id = serializers.IntegerField(write_only=True, required=False)
    
    class Meta:
        model = Conversation
        fields = ['id', 'client_id', 'consultant_id', 'created_at']
        read_only_fields = ['id', 'created_at']
