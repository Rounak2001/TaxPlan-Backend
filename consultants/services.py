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


def assign_consultant_to_request(request_id, exclude_consultant_id=None):
    """
    Automatically assign the best consultant to a service request.

    Assignment Priority:
      1. Familiar consultant (worked with this client before) who offers the service
      2. Round-robin (least busy, longest waiting) from all available consultants

    Args:
        request_id: ID of the ClientServiceRequest
        exclude_consultant_id: optional ConsultantServiceProfile.id to skip
            (used after a drop to avoid re-assigning the same consultant)

    Returns:
        ConsultantServiceProfile object if assigned, None if no consultants available
    """

    request = ClientServiceRequest.objects.get(id=request_id)

    # ─── Try Affinity First ───
    familiar_result = find_familiar_consultant(request.client, request.service.id)
    # find_familiar_consultant returns a tuple (profile, meta) or (None, None)
    if isinstance(familiar_result, tuple):
        consultant = familiar_result[0]
    else:
        consultant = familiar_result

    # Skip dropped consultant if they showed up as familiar
    if consultant and exclude_consultant_id and consultant.id == exclude_consultant_id:
        consultant = None

    # ─── Fallback to Round-Robin ───
    if not consultant:
        available_consultants = find_matching_consultants(request.service.id)
        # Filter out the consultant who just dropped
        if exclude_consultant_id:
            available_consultants = [
                c for c in available_consultants if c.id != exclude_consultant_id
            ]
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


def drop_consultant_from_service(service_request_id, consultant_profile, reason_code, notes=''):
    """
    Handle the full drop flow when a consultant exits a service request.

    Steps:
      1. Validate the request is in a droppable state.
      2. Record the drop metadata (who dropped, reason, timestamp).
      3. Decrement consultant workload.
      4. Set status → 'consultant_dropped' and clear assigned_consultant.
      5. Set reassignment_deadline = now + 24h.
      6. Schedule Celery auto-reassign task.
      7. Send notifications (admin + client).
      8. Log to Activity Timeline.

    Args:
        service_request_id: int
        consultant_profile: ConsultantServiceProfile of the dropping consultant
        reason_code:  str (predefined key, e.g. 'client_inactive')
        notes:        str (optional free text)

    Returns:
        dict with keys: success, reassignment_deadline, message
    Raises:
        ValueError if the request is in a non-droppable state or belongs to a
        different consultant.
    """
    from django.utils import timezone
    from datetime import timedelta
    from .models import ClientServiceRequest
    from notifications.models import Notification
    from activity_timeline.models import Activity

    sr = ClientServiceRequest.objects.select_related('client', 'service', 'assigned_consultant').get(
        id=service_request_id
    )

    # — Guard: must be currently assigned to this consultant —
    if sr.assigned_consultant_id != consultant_profile.id:
        raise ValueError('You are not the assigned consultant for this service request.')

    # — Guard: already dropped / cancelled / completed —
    if sr.status in ('consultant_dropped', 'cancelled', 'completed'):
        raise ValueError(f'Cannot drop a service request in "{sr.status}" status.')

    REASON_LABELS = {
        'client_inactive':    'Client is inactive / not responding',
        'docs_not_uploaded':  'Documents not uploaded on time',
        'scope_mismatch':     'Service complexity beyond my expertise',
        'scheduling_conflict': 'Personal scheduling conflict',
        'client_behaviour':   'Client communication issues',
        'other':              'Other reason',
    }
    reason_label = REASON_LABELS.get(reason_code, reason_code)
    full_reason = f'{reason_label}: {notes}'.strip(': ') if notes else reason_label

    deadline = timezone.now() + timedelta(hours=24)

    # ── 1. Save drop metadata on the service request ──
    sr.dropped_consultant = consultant_profile
    sr.drop_reason = full_reason
    sr.dropped_at = timezone.now()
    sr.drop_count = (sr.drop_count or 0) + 1
    sr.assigned_consultant = None
    sr.assigned_at = None
    sr.status = 'consultant_dropped'
    sr.reassignment_deadline = deadline
    sr.save(update_fields=[
        'dropped_consultant', 'drop_reason', 'dropped_at', 'drop_count',
        'assigned_consultant', 'assigned_at', 'status', 'reassignment_deadline',
        'updated_at',
    ])

    # ── 2. Decrement consultant workload ──
    consultant_profile.current_client_count = models.Case(
        models.When(current_client_count__gt=0, then=F('current_client_count') - 1),
        default=0,
    )
    consultant_profile.save(update_fields=['current_client_count'])
    consultant_profile.refresh_from_db()

    # ── 3. Schedule auto-reassign Celery task ──
    try:
        from .tasks import auto_reassign_dropped_service
        auto_reassign_dropped_service.apply_async(
            args=[service_request_id],
            countdown=int(timedelta(hours=24).total_seconds()),
        )
    except Exception as task_exc:
        import logging
        logging.getLogger('consultants').warning(
            'Could not schedule auto_reassign task for request %s: %s',
            service_request_id, task_exc
        )

    # ── 4. Notify admin(s) ──
    consultant_name = consultant_profile.full_name
    client_name = sr.client.get_full_name() or sr.client.username
    service_name = sr.service.title if sr.service else 'Service'

    from django.contrib.auth import get_user_model
    User = get_user_model()
    admins = User.objects.filter(is_staff=True, is_active=True)

    def _push_ws(user_id, payload):
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'user_{user_id}',
                    {'type': 'notification_message', 'data': payload},
                )
        except Exception:
            pass

    for admin in admins:
        n = Notification.objects.create(
            recipient=admin,
            category='service',
            title=f'⚠️ Consultant Dropped: {service_name}',
            message=(
                f'{consultant_name} has dropped the service "{service_name}" for {client_name}. '
                f'Reason: {full_reason}. '
                f'Client has 24 hours to choose a new consultant before auto-assignment.'
            ),
            link='/admin/consultants/clientservicerequest/',
        )
        _push_ws(admin.id, {
            'id': n.id, 'type': 'NEW_NOTIFICATION', 'category': n.category,
            'title': n.title, 'message': n.message, 'link': n.link,
            'created_at': n.created_at.isoformat(), 'is_read': False,
        })

    # ── 5. Notify client ──
    client_notif = Notification.objects.create(
        recipient=sr.client,
        category='service',
        title=f'Your service is being reassigned: {service_name}',
        message=(
            f'Your consultant for {service_name} had to step back. '
            f'Please choose a new consultant within 24 hours. '
            f'If you don\'t, we\'ll assign the best available expert automatically. '
            f'All your documents and progress are fully preserved.'
        ),
        link='/client',
    )
    _push_ws(sr.client_id, {
        'id': client_notif.id, 'type': 'SERVICE_REASSIGNMENT',
        'category': client_notif.category, 'title': client_notif.title,
        'message': client_notif.message, 'link': client_notif.link,
        'service_request_id': sr.id,
        'created_at': client_notif.created_at.isoformat(), 'is_read': False,
    })

    # ── 6. Activity Timeline ──
    try:
        Activity.objects.create(
            actor=consultant_profile.user,
            target_user=sr.client,
            activity_type='service_status',
            title=f'Consultant stepped back from {service_name}',
            description=(
                f'{consultant_name} has dropped this service. Reason: {full_reason}. '
                f'A new consultant will be assigned within 24 hours.'
            ),
            content_object=sr,
            metadata={
                'dropped_by': consultant_name,
                'reason': full_reason,
                'drop_count': sr.drop_count,
                'reassignment_deadline': deadline.isoformat(),
            },
        )
    except Exception:
        pass

    return {
        'success': True,
        'reassignment_deadline': deadline.isoformat(),
        'message': 'Service dropped. Client will be notified to choose a new consultant within 24 hours.',
    }


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
