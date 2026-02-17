from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest
)
from .serializers import (
    ConsultantServiceProfileSerializer,
    ServiceCategorySerializer,
    ServiceSerializer,
    ConsultantServiceExpertiseSerializer,
    ClientServiceRequestSerializer,
    ConsultantDashboardSerializer
)
from .services import assign_consultant_to_request, complete_service_request


class ServiceCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing service categories
    """
    queryset = ServiceCategory.objects.filter(is_active=True)
    serializer_class = ServiceCategorySerializer
    permission_classes = [IsAuthenticated]


class ServiceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing services
    """
    queryset = Service.objects.filter(is_active=True)
    serializer_class = ServiceSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        category_id = self.request.query_params.get('category_id', None)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        Returns all services grouped by category
        """
        categories = ServiceCategory.objects.filter(is_active=True).prefetch_related('services')
        result = []
        for category in categories:
            result.append({
                'id': category.id,
                'name': category.name,
                'description': category.description,
                'services': ServiceSerializer(
                    category.services.filter(is_active=True), 
                    many=True
                ).data
            })
        return Response(result)


class ConsultantServiceProfileViewSet(viewsets.ModelViewSet):
    """
    API endpoint for consultant profiles
    """
    queryset = ConsultantServiceProfile.objects.all()
    serializer_class = ConsultantServiceProfileSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        # Consultants can only see their own profile
        if hasattr(self.request.user, 'consultant_service_profile'):
            return ConsultantServiceProfile.objects.filter(user=self.request.user)
        return ConsultantServiceProfile.objects.none()
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """
        Get consultant dashboard data
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response(
                {'error': 'Consultant profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get services offered by consultant
        expertise = ConsultantServiceExpertise.objects.filter(consultant=profile).select_related('service')
        services = [exp.service for exp in expertise]
        
        # Get assigned requests
        assigned_requests = ClientServiceRequest.objects.filter(
            assigned_consultant=profile
        ).exclude(status='completed').order_by('-created_at')
        
        # Calculate stats
        total_completed = ClientServiceRequest.objects.filter(
            assigned_consultant=profile,
            status='completed'
        ).count()
        
        stats = {
            'total_completed': total_completed,
            'current_clients': profile.current_client_count,
            'max_capacity': profile.max_concurrent_clients,
            'services_offered': len(services)
        }
        
        dashboard_data = {
            'profile': ConsultantServiceProfileSerializer(profile).data,
            'services': ServiceSerializer(services, many=True).data,
            'assigned_requests': ClientServiceRequestSerializer(assigned_requests, many=True).data,
            'stats': stats
        }
        
        return Response(dashboard_data)
    
    @action(detail=False, methods=['get'], url_path='dashboard-stats')
    def dashboard_stats(self, request):
        """
        Get comprehensive dashboard statistics for consultant
        Returns: service requests by status, documents needing review, client metrics
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response(
                {'error': 'Consultant profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Import Document model here to avoid circular imports
        from document_vault.models import Document
        
        # Service requests breakdown by status
        service_requests = ClientServiceRequest.objects.filter(assigned_consultant=profile)
        requests_by_status = {
            'pending': service_requests.filter(status='pending').count(),
            'assigned': service_requests.filter(status='assigned').count(),
            'in_progress': service_requests.filter(status='in_progress').count(),
            'completed': service_requests.filter(status='completed').count(),
            'total': service_requests.count()
        }
        
        # Documents needing review (uploaded by clients assigned to this consultant)
        # Get all clients assigned to this consultant through service requests
        assigned_client_ids = service_requests.values_list('client_id', flat=True).distinct()
        
        documents_uploaded = Document.objects.filter(
            client_id__in=assigned_client_ids,
            status='UPLOADED'
        ).count()
        
        documents_rejected = Document.objects.filter(
            client_id__in=assigned_client_ids,
            status='REJECTED'
        ).count()
        
        # Client metrics
        client_metrics = {
            'current_clients': profile.current_client_count,
            'max_capacity': profile.max_concurrent_clients,
            'available_slots': profile.max_concurrent_clients - profile.current_client_count,
            'utilization_percentage': round(
                (profile.current_client_count / profile.max_concurrent_clients * 100) 
                if profile.max_concurrent_clients > 0 else 0, 
                1
            )
        }
        
        # Monthly completion rate (last 30 days)
        from django.utils import timezone
        from datetime import timedelta
        
        thirty_days_ago = timezone.now() - timedelta(days=30)
        monthly_completed = service_requests.filter(
            status='completed',
            updated_at__gte=thirty_days_ago
        ).count()
        
        # Services offered
        expertise_count = ConsultantServiceExpertise.objects.filter(consultant=profile).count()
        
        return Response({
            'service_requests': requests_by_status,
            'documents': {
                'needs_review': documents_uploaded,
                'rejected': documents_rejected,
                'total_pending': documents_uploaded + documents_rejected
            },
            'clients': client_metrics,
            'services_offered': expertise_count,
            'monthly_completed': monthly_completed
        })
    
    @action(detail=False, methods=['get'])
    def client_view(self, request):
        """
        Get all consultants with their services for client dashboard
        Clients can see which consultants offer which services
        """
        # Get all active consultants
        consultants = ConsultantServiceProfile.objects.filter(is_active=True)
        
        result = []
        for consultant in consultants:
            # Get services offered by this consultant
            expertise = ConsultantServiceExpertise.objects.filter(
                consultant=consultant
            ).select_related('service', 'service__category')
            
            services = []
            for exp in expertise:
                services.append({
                    'id': exp.service.id,
                    'title': exp.service.title,
                    'category': exp.service.category.name,
                    'price': str(exp.service.price) if exp.service.price else None,
                    'tat': exp.service.tat
                })
            
            # Calculate availability
            available_slots = consultant.max_concurrent_clients - consultant.current_client_count
            availability_percentage = (available_slots / consultant.max_concurrent_clients * 100) if consultant.max_concurrent_clients > 0 else 0
            
            result.append({
                'id': consultant.id,
                'full_name': consultant.full_name,
                'email': consultant.email,
                'phone': consultant.phone,
                'qualification': consultant.qualification,
                'experience_years': consultant.experience_years,
                'certifications': consultant.certifications,
                'availability': {
                    'current_clients': consultant.current_client_count,
                    'max_clients': consultant.max_concurrent_clients,
                    'available_slots': available_slots,
                    'availability_percentage': round(availability_percentage, 1),
                    'is_available': available_slots > 0
                },
                'services': services,
                'total_services': len(services)
            })
        
        return Response({
            'consultants': result,
            'total_consultants': len(result)
        })


class ConsultantServiceExpertiseViewSet(viewsets.ModelViewSet):
    """
    API endpoint for consultant service expertise
    """
    queryset = ConsultantServiceExpertise.objects.all()
    serializer_class = ConsultantServiceExpertiseSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        # Filter by consultant if user has a profile
        if hasattr(self.request.user, 'consultant_service_profile'):
            return ConsultantServiceExpertise.objects.filter(
                consultant=self.request.user.consultant_service_profile
            )
        return ConsultantServiceExpertise.objects.none()
    
    @action(detail=False, methods=['post'])
    def add_services(self, request):
        """
        Add multiple services to consultant's expertise
        Body: {"service_ids": [1, 2, 3, 5]}
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response(
                {'error': 'Consultant profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        service_ids = request.data.get('service_ids', [])
        
        if not service_ids:
            return Response(
                {'error': 'service_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_count = 0
        for service_id in service_ids:
            _, created = ConsultantServiceExpertise.objects.get_or_create(
                consultant=profile,
                service_id=service_id
            )
            if created:
                created_count += 1
        
        return Response({
            'message': f'Added {created_count} services to expertise',
            'total_services': ConsultantServiceExpertise.objects.filter(consultant=profile).count()
        })
    
    @action(detail=False, methods=['get'])
    def my_services(self, request):
        """
        Get current consultant's selected service IDs
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
            expertise = ConsultantServiceExpertise.objects.filter(consultant=profile)
            service_ids = list(expertise.values_list('service_id', flat=True))
            return Response({'service_ids': service_ids})
        except ConsultantServiceProfile.DoesNotExist:
            return Response({'service_ids': []})
    
    @action(detail=False, methods=['post'])
    def update_services(self, request):
        """
        Replace consultant's expertise with new service selection
        Body: {"service_ids": [1, 2, 3, 5]}
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response(
                {'error': 'Consultant profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        service_ids = request.data.get('service_ids', [])
        
        # Delete all existing expertise
        ConsultantServiceExpertise.objects.filter(consultant=profile).delete()
        
        # Add new selections
        for service_id in service_ids:
            ConsultantServiceExpertise.objects.create(
                consultant=profile,
                service_id=service_id
            )
        
        return Response({
            'message': f'Updated expertise with {len(service_ids)} services',
            'service_ids': service_ids
        })


class ClientServiceRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for client service requests
    """
    queryset = ClientServiceRequest.objects.all()
    serializer_class = ClientServiceRequestSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.role == "CLIENT":
            return ClientServiceRequest.objects.filter(client=user).order_by('-created_at')
        elif user.role == "CONSULTANT":
            return ClientServiceRequest.objects.filter(
                assigned_consultant__user=user
            ).order_by('-created_at')
        return ClientServiceRequest.objects.none()
    
    def perform_create(self, serializer):
        # Create request and automatically assign consultant
        request_obj = serializer.save(client=self.request.user)
        
        # Try to assign a consultant
        consultant = assign_consultant_to_request(request_obj.id)
        
        if not consultant:
            # No consultants available, request stays pending
            pass
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        Mark a service request as completed
        """
        service_request = self.get_object()
        
        if service_request.status == 'completed':
            return Response(
                {'error': 'Request is already completed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        complete_service_request(service_request.id)
        
        return Response({
            'message': 'Service request completed successfully',
            'request': ClientServiceRequestSerializer(service_request).data
        })

    @action(detail=True, methods=['patch'], url_path='update-status')
    def update_status(self, request, pk=None):
        """
        Update the status of a service request.
        Only the assigned consultant can perform this action.
        """
        service_request = self.get_object()
        user = request.user

        # Permission check: Only the assigned consultant can update the status
        if service_request.assigned_consultant.user != user:
            return Response(
                {'error': 'You are not authorized to update this request'},
                status=status.HTTP_403_FORBIDDEN
            )

        new_status = request.data.get('status')
        if not new_status:
            return Response(
                {'error': 'Status is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate status choice
        valid_statuses = [choice[0] for choice in ClientServiceRequest.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Choose from: {", ".join(valid_statuses)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # [Logic Change]: Enforce strictly which statuses a consultant can manually set
        CONSULTANT_SETTABLE_STATUSES = [
            'doc_pending', 
            'under_review', 
            'wip', 
            'under_query', 
            'final_review', 
            'filed', 
            'revision_pending'
        ]

        if new_status not in CONSULTANT_SETTABLE_STATUSES:
            error_mapping = {
                'completed': 'Consultants cannot mark as "Completed" directly. Please use "Filed" and wait for client confirmation.',
                'assigned': 'The request is already assigned. You cannot move it back to the base "Assigned" status manually.',
                'pending': 'The request is already assigned. You cannot move it back to "Pending" manually.',
                'cancelled': 'Only an administrator or client can cancel a service request.'
            }
            error_msg = error_mapping.get(new_status, f'Manual update to status "{new_status}" is not allowed for consultants.')
            return Response(
                {'error': error_msg},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update status
        service_request.status = new_status
        service_request.save()

        return Response({
            'message': f'Status updated to {service_request.get_status_display()}',
            'status': service_request.status,
            'request': ClientServiceRequestSerializer(service_request).data
        })

    @action(detail=True, methods=['post'], url_path='acknowledge-completion')
    def acknowledge_completion(self, request, pk=None):
        """
        Client confirms work is satisfactory.
        Marks service as completed and decrements TC count.
        """
        service_request = self.get_object()
        user = request.user

        # Permission check: Only the client who requested the service can acknowledge
        if service_request.client != user:
            return Response(
                {'error': 'Only the client can acknowledge completion.'},
                status=status.HTTP_403_FORBIDDEN
            )

        if service_request.status == 'completed':
            return Response({'message': 'Service is already completed.'})

        # Finalize service
        from .services import complete_service_request
        complete_service_request(service_request.id)

        return Response({
            'success': True,
            'message': 'Service successfully completed and closed.',
            'request': ClientServiceRequestSerializer(service_request).data
        })

    @action(detail=True, methods=['post'], url_path='request-revision')
    def request_revision(self, request, pk=None):
        """
        Client is not satisfied and requests revision.
        Sets status to revision_pending.
        """
        service_request = self.get_object()
        user = request.user

        if service_request.client != user:
            return Response(
                {'error': 'Only the client can request revisions.'},
                status=status.HTTP_403_FORBIDDEN
            )

        notes = request.data.get('notes', '')
        service_request.status = 'revision_pending'
        service_request.revision_notes = notes
        service_request.save()

        return Response({
            'success': True,
            'message': 'Revision requested. Consultant will be notified.',
            'request': ClientServiceRequestSerializer(service_request).data
        })
