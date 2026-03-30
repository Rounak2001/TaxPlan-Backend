"""
Celery tasks for the consultants app.

Currently handles:
  - auto_reassign_dropped_service: fires at the reassignment deadline and
    picks the best available consultant if the client hasn't chosen one yet.
"""
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger('consultants')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def auto_reassign_dropped_service(self, service_request_id: int):
    """
    Celery task scheduled ~24 hours after a consultant drops a service.

    If the client has NOT already picked a new consultant (i.e. status is
    still 'consultant_dropped'), auto-assign the best available consultant
    using the existing round-robin / affinity logic.

    If no consultants are available, notify admin and leave in dropped state
    so they can manually intervene.
    """
    from .models import ClientServiceRequest
    from .services import assign_consultant_to_request

    try:
        sr = ClientServiceRequest.objects.select_related(
            'client', 'service', 'assigned_consultant'
        ).get(id=service_request_id)
    except ClientServiceRequest.DoesNotExist:
        logger.warning(
            'auto_reassign_dropped_service: request %s not found – skipping',
            service_request_id,
        )
        return

    # If client already chose or something else intervened, skip
    if sr.status != 'consultant_dropped':
        logger.info(
            'auto_reassign_dropped_service: request %s already in status "%s" – skipping',
            service_request_id,
            sr.status,
        )
        return

    logger.info(
        'auto_reassign_dropped_service: auto-assigning request %s (client=%s, service=%s)',
        service_request_id,
        sr.client_id,
        sr.service_id,
    )

    try:
        consultant = assign_consultant_to_request(service_request_id)
    except Exception as exc:
        logger.exception(
            'auto_reassign_dropped_service: assignment error for request %s: %s',
            service_request_id,
            exc,
        )
        raise self.retry(exc=exc)

    if consultant:
        # Notify the new consultant
        _notify_new_consultant(sr, consultant)
        # Notify the client
        _notify_client_reassigned(sr, consultant)
        logger.info(
            'auto_reassign_dropped_service: request %s auto-assigned to consultant %s',
            service_request_id,
            consultant.id,
        )
    else:
        # No consultant available → ping admin
        _notify_admin_no_consultant(sr)
        logger.warning(
            'auto_reassign_dropped_service: no consultant available for request %s – admin notified',
            service_request_id,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _push_ws_notification(user_id: int, payload: dict):
    """Best-effort realtime push. Silently skips if Redis is unavailable."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f'user_{user_id}',
            {'type': 'notification_message', 'data': payload},
        )
    except Exception as ws_exc:
        logger.warning('WS push failed for user %s: %s', user_id, ws_exc)


def _notify_new_consultant(sr, consultant):
    """Send notification to the newly assigned consultant."""
    from notifications.models import Notification

    client_name = sr.client.get_full_name() or sr.client.username
    service_name = sr.service.title if sr.service else 'Service'

    notif = Notification.objects.create(
        recipient=consultant.user,
        category='service',
        title=f'New Service Assigned: {service_name}',
        message=(
            f'{client_name} has been assigned to you for {service_name}. '
            'A previous consultant had to step back. All documents are preserved. '
            'Please review the service details and contact the client.'
        ),
        link='/dashboard',
    )
    _push_ws_notification(consultant.user_id, {
        'id': notif.id,
        'type': 'NEW_NOTIFICATION',
        'category': notif.category,
        'title': notif.title,
        'message': notif.message,
        'link': notif.link,
        'created_at': notif.created_at.isoformat(),
        'is_read': False,
    })


def _notify_client_reassigned(sr, consultant):
    """Notify the client that a new consultant has been assigned."""
    from notifications.models import Notification

    service_name = sr.service.title if sr.service else 'Service'
    consultant_name = consultant.full_name

    notif = Notification.objects.create(
        recipient=sr.client,
        category='service',
        title=f'New Expert Assigned for {service_name}',
        message=(
            f'Great news! {consultant_name} has been assigned to handle your {service_name}. '
            'All your documents and progress are preserved. '
            'You can reach them via the Messages section.'
        ),
        link='/client',
    )
    _push_ws_notification(sr.client_id, {
        'id': notif.id,
        'type': 'NEW_NOTIFICATION',
        'category': notif.category,
        'title': notif.title,
        'message': notif.message,
        'link': notif.link,
        'created_at': notif.created_at.isoformat(),
        'is_read': False,
    })


def _notify_admin_no_consultant(sr):
    """
    Notify all active admin users when no consultant is available for a
    dropped service request.
    """
    from notifications.models import Notification
    from django.contrib.auth import get_user_model

    User = get_user_model()
    admins = User.objects.filter(is_staff=True, is_active=True)

    client_name = sr.client.get_full_name() or sr.client.username
    service_name = sr.service.title if sr.service else 'Service'

    for admin in admins:
        notif = Notification.objects.create(
            recipient=admin,
            category='service',
            title=f'⚠️ No Consultant Available: {service_name}',
            message=(
                f'Service request #{sr.id} ({service_name}) for {client_name} could not be '
                'auto-assigned after the 24-hour deadline. '
                'Please assign a consultant manually from the admin panel.'
            ),
            link='/admin/consultants/clientservicerequest/',
        )
        _push_ws_notification(admin.id, {
            'id': notif.id,
            'type': 'NEW_NOTIFICATION',
            'category': notif.category,
            'title': notif.title,
            'message': notif.message,
            'link': notif.link,
            'created_at': notif.created_at.isoformat(),
            'is_read': False,
        })
