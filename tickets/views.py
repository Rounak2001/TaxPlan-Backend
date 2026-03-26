from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from .models import Ticket, TicketComment
from .serializers import TicketSerializer, TicketCommentSerializer

class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Ticket.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        # Only allow partial updates (PATCH) on subject and description if status is open
        ticket = self.get_object()
        if ticket.status != 'open':
            return Response({"error": "You can only edit an open ticket."}, status=status.HTTP_400_BAD_REQUEST)
        
        allowed_fields = {'subject', 'description', 'priority', 'category', 'related_service'}
        for field in list(request.data.keys()):
            if field not in allowed_fields:
                request.data.pop(field, None)
                
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status != 'open':
            return Response({"error": "Only open tickets can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        
        ticket.status = 'cancelled'
        ticket.save()
        return Response({"status": "Ticket cancelled."})

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status != 'resolved':
            return Response({"error": "Only resolved tickets can be reopened."}, status=status.HTTP_400_BAD_REQUEST)
        
        ticket.status = 'reopened'
        ticket.save()
        return Response({"status": "Ticket reopened."})

    @action(detail=True, methods=['post'])
    def confirm_resolved(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status != 'resolved':
            return Response({"error": "Only resolved tickets can be confirmed as closed."}, status=status.HTTP_400_BAD_REQUEST)
        
        ticket.status = 'closed'
        ticket.save()
        return Response({"status": "Ticket closed."})

class TicketCommentViewSet(viewsets.ModelViewSet):
    serializer_class = TicketCommentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        ticket_id = self.kwargs.get('ticket_pk')
        return TicketComment.objects.filter(ticket__id=ticket_id, ticket__user=self.request.user).order_by('created_at')

    def perform_create(self, serializer):
        ticket_id = self.kwargs.get('ticket_pk')
        try:
            ticket = Ticket.objects.get(id=ticket_id, user=self.request.user)
        except Ticket.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound()
            
        serializer.save(ticket=ticket, author=self.request.user)
