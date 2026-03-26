from rest_framework import serializers
from .models import Ticket, TicketComment

class TicketCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source='author.full_name', read_only=True)
    author_email = serializers.CharField(source='author.email', read_only=True)

    class Meta:
        model = TicketComment
        fields = [
            'id', 'ticket', 'author', 'author_name', 'author_email',
            'is_admin_reply', 'message', 'attachment', 'created_at'
        ]
        read_only_fields = ['id', 'ticket', 'author', 'is_admin_reply', 'created_at']

class TicketSerializer(serializers.ModelSerializer):
    comments = TicketCommentSerializer(many=True, read_only=True)
    related_service_title = serializers.CharField(source='related_service.service.title', read_only=True, default=None)
    user_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'ticket_id', 'user', 'user_name', 'category', 'related_service', 'related_service_title',
            'subject', 'description', 'priority', 'status', 'resolution', 'attachment',
            'admin_viewed_at', 'created_at', 'updated_at', 'comments'
        ]
        read_only_fields = [
            'id', 'ticket_id', 'user', 'status', 'resolution',
            'admin_viewed_at', 'created_at', 'updated_at'
        ]
