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
    from consultations.models import ConsultationBooking
    from django.db.models import Max
    
    # ─── Step 1: Build relationship map from Service Requests ───
    # {ConsultantServiceProfile.id: latest_assigned_at}
    past_from_services = (
        ClientServiceRequest.objects.filter(
            client=client,
            assigned_consultant__isnull=False,
            status__in=['assigned', 'completed'] + ClientServiceRequest.ACTIVE_STATUSES
        )
        .values('assigned_consultant')
        .annotate(last_interaction=Max('assigned_at'))
    )
    
    # Map: consultant_profile_id -> last_interaction_date
    affinity_map = {}
    for record in past_from_services:
        profile_id = record['assigned_consultant']
        interaction_date = record['last_interaction']
        if profile_id and interaction_date:
            affinity_map[profile_id] = interaction_date
    
    # ─── Step 2: Build relationship map from Consultation Bookings ───
    # ConsultationBooking.consultant is a User FK, so we need to map User -> ConsultantServiceProfile
    past_from_consultations = (
        ConsultationBooking.objects.filter(
            client=client,
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
            
        # Map User ID -> ConsultantServiceProfile ID
        try:
            profile = ConsultantServiceProfile.objects.get(user_id=user_id)
            # Convert date to datetime for comparison (booking_date is DateField)
            interaction_datetime = timezone.make_aware(
                timezone.datetime.combine(interaction_date, timezone.datetime.min.time())
            ) if not isinstance(interaction_date, timezone.datetime) else interaction_date
            
            # Keep the most recent interaction date
            if profile.id in affinity_map:
                if interaction_datetime > affinity_map[profile.id]:
                    affinity_map[profile.id] = interaction_datetime
            else:
                affinity_map[profile.id] = interaction_datetime
        except ConsultantServiceProfile.DoesNotExist:
            continue  # This consultant doesn't have a service profile
    
    if not affinity_map:
        return None  # No past relationships
    
    # ─── Step 3: Sort by most recent interaction, keep top 5 ───
    sorted_profiles = sorted(affinity_map.items(), key=lambda x: x[1], reverse=True)[:5]
    familiar_profile_ids = [profile_id for profile_id, _ in sorted_profiles]
    
    # ─── Step 4: Filter to those who offer the requested service AND are available ───
    familiar_experts = ConsultantServiceExpertise.objects.filter(
        service_id=service_id,
        consultant_id__in=familiar_profile_ids,
        consultant__is_active=True,
        consultant__current_client_count__lt=F('consultant__max_concurrent_clients')
    ).select_related('consultant')
    
    if not familiar_experts.exists():
        return None  # No familiar consultant offers this service or is available
    
    # ─── Step 5: Among available familiar consultants, pick the best ───
    # Sort: most recent interaction first, then least busy as tiebreaker
    best_match = None
    best_interaction_date = None
    
    for expertise in familiar_experts:
        consultant = expertise.consultant
        interaction_date = affinity_map.get(consultant.id)
        
        if best_match is None:
            best_match = consultant
            best_interaction_date = interaction_date
        elif interaction_date and best_interaction_date:
            # Prefer more recent interaction
            if interaction_date > best_interaction_date:
                best_match = consultant
                best_interaction_date = interaction_date
            # If same interaction date, prefer least busy
            elif interaction_date == best_interaction_date:
                if consultant.current_client_count < best_match.current_client_count:
                    best_match = consultant
                    best_interaction_date = interaction_date
    
    return best_match


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
