from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from .models import ClientServiceRequest, ConsultantServiceExpertise
from core_auth.models import ClientProfile


@receiver(post_save, sender=ConsultantServiceExpertise)
def auto_add_consultant_to_topic(sender, instance, created, **kwargs):
    """
    When a consultant selects a service (e.g. 'Income Tax Filing'),
    auto-add them to all Topics linked to that service's category (e.g. 'Income Tax').
    This eliminates manual admin work for consultation topic assignment.
    """
    if created:
        from consultations.models import Topic
        category = instance.service.category
        topics = Topic.objects.filter(category=category)
        for topic in topics:
            topic.consultants.add(instance.consultant.user)
            print(f"✅ [Auto-Sync] Added {instance.consultant.full_name} to topic '{topic.name}'")


@receiver(post_delete, sender=ConsultantServiceExpertise)
def auto_remove_consultant_from_topic(sender, instance, **kwargs):
    """
    When a consultant loses their last service in a category,
    remove them from the matching Topic.
    """
    from consultations.models import Topic
    category = instance.service.category
    
    # Check if consultant still has any other services in this category
    still_has_services = ConsultantServiceExpertise.objects.filter(
        consultant=instance.consultant,
        service__category=category
    ).exists()
    
    if not still_has_services:
        topics = Topic.objects.filter(category=category)
        for topic in topics:
            topic.consultants.remove(instance.consultant.user)
            print(f"🔄 [Auto-Sync] Removed {instance.consultant.full_name} from topic '{topic.name}' (no more services in category)")



@receiver(post_save, sender=ClientServiceRequest)
def sync_consultant_to_client_profile(sender, instance, created, **kwargs):
    """
    When a consultant is assigned to a service request,
    automatically update the client's profile with this consultant.
    
    Logic: Most recent assignment wins - the client's primary consultant
    is always updated to the most recently assigned consultant.
    """
    if instance.assigned_consultant and instance.status not in ['completed', 'cancelled']:
        try:
            # Get or create the client profile
            client_profile, _ = ClientProfile.objects.get_or_create(
                user=instance.client,
                defaults={'assigned_consultant': instance.assigned_consultant.user}
            )
            
            # Update primary consultant if not set
            if not client_profile.assigned_consultant:
                client_profile.assigned_consultant = instance.assigned_consultant.user
                client_profile.save()
                print(f"✅ Set primary consultant: {instance.assigned_consultant.full_name} → Client: {instance.client.email}")
            else:
                # Even if already set, check if it's the same or if we should leave it
                print(f"ℹ️ Consultant assignment sync for {instance.client.email}")
                
        except Exception as e:
            print(f"Error syncing consultant assignment: {e}")
    else:
        # Service completed, cancelled, or consultant explicitly unassigned
        # Check if we should clear the ClientProfile assignment
        try:
            client_profile = ClientProfile.objects.filter(user=instance.client).first()
            if client_profile and client_profile.assigned_consultant:
                # Check if there are ANY active (non-completed/cancelled) requests with THIS consultant
                active_requests_exist = ClientServiceRequest.objects.filter(
                    client=instance.client,
                    assigned_consultant__user=client_profile.assigned_consultant
                ).exclude(status__in=['completed', 'cancelled']).exists()
                
                if not active_requests_exist:
                    # No more active services with this consultant - unassign from profile
                    client_profile.assigned_consultant = None
                    client_profile.save()
                    print(f"✅ Cleared consultant from ClientProfile: {instance.client.email} (No more active services)")
        except Exception as e:
            print(f"Error clearing consultant assignment: {e}")




@receiver(post_save, sender=ClientServiceRequest)
def create_pending_document_requests(sender, instance, **kwargs):
    """
    Automatically synchronize pending document requests in the client's vault
    with the service's requirements.
    """
    from document_vault.models import Document
    import re
    
    # Only process if service is active/non-terminal
    if instance.status in ['completed', 'cancelled', 'pending']:
        return

    client = instance.client
    service = instance.service
    
    # 1. Parse current requirements from the service definition
    required_list = []
    if service.documents_required:
        raw_list = re.split(r'[\n,;•]|\r\n', service.documents_required)
        for doc in raw_list:
            title = re.sub(r'^[ \-\•\*\d\.]+', '', doc).strip()
            if title and len(title) > 1:
                required_list.append(title)
    
    # 2. Get existing PENDING documents for this service/client
    # We match by description which contains the service title
    existing_pending = Document.objects.filter(
        client=client, 
        status='PENDING',
        description__icontains=service.title
    )
    
    # 3. Delete PENDING documents that are no longer in the required list
    # This cleans up old/outdated requirements if the service definition changed
    required_titles_lower = [t.lower() for t in required_list]
    for doc in existing_pending:
        if doc.title.lower() not in required_titles_lower:
            doc.delete()
            print(f"[Sync] Removed outdated requirement: {doc.title}")

    # 4. Add/Link missing requirements
    for doc_title in required_list:
        # Check if it exists in any status (avoid duplicate titles)
        doc = Document.objects.filter(
            client=client, 
            title__iexact=doc_title
        ).first()
        
        link_metadata = f"Required for {service.title}"
        
        if not doc:
            Document.objects.create(
                client=client,
                consultant=instance.assigned_consultant.user if instance.assigned_consultant else None,
                title=doc_title,
                description=link_metadata,
                status='PENDING'
            )
            print(f"[Sync] Added missing requirement: {doc_title}")
        else:
            # Document exists. Ensure it has the service link in description for auto-progression logic.
            needs_save = False
            
            if not doc.description or link_metadata not in doc.description:
                if doc.description:
                    doc.description = f"{doc.description} | {link_metadata}"
                else:
                    doc.description = link_metadata
                needs_save = True
                print(f"[Sync] Linked existing document '{doc.title}' to service '{service.title}' metadata.")
            
            # If it's a PENDING request but consultant is not set, update it
            if doc.status == 'PENDING' and not doc.consultant and instance.assigned_consultant:
                doc.consultant = instance.assigned_consultant.user
                needs_save = True
                
            if needs_save:
                doc.save()


@receiver(post_save, sender=ClientServiceRequest)
def cleanup_orphaned_pending_documents(sender, instance, created, **kwargs):
    """
    Automatically delete orphaned PENDING document requests when:
    1. Service status changes to 'completed' or 'cancelled'
    2. Consultant is unassigned from a service
    
    This ensures the database stays clean and clients don't see outdated document requests.
    """
    from document_vault.models import Document
    
    # Skip if this is a new record
    if created:
        return
    
    # Check if service is no longer active (or unassigned back to pending)
    if instance.status in ['completed', 'cancelled', 'pending']:
        # Delete PENDING documents that were created for this service
        # Match by description containing the service title
        deleted_count = Document.objects.filter(
            client=instance.client,
            status='PENDING',
            description__icontains=instance.service.title
        ).delete()[0]
        
        if deleted_count > 0:
            print(f"[Cleanup] Deleted {deleted_count} orphaned PENDING documents for completed/cancelled service: {instance.service.title}")
@receiver(pre_save, sender=ClientServiceRequest)
def log_status_change(sender, instance, **kwargs):
    """
    Log status changes to the Activity Timeline.
    Detects change by comparing current status with the one in database.
    """
    if instance.id:
        try:
            old_instance = ClientServiceRequest.objects.get(id=instance.id)
            if old_instance.status != instance.status:
                from activity_timeline.models import Activity
                
                # Actor is the assigned consultant, or the client if not assigned
                actor = instance.assigned_consultant.user if instance.assigned_consultant else instance.client
                
                Activity.objects.create(
                    actor=actor,
                    target_user=instance.client,
                    activity_type='service_status',
                    title=f"Service status: {instance.get_status_display()}",
                    description=f"'{instance.service.title}' status changed from {old_instance.get_status_display()} to {instance.get_status_display()}.",
                    content_object=instance,
                    metadata={
                        'old_status': old_instance.status,
                        'new_status': instance.status,
                        'service_title': instance.service.title
                    }
                )
                print(f"Logged status change for {instance.client.email}: {old_instance.status} -> {instance.status}")
        except Exception as e:
            print(f"Error logging status change to timeline: {e}")

@receiver(post_delete, sender=ClientServiceRequest)
def cleanup_orphaned_pending_documents_on_delete(sender, instance, **kwargs):
    """
    Delete pending document requests when a service request is deleted
    from the system (e.g. via admin).
    """
    from document_vault.models import Document
    
    deleted_count = Document.objects.filter(
        client=instance.client,
        status='PENDING',
        description__icontains=instance.service.title
    ).delete()[0]
    
    if deleted_count > 0:
        print(f"[Cleanup] Deleted {deleted_count} orphaned PENDING documents due to service deletion: {instance.service.title}")

@receiver(post_save, sender=ClientServiceRequest)
def log_new_service_request(sender, instance, created, **kwargs):
    """Log when a new service request is created."""
    if created:
        try:
            from activity_timeline.models import Activity
            Activity.objects.create(
                actor=instance.client,
                target_user=instance.client,
                activity_type='service_new',
                title="New service purchased",
                description=f"Client purchased service: {instance.service.title}",
                content_object=instance
            )
        except Exception as e:
            print(f"Error logging new service request: {e}")


@receiver(post_save, sender='document_vault.Document')
def auto_progress_to_wip(sender, instance, **kwargs):
    """
    Automatically move a service request to 'wip' status phase
    when all its associated required documents are 'VERIFIED'.
    """
    if instance.status != 'VERIFIED':
        return

    from document_vault.models import Document
    client = instance.client
    
    # Extract service title from description: "Required for <Service Title>"
    import re
    desc = instance.description or ''
    match = re.search(r'Required for (.*?)(?: \| REJECTION|$)', desc)
    if not match:
        print(f"⚠️ [Signal] Verified document '{instance.title}' has no service metadata in description.")
        return
        
    service_title = match.group(1).strip()
    print(f"🔍 [Signal] Verified doc for service: '{service_title}'. Checking other requirements...")
    
    # Find active service request for this client matching the title
    # We target active phases where document collection happens
    target_phases = ['assigned', 'doc_pending', 'under_review', 'under_query']
    service_req = ClientServiceRequest.objects.filter(
        client=client,
        service__title__iexact=service_title,
        status__in=target_phases
    ).first()
    
    if service_req:
        # Check all documents for this specific service request 
        # using the same metadata prefix in description
        all_reqs = Document.objects.filter(
            client=client,
            description__icontains=f"Required for {service_title}"
        )
        
        non_verified = all_reqs.exclude(status='VERIFIED')
        non_verified_count = non_verified.count()
        
        if all_reqs.exists() and non_verified_count == 0:
            # All documents are confirmed! Move to WIP.
            service_req.status = 'wip'
            service_req.save()
            print(f"🚀 [Signal] Auto-progressed {client.email}'s {service_title} to WIP (All {all_reqs.count()} docs verified)")
        else:
            titles = ", ".join([d.title for d in non_verified[:3]])
            print(f"⏳ [Signal] {non_verified_count} docs still not verified for {service_title} (e.g., {titles})")
    else:
        print(f"ℹ️ [Signal] No active {target_phases} request found for '{service_title}'.")


@receiver(post_save, sender='document_vault.SharedReport')
def auto_progress_to_review(sender, instance, created, **kwargs):
    """
    Automatically move client service requests to 'final_review' 
    when a report is shared or updated by the consultant.
    """
    # Run on both create and update to ensure resharing works

    consultant_user = instance.consultant
    client_user = instance.client

    # Find the service request that is currently in WIP state
    # or another active state that would precede the review phase.
    service_reqs = ClientServiceRequest.objects.filter(
        client=client_user,
        assigned_consultant__user=consultant_user,
        status__in=['wip', 'under_review', 'under_query', 'revision_pending']
    )

    for req in service_reqs:
        req.status = 'final_review'
        req.save()
        print(f"✅ [Signal] Auto-moved service '{req.service.title}' to Final Review due to report upload.")
