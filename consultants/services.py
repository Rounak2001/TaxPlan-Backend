"""
Service matching and assignment logic for consultants
"""

from django.db import models
from django.db.models import F
from django.utils import timezone
from .models import ConsultantServiceProfile, ConsultantServiceExpertise, ClientServiceRequest


def find_matching_consultants(service_id):
    """
    Find all consultants who offer the requested service and are available.
    Returns a list of available consultants ordered by current workload (least busy first).
    
    Args:
        service_id: ID of the service being requested
        
    Returns:
        List of ConsultantServiceProfile objects
    """
    
    # Find consultants with expertise in this service
    expertise_records = ConsultantServiceExpertise.objects.filter(
        service_id=service_id,
        consultant__is_active=True,
        consultant__current_client_count__lt=F('consultant__max_concurrent_clients')
    ).select_related('consultant').order_by('consultant__current_client_count', 'consultant__last_assigned_at')
    
    # Return consultants ordered by availability (least busy first)
    return [expertise.consultant for expertise in expertise_records]


def find_familiar_consultant(client, service_id):
    """
    Find a consultant who has previously worked with this client AND offers the requested service.
    
    Priority:
      1. Most recent interaction with the client (freshest context)
      2. Least current workload (tiebreaker)
    
    Cross-system: Checks both ServiceRequests and ConsultationBookings.
    Caps to last 5 unique consultants for efficiency.
    
    Args:
        client: User instance (the client)
        service_id: ID of the service being requested
        
    Returns:
        ConsultantServiceProfile or None
    """
def get_consultant_affinity(client):
    """
    Returns a dictionary mapping ConsultantServiceProfile ID to interaction metadata:
    {
        profile_id: {
            'last_interaction': datetime,
            'relation': 'self' | 'parent',
            'parent_name': str | None
        }
    }
    """
    from consultations.models import ConsultationBooking
    from django.db.models import Max
    
    def fetch_affinity(user, relation='self'):
        # {ConsultantServiceProfile.id: latest_assigned_at}
        past_from_services = (
            ClientServiceRequest.objects.filter(
                client=user,
                assigned_consultant__isnull=False,
                status__in=['assigned', 'completed'] + ClientServiceRequest.ACTIVE_STATUSES
            )
            .values('assigned_consultant')
            .annotate(last_interaction=Max('assigned_at'))
        )
        
        local_map = {}
        for record in past_from_services:
            profile_id = record['assigned_consultant']
            interaction_date = record['last_interaction']
            if profile_id and interaction_date:
                local_map[profile_id] = {
                    'last_interaction': interaction_date,
                    'relation': relation,
                    'parent_name': user.get_full_name() if relation == 'parent' else None
                }
                
        past_from_consultations = (
            ConsultationBooking.objects.filter(
                client=user,
                status='confirmed'
            )
            .values('consultant')
            .annotate(last_interaction=Max('booking_date'))
        )
        
        for record in past_from_consultations:
            user_id = record['consultant']
            interaction_date = record['last_interaction']
            if not user_id or not interaction_date:
                continue
                
            try:
                profile = ConsultantServiceProfile.objects.get(user_id=user_id)
                interaction_datetime = timezone.make_aware(
                    timezone.datetime.combine(interaction_date, timezone.datetime.min.time())
                ) if not isinstance(interaction_date, timezone.datetime) else interaction_date
                
                if profile.id in local_map:
                    if interaction_datetime > local_map[profile.id]['last_interaction']:
                        local_map[profile.id]['last_interaction'] = interaction_datetime
                else:
                    local_map[profile.id] = {
                        'last_interaction': interaction_datetime,
                        'relation': relation,
                        'parent_name': user.get_full_name() if relation == 'parent' else None
                    }
            except ConsultantServiceProfile.DoesNotExist:
                continue
        return local_map

    # 1. Get user's own affinity
    affinity_map = fetch_affinity(client, relation='self')
    
    # 2. If sub-account, get parent's affinity
    if client.parent_account:
        parent_affinity = fetch_affinity(client.parent_account, relation='parent')
        # Merge: User's own affinity takes priority if same consultant
        for profile_id, meta in parent_affinity.items():
            if profile_id not in affinity_map:
                affinity_map[profile_id] = meta
            # If both exist, keep the 'self' one (already in map) unless parent one is much newer?
            # User's own context is usually better even if slightly older.
            
    return affinity_map

def find_familiar_consultant(client, service_id):
    """
    Find a consultant who has previously worked with this client (or their parent)
    AND offers the requested service.
    
    Returns: (ConsultantServiceProfile, relation_meta) or (None, None)
    """
    affinity_map = get_consultant_affinity(client)
    
    if not affinity_map:
        return None, None
    
    # Sort by most recent interaction date
    sorted_profiles = sorted(
        affinity_map.items(), 
        key=lambda x: x[1]['last_interaction'], 
        reverse=True
    )[:5]
    familiar_profile_ids = [profile_id for profile_id, _ in sorted_profiles]
    
    # Filter to those who offer the requested service AND are available
    familiar_experts = ConsultantServiceExpertise.objects.filter(
        service_id=service_id,
        consultant_id__in=familiar_profile_ids,
        consultant__is_active=True,
        consultant__current_client_count__lt=F('consultant__max_concurrent_clients')
    ).select_related('consultant')
    
    if not familiar_experts.exists():
        return None, None
    
    best_match = None
    best_interaction_date = None
    
    for expertise in familiar_experts:
        profile = expertise.consultant
        meta = affinity_map.get(profile.id)
        if not meta:
            continue
            
        interaction_date = meta['last_interaction']
        if best_match is None or interaction_date > best_interaction_date:
            best_match = profile
            best_interaction_date = interaction_date
            
    return best_match, affinity_map.get(best_match.id) if best_match else None


def assign_consultant_to_request(request_id):
    """
    Automatically assign the best consultant to a service request.
    
    Assignment Priority:
      1. Familiar consultant (worked with this client before) who offers the service
      2. Round-robin (least busy, longest waiting) from all available consultants
    
    Args:
        request_id: ID of the ClientServiceRequest
        
    Returns:
        ConsultantServiceProfile object if assigned, None if no consultants available
    """
    
    request = ClientServiceRequest.objects.get(id=request_id)
    
    # ─── Try Affinity First ───
    consultant = find_familiar_consultant(request.client, request.service.id)
    
    # ─── Fallback to Round-Robin ───
    if not consultant:
        available_consultants = find_matching_consultants(request.service.id)
        if not available_consultants:
            return None
        consultant = available_consultants[0]
    
    # ─── Assign ───
    request.assigned_consultant = consultant
    request.status = 'assigned'
    request.assigned_at = timezone.now()
    request.save()
    
    # Increment consultant's current client count atomically and update timestamp
    consultant.current_client_count = F('current_client_count') + 1
    consultant.last_assigned_at = timezone.now()
    consultant.save()
    
    # Reload from DB to get the new integer value for current_client_count
    consultant.refresh_from_db()
    
    return consultant


def unassign_consultant_from_request(request_id):
    """
    Remove consultant assignment from a request and decrement their client count.
    
    Args:
        request_id: ID of the ClientServiceRequest
    """
    
    request = ClientServiceRequest.objects.get(id=request_id)
    
    if request.assigned_consultant:
        consultant = request.assigned_consultant
        consultant.current_client_count = models.Case(
            models.When(current_client_count__gt=0, then=F('current_client_count') - 1),
            default=0
        )
        consultant.save()
        consultant.refresh_from_db()
        
        request.assigned_consultant = None
        request.assigned_at = None
        request.status = 'pending'
        request.save()


def complete_service_request(request_id):
    """
    Mark a service request as completed and decrement consultant's client count.
    Also unassigns the consultant from the request.
    
    Args:
        request_id: ID of the ClientServiceRequest
    """
    
    request = ClientServiceRequest.objects.get(id=request_id)
    if request.assigned_consultant:
        consultant = request.assigned_consultant
        consultant.current_client_count = models.Case(
            models.When(current_client_count__gt=0, then=F('current_client_count') - 1),
            default=0
        )
        consultant.save()
        consultant.refresh_from_db()

    request.status = 'completed'
    request.completed_at = timezone.now()
    request.save()
