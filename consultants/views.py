from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import models
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest,
    ConsultantReview
)
from .serializers import (
    ConsultantServiceProfileSerializer,
    ServiceCategorySerializer,
    ServiceSerializer,
    ConsultantServiceExpertiseSerializer,
    ClientServiceRequestSerializer,
    ConsultantDashboardSerializer,
    ConsultantReviewSerializer
)
from .services import assign_consultant_to_request, complete_service_request
from consultant_onboarding.assessment_outcome import get_application_assessment_outcome
from consultant_onboarding.category_access import (
    ASSESSMENT_CATEGORY_ORDER,
    get_unlock_category_slugs_for_service,
    is_service_unlocked,
)
from consultant_onboarding.expertise_sync import sync_passed_sessions_to_consultant

MANUAL_CONSULTATION_CATEGORY_NAME = "consultation"


def _get_consultant_application(user):
    from consultant_onboarding.models import ConsultantApplication

    if not getattr(user, "email", None):
        return None
    return ConsultantApplication.objects.filter(email=user.email).first()


def _get_unlock_state_for_user(user):
    application = _get_consultant_application(user)
    if not application:
        return {
            'application': None,
            'unlocked_categories': list(ASSESSMENT_CATEGORY_ORDER),
            'available_assessment_categories': [],
            'can_start_assessment': False,
        }

    assessment = get_application_assessment_outcome(application)
    return {
        'application': application,
        'unlocked_categories': assessment.get('unlocked_categories', []),
        'available_assessment_categories': assessment.get('available_assessment_categories', []),
        'can_start_assessment': assessment.get('can_start_assessment', False),
    }


def _is_manual_consultation_service(service):
    category_name = getattr(getattr(service, "category", None), "name", "") or ""
    return category_name.strip().lower() == MANUAL_CONSULTATION_CATEGORY_NAME


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
    
    @action(detail=False, methods=['get'], url_path='by_category')
    def by_category(self, request):
        """
        Get all categories with their nested services.
        Uses Prefetch to avoid N+1 queries on the services relation.
        """
        unlock_state = _get_unlock_state_for_user(request.user)
        unlocked_categories = unlock_state['unlocked_categories']
        categories = ServiceCategory.objects.filter(is_active=True).exclude(
            name__iexact=MANUAL_CONSULTATION_CATEGORY_NAME
        ).prefetch_related(
            Prefetch(
                'services',
                queryset=Service.objects.filter(is_active=True),
                to_attr='active_services'
            )
        )
        
        result = []
        for cat in categories:
            services = []
            for service in cat.active_services:
                service_data = ServiceSerializer(service).data
                unlock_category_slugs = get_unlock_category_slugs_for_service(service)
                service_data['unlock_category_slugs'] = unlock_category_slugs
                service_data['is_unlocked'] = is_service_unlocked(service, unlocked_categories)
                services.append(service_data)

            result.append({
                'id': cat.id,
                'name': cat.name,
                'description': cat.description,
                'services': services
            })
            
        return Response(result)

    @action(detail=False, methods=['post'], url_path='match-cart')
    def match_cart(self, request):
        """
        Accept a list of service titles from the cart, match them to DB services,
        and return consultants who have expertise in ANY of those services.
        Body: { "titles": ["PAN Application", "Aadhaar Validation", ...] }
        """
        from .models import ConsultantReview

        titles = request.data.get('titles', [])
        if not titles:
            return Response({
                'consultants': [],
                'auto_recommended': None,
                'familiar_consultant': None,
                'total': 0
            })
        normalized_titles = [
            str(title).strip()
            for title in titles
            if str(title).strip()
        ]
        if not normalized_titles:
            return Response({
                'consultants': [],
                'auto_recommended': None,
                'familiar_consultant': None,
                'total': 0,
                'matched_services': 0
            })

        # Find matching Service records by title (case-insensitive)
        from django.db.models import Q, Count
        title_q = Q()
        for title in normalized_titles:
            title_q |= Q(title__iexact=title)
        
        matched_services = Service.objects.filter(title_q, is_active=True)
        # Get unique IDs to ensure we count correctly even if duplicate titles were sent
        required_service_ids = list(matched_services.values_list('id', flat=True).distinct())
        total_required = len(required_service_ids)

        if total_required == 0:
            return Response({
                'consultants': [],
                'auto_recommended': None,
                'familiar_consultant': None,
                'total': 0,
                'matched_services': 0
            })

        # Find consultants who have expertise in ANY of the matched services.
        # This keeps consultant discovery broad and aligns with the endpoint contract.
        consultant_ids = ConsultantServiceExpertise.objects.filter(
            service_id__in=required_service_ids,
            consultant__is_active=True,
        ).values_list('consultant_id', flat=True).distinct()

        # Annotate live_client_count and prefetch recent reviews in bulk to avoid
        # N+1 queries (one COUNT + one reviews query per consultant).
        consultants = ConsultantServiceProfile.objects.filter(
            id__in=consultant_ids
        ).annotate(
            live_client_count=Count(
                'assigned_requests',
                filter=~Q(assigned_requests__status__in=['completed', 'cancelled']),
                distinct=True
            )
        ).prefetch_related(
            Prefetch(
                'reviews',
                queryset=ConsultantReview.objects.select_related(
                    'client', 'service_request__service'
                ).order_by('-created_at'),
                to_attr='recent_reviews_prefetched'
            )
        ).order_by('current_client_count', 'last_assigned_at')

        covered_titles = list(matched_services.values_list('title', flat=True))

        result = []
        for consultant in consultants:
            live_client_count = consultant.live_client_count
            available_slots = consultant.max_concurrent_clients - live_client_count
            utilization = (live_client_count / consultant.max_concurrent_clients * 100) if consultant.max_concurrent_clients > 0 else 0

            reviews_data = []
            for review in consultant.recent_reviews_prefetched[:3]:
                reviews_data.append({
                    'id': review.id,
                    'rating': review.rating,
                    'review_text': review.review_text,
                    'client_name': review.client.get_full_name() or 'Anonymous',
                    'service_title': review.service_request.service.title if review.service_request and review.service_request.service else '',
                    'created_at': review.created_at.isoformat(),
                })

            result.append({
                'id': consultant.id,
                'full_name': consultant.full_name,
                'qualification': consultant.qualification,
                'experience_years': consultant.experience_years,
                'bio': consultant.bio or '',
                'average_rating': float(consultant.average_rating),
                'total_reviews': consultant.total_reviews,
                'recent_reviews': reviews_data,
                'covered_services': covered_titles,
                'workload': {
                    'current_tasks': live_client_count,
                    'max_capacity': consultant.max_concurrent_clients,
                    'available_slots': available_slots,
                    'utilization': round(utilization, 1)
                }
            })

        auto_recommended = result[0] if result else None

        familiar_data = None
        if request.user.is_authenticated and result:
            from .services import get_consultant_affinity
            try:
                affinity_map = get_consultant_affinity(request.user)
            except Exception:
                affinity_map = {}
            
            best_match = None
            best_interaction_date = None
            
            for consultant_data in result:
                cid = consultant_data['id']
                if cid in affinity_map:
                    interaction_date = affinity_map[cid]['last_interaction']
                    if best_match is None or interaction_date > best_interaction_date:
                        best_match = consultant_data
                        best_interaction_date = interaction_date
            
            if best_match:
                meta = affinity_map[best_match['id']]
                if meta['relation'] == 'parent':
                    message = f"This consultant has handled work for your main account ({meta['parent_name']}). Continuing with them ensures consistent service across all your accounts."
                else:
                    message = f"{best_match['full_name']} has worked with you before. Continuing with the same consultant ensures faster processing and smoother communication."
                
                familiar_data = {
                    **best_match,
                    'message': message
                }

        return Response({
            'consultants': result,
            'auto_recommended': auto_recommended,
            'familiar_consultant': familiar_data,
            'total': len(result),
            'matched_services': total_required
        })

    @action(detail=True, methods=['get'], url_path='available-consultants')
    def available_consultants(self, request, pk=None):
        """
        Get consultants who offer this service, sorted by workload (least busy first).
        Also detects returning clients and suggests their previous consultant.
        Includes recent reviews per consultant for the "More Info" panel.
        """
        service = self.get_object()
        
        # Reuse existing matching logic
        from .services import find_matching_consultants, find_familiar_consultant
        from .models import ConsultantReview
        
        consultants = find_matching_consultants(service.id)
        
        # Detect returning client — find a consultant they've worked with before
        familiar = None
        familiar_relation = None
        familiar_data = None
        if request.user.is_authenticated:
            familiar, familiar_relation = find_familiar_consultant(request.user, service.id)
        
        # Build consultant data with reviews
        # Annotate live_client_count and prefetch reviews in bulk to eliminate
        # N+1 queries (one per consultant for COUNT and one for reviews).
        from django.db.models import Count, Q, Prefetch
        consultants = ConsultantServiceProfile.objects.filter(
            id__in=[c.id for c in consultants]
        ).annotate(
            live_client_count=Count(
                'assigned_requests',
                filter=~Q(assigned_requests__status__in=['completed', 'cancelled']),
                distinct=True
            )
        ).prefetch_related(
            Prefetch(
                'reviews',
                queryset=ConsultantReview.objects.select_related(
                    'client', 'service_request__service'
                ).order_by('-created_at'),
                to_attr='recent_reviews_prefetched'
            )
        ).order_by('current_client_count', 'last_assigned_at')

        result = []
        for consultant in consultants:
            live_client_count = consultant.live_client_count
            available_slots = consultant.max_concurrent_clients - live_client_count
            utilization = (live_client_count / consultant.max_concurrent_clients * 100) if consultant.max_concurrent_clients > 0 else 0

            reviews_data = []
            for review in consultant.recent_reviews_prefetched[:3]:
                reviews_data.append({
                    'id': review.id,
                    'rating': review.rating,
                    'review_text': review.review_text,
                    'client_name': review.client.get_full_name() or 'Anonymous',
                    'service_title': review.service_request.service.title if review.service_request and review.service_request.service else '',
                    'created_at': review.created_at.isoformat(),
                })

            consultant_data = {
                'id': consultant.id,
                'full_name': consultant.full_name,
                'qualification': consultant.qualification,
                'experience_years': consultant.experience_years,
                'bio': consultant.bio or '',
                'average_rating': float(consultant.average_rating),
                'total_reviews': consultant.total_reviews,
                'recent_reviews': reviews_data,
                'workload': {
                    'current_tasks': live_client_count,
                    'max_capacity': consultant.max_concurrent_clients,
                    'available_slots': available_slots,
                    'utilization': round(utilization, 1)
                }
            }

            result.append(consultant_data)

            # If this is the familiar consultant, build the suggestion
            if familiar and consultant.id == familiar.id:
                if familiar_relation and familiar_relation['relation'] == 'parent':
                    message = f"{consultant.full_name} has handled work for your main account ({familiar_relation['parent_name']}). Would you like to do this work with the same consultant?"
                else:
                    message = f"{consultant.full_name} has worked with you before and already understands your requirements. Continuing with the same consultant ensures faster processing and smoother communication."
                
                familiar_data = {
                    **consultant_data,
                    'message': message
                }
        
        auto_recommended = result[0] if result else None
        
        return Response({
            'consultants': result,
            'auto_recommended': auto_recommended,
            'familiar_consultant': familiar_data,
            'total': len(result)
        })


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
        expertise = ConsultantServiceExpertise.objects.filter(consultant=profile).select_related('service', 'service__category')
        services = [exp.service for exp in expertise]
        
        # Get assigned requests — select_related eliminates per-row FK queries in the serializer
        assigned_requests = ClientServiceRequest.objects.filter(
            assigned_consultant=profile
        ).exclude(status__in=['completed', 'cancelled']).select_related(
            'client', 'service', 'service__category'
        ).order_by('-created_at')
        
        # Calculate stats — compute live client count from active requests
        total_completed = ClientServiceRequest.objects.filter(
            assigned_consultant=profile,
            status='completed'
        ).count()
        
        live_client_count = ClientServiceRequest.objects.filter(
            assigned_consultant=profile
        ).exclude(status__in=['completed', 'cancelled']).values('client').distinct().count()

        stats = {
            'total_completed': total_completed,
            'current_clients': live_client_count,
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
        
        # Client metrics — compute live from active requests so admin deletions are reflected immediately
        live_client_count = ClientServiceRequest.objects.filter(
            assigned_consultant=profile
        ).exclude(status__in=['completed', 'cancelled']).values('client').distinct().count()

        client_metrics = {
            'current_clients': live_client_count,
            'max_capacity': profile.max_concurrent_clients,
            'available_slots': profile.max_concurrent_clients - live_client_count,
            'utilization_percentage': round(
                (live_client_count / profile.max_concurrent_clients * 100)
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
        
        # Total earnings from completed requests
        from django.db.models import Sum, F
        total_earnings = service_requests.filter(
            status='completed'
        ).aggregate(
            total=Sum('service__price')
        )['total'] or 0
        
        return Response({
            'service_requests': requests_by_status,
            'documents': {
                'needs_review': documents_uploaded,
                'rejected': documents_rejected,
                'total_pending': documents_uploaded + documents_rejected
            },
            'clients': client_metrics,
            'services_offered': expertise_count,
            'monthly_completed': monthly_completed,
            'total_earnings': float(total_earnings),
        })
    
    @action(detail=False, methods=['get'])
    def client_view(self, request):
        """
        Get all consultants with their services for client dashboard
        Clients can see which consultants offer which services
        """
        from django.db.models import Count, Q, Prefetch

        # Prefetch expertise in ONE query and annotate live_client_count to avoid
        # N+1 queries (one per consultant) for both expertise and client counts.
        consultants = ConsultantServiceProfile.objects.filter(is_active=True).prefetch_related(
            Prefetch(
                'consultantserviceexpertise_set',
                queryset=ConsultantServiceExpertise.objects.select_related(
                    'service', 'service__category'
                ),
                to_attr='prefetched_expertise'
            )
        ).annotate(
            live_client_count=Count(
                'assigned_requests__client',
                filter=~Q(assigned_requests__status__in=['completed', 'cancelled']),
                distinct=True
            )
        )

        result = []
        for consultant in consultants:
            services = []
            for exp in consultant.prefetched_expertise:
                services.append({
                    'id': exp.service.id,
                    'title': exp.service.title,
                    'category': exp.service.category.name,
                    'price': str(exp.service.price) if exp.service.price else None,
                    'tat': exp.service.tat
                })

            live_client_count = consultant.live_client_count
            available_slots = consultant.max_concurrent_clients - live_client_count
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
                    'current_clients': live_client_count,
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

        unlock_state = _get_unlock_state_for_user(request.user)
        unlocked_categories = unlock_state['unlocked_categories']
        requested_services = list(Service.objects.filter(id__in=service_ids).select_related('category'))
        consultation_services = [
            {
                'id': service.id,
                'title': service.title,
            }
            for service in requested_services
            if _is_manual_consultation_service(service)
        ]
        if consultation_services:
            return Response(
                {
                    'error': 'Consultation topics are enabled automatically from the services you offer and cannot be selected separately.',
                    'consultation_services': consultation_services,
                },
                status=400,
            )
        locked_services = [
            {
                'id': service.id,
                'title': service.title,
                'unlock_category_slugs': get_unlock_category_slugs_for_service(service),
            }
            for service in requested_services
            if not is_service_unlocked(service, unlocked_categories)
        ]
        if locked_services:
            return Response(
                {
                    'error': 'Some selected services are still locked for your account.',
                    'locked_services': locked_services,
                    'unlocked_categories': unlocked_categories,
                },
                status=400,
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
    
    @action(detail=False, methods=['get'], url_path='my_services')
    def my_services(self, request):
        """
        Get IDs of services currently selected by the consultant
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response({
                'service_ids': [],
                'unlocked_categories': list(ASSESSMENT_CATEGORY_ORDER),
                'available_assessment_categories': [],
                'can_start_assessment': False,
            })

        unlock_state = _get_unlock_state_for_user(request.user)
        if unlock_state['application'] is not None:
            sync_passed_sessions_to_consultant(
                unlock_state['application'],
                consultant_profile=profile,
            )
            
        service_ids = ConsultantServiceExpertise.objects.filter(
            consultant=profile
        ).values_list('service_id', flat=True)
        
        return Response({
            'service_ids': list(service_ids),
            'unlocked_categories': unlock_state['unlocked_categories'],
            'available_assessment_categories': unlock_state['available_assessment_categories'],
            'can_start_assessment': unlock_state['can_start_assessment'],
        })

    @action(detail=False, methods=['post'], url_path='update_services')
    def update_services(self, request):
        """
        Set the exact list of services offered by the consultant
        """
        try:
            profile = ConsultantServiceProfile.objects.get(user=request.user)
        except ConsultantServiceProfile.DoesNotExist:
            return Response({'error': 'Profile not found'}, status=404)
            
        service_ids = request.data.get('service_ids', [])
        unlock_state = _get_unlock_state_for_user(request.user)
        unlocked_categories = unlock_state['unlocked_categories']

        requested_services = list(Service.objects.filter(id__in=service_ids).select_related('category'))
        known_service_ids = {service.id for service in requested_services}
        unknown_service_ids = [
            service_id for service_id in service_ids
            if service_id not in known_service_ids
        ]
        if unknown_service_ids:
            return Response(
                {'error': 'One or more selected services were not found.', 'service_ids': unknown_service_ids},
                status=400,
            )

        consultation_services = [
            {
                'id': service.id,
                'title': service.title,
            }
            for service in requested_services
            if _is_manual_consultation_service(service)
        ]
        if consultation_services:
            return Response(
                {
                    'error': 'Consultation topics are enabled automatically from the services you offer and cannot be selected separately.',
                    'consultation_services': consultation_services,
                },
                status=400,
            )

        locked_services = [
            {
                'id': service.id,
                'title': service.title,
                'unlock_category_slugs': get_unlock_category_slugs_for_service(service),
            }
            for service in requested_services
            if not is_service_unlocked(service, unlocked_categories)
        ]
        if locked_services:
            return Response(
                {
                    'error': 'Some selected services are still locked for your account.',
                    'locked_services': locked_services,
                    'unlocked_categories': unlocked_categories,
                },
                status=400,
            )
        
        # Remove old ones
        ConsultantServiceExpertise.objects.filter(consultant=profile).delete()
        
        created_count = 0
        for service_id in service_ids:
            ConsultantServiceExpertise.objects.create(
                consultant=profile,
                service_id=service_id,
            )
            created_count += 1
        
        return Response({'success': True, 'count': created_count})


class ClientServiceRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for client service requests
    """
    queryset = ClientServiceRequest.objects.all()
    serializer_class = ClientServiceRequestSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        from core_auth.utils import get_active_profile
        user = get_active_profile(self.request)
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

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """
        Client cancels the service request and receives a full automatic refund
        via Razorpay, provided work has not started (status is before 'wip').
        Body: { "reason": "I changed my mind" }
        """
        from django.utils import timezone
        import razorpay
        from django.conf import settings
        from service_orders.models import ServiceOrder, OrderItem

        service_request = self.get_object()
        user = request.user

        # Permission: only the requesting client
        if service_request.client != user:
            return Response(
                {'error': 'Only the client who placed this request can cancel it.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Already cancelled
        if service_request.status == 'cancelled':
            return Response(
                {'error': 'This service request is already cancelled.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Gate: cancellation not allowed once work has started (wip or beyond)
        from .models import ClientServiceRequest as CSR
        if service_request.status not in CSR.CANCELLABLE_STATUSES:
            return Response(
                {
                    'error': 'Cancellation is no longer allowed. Work has already started on your service request.',
                    'current_status': service_request.status
                },
                status=status.HTTP_403_FORBIDDEN
            )

        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response(
                {'error': 'A cancellation reason is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # --- Razorpay Refund ---
        refund_info = None
        refund_error = None
        try:
            # Find OrderItem linked to this service_request's service
            # The link: ServiceOrder → OrderItem (service_title matches) owned by same user
            order_item = OrderItem.objects.select_related('order').filter(
                order__user=service_request.client,
                order__status='paid',
                service_title=service_request.service.title,
            ).order_by('-order__created_at').first()

            if order_item and order_item.order.razorpay_payment_id:
                rzp = razorpay.Client(
                    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
                )
                amount_paise = int(order_item.price * 100)
                refund = rzp.payment.refund(
                    order_item.order.razorpay_payment_id,
                    {'amount': amount_paise}
                )
                refund_info = {
                    'refund_id': refund.get('id'),
                    'amount': float(order_item.price),
                    'status': refund.get('status'),
                }
                # Mark the service order as cancelled
                order_item.order.status = 'cancelled'
                order_item.order.save(update_fields=['status'])
        except Exception as exc:
            # Log but do not block cancellation — admin can retry refund manually
            import logging
            logging.getLogger('consultants').error(
                f"Razorpay refund failed for service_request {service_request.id}: {exc}",
                exc_info=True
            )
            refund_error = str(exc)

        # --- Cancel the service request ---
        service_request.status = 'cancelled'
        service_request.cancellation_reason = reason
        service_request.cancelled_at = timezone.now()
        service_request.save(update_fields=['status', 'cancellation_reason', 'cancelled_at'])

        # --- Decrement consultant workload so their slot opens up immediately ---
        if service_request.assigned_consultant:
            from django.db.models import F, Case, When
            from notifications.models import Notification
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            
            consultant = service_request.assigned_consultant
            consultant.current_client_count = Case(
                When(current_client_count__gt=0, then=F('current_client_count') - 1),
                default=0
            )
            consultant.save(update_fields=['current_client_count'])

            # Send Notification to Consultant
            consultant_user = consultant.user
            client_name = service_request.client.first_name or service_request.client.username
            service_name = service_request.service.title
            
            notif = Notification.objects.create(
                recipient=consultant_user,
                category='service',
                title=f'Service Cancelled: {service_name}',
                message=f"{client_name} has cancelled this service request. Reason: {reason}",
                link='/dashboard'  # Redirects to dashboard
            )
            
            # Send real-time websocket notification
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{consultant_user.id}",
                {
                    "type": "notification_message",
                    "data": {
                        "id": notif.id,
                        "type": "NEW_NOTIFICATION",
                        "category": notif.category,
                        "title": notif.title,
                        "message": notif.message,
                        "link": notif.link,
                        "created_at": notif.created_at.isoformat(),
                        "is_read": False,
                    }
                }
            )

        response_data = {
            'success': True,
            'message': 'Service cancelled successfully.',
            'refund': refund_info,
        }
        if refund_error:
            response_data['refund_warning'] = (
                'Refund could not be processed automatically. '
                'Our team has been notified and will process it manually within 2 business days.'
            )

        return Response(response_data)

class ConsultantReviewViewSet(viewsets.ModelViewSet):
    """API endpoint for client reviews (Feedback & Review)"""
    queryset = ConsultantReview.objects.all()
    serializer_class = ConsultantReviewSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.role == 'CLIENT':
            return ConsultantReview.objects.filter(client=user)
        elif user.role == 'CONSULTANT':
            try:
                return ConsultantReview.objects.filter(consultant=user.consultant_service_profile)
            except ConsultantServiceProfile.DoesNotExist:
                return ConsultantReview.objects.none()
        return super().get_queryset()

    def perform_create(self, serializer):
        from rest_framework import serializers
        service_request = serializer.validated_data.get('service_request')
        
        print(f"[REVIEW] Creating review for service_request={service_request.id}, status={service_request.status}, client={service_request.client}, user={self.request.user}")
        
        # Validation checks
        if service_request.client != self.request.user:
            raise serializers.ValidationError({"error": "You can only review your own service requests."})
            
        if service_request.status not in ('completed', 'final_review', 'filed'):
            raise serializers.ValidationError({"error": f"You can only review a completed service request. Current status: {service_request.status}"})
            
        # Ensure review doesn't already exist for this request
        if ConsultantReview.objects.filter(service_request=service_request).exists():
            raise serializers.ValidationError({"error": "A review already exists for this service request."})
            
        consultant = service_request.assigned_consultant
        if not consultant:
            raise serializers.ValidationError({"error": "No consultant was assigned to this service request."})
            
        serializer.save(client=self.request.user, consultant=consultant)
