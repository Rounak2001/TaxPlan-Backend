from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import timedelta
from .models import Activity
from .serializers import ActivitySerializer
from consultants.models import ClientServiceRequest


class ActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for consultants to view activity timeline
    Supports filtering by type, date range, and client
    """
    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """
        Return activities for clients assigned to the authenticated consultant
        """
        user = self.request.user
        
        # Get all clients assigned to this consultant through service requests
        consultant_clients = ClientServiceRequest.objects.filter(
            assigned_consultant__user=user
        ).values_list('client_id', flat=True).distinct()
        
        # Base queryset: activities for consultant's clients
        queryset = Activity.objects.filter(
            target_user_id__in=consultant_clients
        ).select_related('actor', 'target_user', 'content_type')
        
        # Filter by activity type
        activity_type = self.request.query_params.get('type')
        if activity_type and activity_type != 'all':
            queryset = queryset.filter(activity_type=activity_type)
        
        # Filter by date range
        date_filter = self.request.query_params.get('date')
        if date_filter == 'today':
            queryset = queryset.filter(created_at__date=timezone.now().date())
        elif date_filter == 'week':
            week_ago = timezone.now() - timedelta(days=7)
            queryset = queryset.filter(created_at__gte=week_ago)
        elif date_filter == 'month':
            month_ago = timezone.now() - timedelta(days=30)
            queryset = queryset.filter(created_at__gte=month_ago)
        
        # Filter by specific client
        client_id = self.request.query_params.get('client')
        if client_id:
            queryset = queryset.filter(target_user_id=client_id)
        
        return queryset
    
    @action(detail=False, methods=['get'], url_path='stats')
    def activity_stats(self, request):
        """
        Get activity statistics for the consultant
        Returns counts by activity type
        """
        user = request.user
        
        # Get consultant's clients
        consultant_clients = ClientServiceRequest.objects.filter(
            assigned_consultant__user=user
        ).values_list('client_id', flat=True).distinct()
        
        # Get activities from last 7 days
        week_ago = timezone.now() - timedelta(days=7)
        activities = Activity.objects.filter(
            target_user_id__in=consultant_clients,
            created_at__gte=week_ago
        )
        
        # Count by type
        stats = {
            'total': activities.count(),
            'document_activities': activities.filter(
                activity_type__in=['document_upload', 'document_verify', 'document_reject']
            ).count(),
            'service_activities': activities.filter(
                activity_type__in=['service_new', 'service_status', 'service_complete']
            ).count(),
            'call_activities': activities.filter(
                activity_type__in=['call_made', 'call_received']
            ).count(),
            'today': activities.filter(created_at__date=timezone.now().date()).count(),
        }
        
        return Response(stats)
