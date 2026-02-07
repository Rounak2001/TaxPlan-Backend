"""
Service matching and assignment logic for consultants
"""

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
    ).select_related('consultant').order_by('consultant__current_client_count')
    
    # Return consultants ordered by availability (least busy first)
    return [expertise.consultant for expertise in expertise_records]


def assign_consultant_to_request(request_id):
    """
    Automatically assign the most available consultant to a service request.
    Uses simple round-robin based on current workload.
    
    Args:
        request_id: ID of the ClientServiceRequest
        
    Returns:
        ConsultantServiceProfile object if assigned, None if no consultants available
    """
    
    request = ClientServiceRequest.objects.get(id=request_id)
    
    # Find matching consultants
    available_consultants = find_matching_consultants(request.service.id)
    
    if not available_consultants:
        # No consultants available
        return None
    
    # Assign consultant with lowest current workload (first in list)
    consultant = available_consultants[0]
    
    request.assigned_consultant = consultant
    request.status = 'assigned'
    request.assigned_at = timezone.now()
    request.save()
    
    # Increment consultant's current client count
    consultant.current_client_count += 1
    consultant.save()
    
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
        consultant.current_client_count = max(0, consultant.current_client_count - 1)
        consultant.save()
        
        request.assigned_consultant = None
        request.assigned_at = None
        request.status = 'pending'
        request.save()


def complete_service_request(request_id):
    """
    Mark a service request as completed and decrement consultant's client count.
    
    Args:
        request_id: ID of the ClientServiceRequest
    """
    
    request = ClientServiceRequest.objects.get(id=request_id)
    
    if request.assigned_consultant:
        consultant = request.assigned_consultant
        consultant.current_client_count = max(0, consultant.current_client_count - 1)
        consultant.save()
    
    request.status = 'completed'
    request.completed_at = timezone.now()
    request.save()
