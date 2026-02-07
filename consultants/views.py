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


class ClientServiceRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for client service requests
    """
    queryset = ClientServiceRequest.objects.all()
    serializer_class = ClientServiceRequestSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        # Clients see their own requests
        return ClientServiceRequest.objects.filter(client=self.request.user).order_by('-created_at')
    
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
