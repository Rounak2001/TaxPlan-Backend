"""
REST API views for Chat functionality.
"""

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from django.shortcuts import get_object_or_404

from .models import Conversation, Message
from .serializers import (
    ConversationSerializer,
    ConversationCreateSerializer,
    MessageSerializer,
)


class MessagePagination(PageNumberPagination):
    """Pagination for messages - latest messages first."""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


class ConversationListCreateView(generics.ListCreateAPIView):
    """
    GET: List all conversations for the authenticated user.
    POST: Create a new conversation (or return existing one).
    """
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ConversationCreateSerializer
        return ConversationSerializer
    
    def get_queryset(self):
        user = self.request.user
        from consultants.models import ClientServiceRequest

        if user.role == 'CONSULTANT':
            # Only show conversations with clients who have active services with this consultant
            active_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user,
            ).exclude(status__in=['completed', 'cancelled']).values_list('client_id', flat=True)

            return Conversation.objects.filter(
                consultant=user,
                client_id__in=active_client_ids,
            ).select_related('consultant', 'client').prefetch_related('messages')

        elif user.role == 'CLIENT':
            # Only show conversations with consultants who have active services with this client
            active_consultant_ids = ClientServiceRequest.objects.filter(
                client=user,
                assigned_consultant__isnull=False,
            ).exclude(status__in=['completed', 'cancelled']).values_list('assigned_consultant__user_id', flat=True)

            return Conversation.objects.filter(
                client=user,
                consultant_id__in=active_consultant_ids,
            ).select_related('consultant', 'client').prefetch_related('messages')

        return Conversation.objects.none()
    
    def create(self, request, *args, **kwargs):
        """
        Create a conversation or return existing one.
        Automatically assigns consultant/client based on user role.
        """
        user = request.user
        
        if user.role == 'CONSULTANT':
            client_id = request.data.get('client_id')
            if not client_id:
                return Response(
                    {'error': 'client_id is required for consultants'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get or create conversation
            conversation, created = Conversation.objects.get_or_create(
                consultant=user,
                client_id=client_id
            )
        
        elif user.role == 'CLIENT':
            # Get consultant_id from request or fallback to active service consultant
            consultant_id = request.data.get('consultant_id')
            
            if not consultant_id:
                # Get assigned consultant from active services
                from consultants.utils import get_active_consultant_for_client
                active_consultant = get_active_consultant_for_client(user)
                if active_consultant:
                    consultant_id = active_consultant.id
            
            if not consultant_id:
                return Response(
                    {'error': 'No consultant assigned to this client and no consultant_id provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            conversation, created = Conversation.objects.get_or_create(
                consultant_id=consultant_id,
                client=user
            )
        else:
            return Response(
                {'error': 'Invalid user role for chat'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ConversationSerializer(conversation, context={'request': request})
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class ConversationDetailView(generics.RetrieveAPIView):
    """
    GET: Retrieve a specific conversation.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationSerializer
    lookup_field = 'id'
    
    def get_queryset(self):
        user = self.request.user
        return Conversation.objects.filter(
            Q(consultant=user) | Q(client=user)
        ).select_related('consultant', 'client')


class MessageListView(generics.ListAPIView):
    """
    GET: List messages for a specific conversation (paginated).
    Messages are returned in chronological order (oldest first).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageSerializer
    pagination_class = MessagePagination
    
    def get_queryset(self):
        conversation_id = self.kwargs['conversation_id']
        user = self.request.user
        
        # Validate user is participant
        conversation = get_object_or_404(
            Conversation.objects.filter(Q(consultant=user) | Q(client=user)),
            id=conversation_id
        )
        
        return Message.objects.filter(
            conversation=conversation
        ).select_related('sender').order_by('timestamp')


class MarkMessagesReadView(APIView):
    """
    POST: Mark all messages in a conversation as read for the current user.
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request, conversation_id):
        user = request.user
        
        # Validate user is participant
        conversation = get_object_or_404(
            Conversation.objects.filter(Q(consultant=user) | Q(client=user)),
            id=conversation_id
        )
        
        # Mark messages from the other party as read
        updated_count = Message.objects.filter(
            conversation=conversation,
            is_read=False
        ).exclude(sender=user).update(is_read=True)
        
        return Response({
            'marked_read': updated_count
        })
