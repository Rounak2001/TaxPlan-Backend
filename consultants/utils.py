"""
Utility to resolve the active consultant for a given client user.

Replaces the old ClientProfile.assigned_consultant FK with a dynamic
lookup through active ClientServiceRequest rows.
"""


def get_active_consultant_for_client(client_user):
    """
    Return the consultant *User* for a client based on their most recently
    updated active service request, or None if no active service exists.
    """
    from consultants.models import ClientServiceRequest

    active_req = (
        ClientServiceRequest.objects.filter(
            client=client_user,
            assigned_consultant__isnull=False,
        )
        .exclude(status__in=['completed', 'cancelled'])
        .order_by('-updated_at')
        .select_related('assigned_consultant__user')
        .first()
    )
    return active_req.assigned_consultant.user if active_req else None
