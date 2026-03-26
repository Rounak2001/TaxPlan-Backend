from rest_framework import viewsets, permissions, status, decorators
from rest_framework.response import Response
from django.utils import timezone
from django.db import models, connection
import logging
from .models import Document, SharedReport, LegalNotice, Folder, DocumentAccess
from .serializers import DocumentSerializer, DocumentUploadSerializer, SharedReportSerializer, LegalNoticeSerializer, FolderSerializer
from core_auth.serializers import IsConsultantUser, IsClientUser
from consultants.models import ClientServiceRequest
from core_auth.utils import get_active_profile

logger = logging.getLogger(__name__)

def create_system_folders(client_user):
    """Creates default system folders for a client."""
    system_folders = ["KYC", "Bank Details", "GST Details", "Company Docs"]
    for folder_name in system_folders:
        Folder.objects.get_or_create(
            client=client_user,
            name=folder_name,
            defaults={'is_system': True}
        )


def _has_document_access_table():
    """
    Backward-compatible guard during rollout. If migration isn't applied yet,
    we avoid crashing and default to safest behavior.
    """
    try:
        return 'vault_document_access' in connection.introspection.table_names()
    except Exception:
        return False

class FolderViewSet(viewsets.ModelViewSet):
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = get_active_profile(self.request)
        client_id = self.request.query_params.get('client_id')
        
        if user.role == 'CONSULTANT':
            # Get clients assigned via service requests
            service_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user
            ).values_list('client_id', flat=True)
            
            if client_id:
                # Consultant viewing folders for a specific client
                return Folder.objects.filter(
                    client_id=client_id,
                    client_id__in=service_client_ids
                ).distinct()
            
            # Default to all folders for any service-assigned clients
            return Folder.objects.filter(
                client_id__in=service_client_ids
            ).distinct()
        
        # Clients see their own folders
        return Folder.objects.filter(client=user)

    def perform_create(self, serializer):
        user = get_active_profile(self.request)
        client_id = self.request.data.get('client')
        name = serializer.validated_data.get('name')
        
        target_client = user
        if user.role == 'CONSULTANT':
            # Security: Ensure client is assigned via active service
            from core_auth.models import ClientProfile
            
            is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
            
            if not is_service:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This client is not assigned to you.")
            
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                target_client = User.objects.get(id=client_id)
            except (User.DoesNotExist, ValueError):
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"client": "Invalid client ID"})

        # Ensure system folders exist
        create_system_folders(target_client)

        # Check for duplicate name
        from rest_framework.exceptions import ValidationError
        if Folder.objects.filter(client=target_client, name=name).exists():
            raise ValidationError({"name": f"A folder named '{name}' already exists."})

        if user.role == 'CONSULTANT':
            serializer.save(created_by=user, client=target_client)
        else:
            serializer.save(client=user, created_by=user)

    def destroy(self, request, *args, **kwargs):
        folder = self.get_object()
        if folder.is_system:
            return Response({'error': 'System folders cannot be deleted'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.select_related('client', 'consultant', 'folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = get_active_profile(self.request)
        folder_id = self.request.query_params.get('folder_id')
        
        if user.role == 'CONSULTANT':
            # Get clients assigned via active service requests
            service_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user,
                status__in=ClientServiceRequest.ACTIVE_STATUSES
            ).values_list('client_id', flat=True)
            
            # Consultants see docs for clients with active service assignments
            qs = Document.objects.select_related('client', 'consultant', 'folder').filter(
                client_id__in=service_client_ids
            ).distinct()

            if _has_document_access_table():
                qs = qs.filter(access_grants__consultant=user).distinct()
            else:
                # Safe default: no explicit grants table means no consultant access.
                qs = qs.none()
        else:
            # Clients see their own docs, but filter out PENDING requests from unassigned consultants
            # Get consultants assigned via active services
            service_consultant_ids = ClientServiceRequest.objects.filter(
                client=user,
                status__in=ClientServiceRequest.ACTIVE_STATUSES
            ).values_list('assigned_consultant__user_id', flat=True)
            
            # Build list of valid consultant IDs
            valid_consultant_ids = list(service_consultant_ids)
            
            # Filter: Show all docs EXCEPT pending requests from unassigned consultants
            qs = Document.objects.select_related('client', 'consultant', 'folder').filter(client=user).filter(
                models.Q(status__in=['UPLOADED', 'VERIFIED', 'REJECTED']) |  # Non-pending docs
                models.Q(status='PENDING', consultant_id__in=valid_consultant_ids) |  # Pending from assigned consultants
                models.Q(status='PENDING', consultant__isnull=True)  # Pending without consultant (client-initiated)
            )
            
        if folder_id:
            qs = qs.filter(folder_id=folder_id)
        return qs

    def _validate_folder_client(self, folder_id, client):
        """Helper to ensure folder belongs to the client."""
        if folder_id:
            try:
                folder = Folder.objects.get(id=folder_id)
                if folder.client != client:
                    from rest_framework.exceptions import ValidationError
                    raise ValidationError({"folder": "This folder does not belong to the correct client."})
                return folder
            except Folder.DoesNotExist:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"folder": "Invalid folder ID."})
        return None

    def perform_create(self, serializer):
        user = get_active_profile(self.request)
        folder_id = self.request.data.get('folder')
        client_id = self.request.data.get('client')

        if user.role == 'CONSULTANT':
            # Security: Ensure client is assigned via active service
            from core_auth.models import ClientProfile
            from consultants.models import ClientServiceRequest
            
            is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
            
            if not is_service:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This client is not assigned to you.")
            
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                target_client = User.objects.get(id=client_id)
            except (User.DoesNotExist, ValueError):
                from rest_framework.exceptions import ValidationError
                raise ValidationError({"client": "Invalid client ID"})

            # Ensure system folders exist for this client
            create_system_folders(target_client)
            
            # Validate folder belongs to target client
            self._validate_folder_client(folder_id, target_client)

            document = serializer.save(consultant=user, client=target_client, folder_id=folder_id, status='PENDING')
            # Auto-grant: the requesting consultant can always see the request they created
            if _has_document_access_table():
                DocumentAccess.objects.get_or_create(document=document, consultant=user)
        else:
            # Client creating a proactive upload
            # Ensure system folders exist
            create_system_folders(user)
            # Validate folder belongs to this client
            self._validate_folder_client(folder_id, user)
            
            serializer.save(client=user, folder_id=folder_id, status='UPLOADED', uploaded_at=timezone.now())

    @decorators.action(detail=True, methods=['get'], url_path='access', permission_classes=[IsClientUser])
    def list_access(self, request, pk=None):
        """
        Returns the list of all consultants assigned to this client,
        with a flag indicating whether each has been granted access to this document.
        """
        try:
            document = self.get_object()
            active_user = get_active_profile(request)

            if not _has_document_access_table():
                return Response(
                    {'error': 'Document access feature is not ready. Please run migrations.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )

            if document.client != active_user:
                return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

            assigned = ClientServiceRequest.objects.filter(
                client=active_user,
                status__in=ClientServiceRequest.ACTIVE_STATUSES
            ).select_related('assigned_consultant__user', 'service').exclude(
                assigned_consultant__isnull=True
            )

            granted_ids = set(document.access_grants.values_list('consultant_id', flat=True))
            seen_consultant_ids = set()
            result = []

            for req in assigned:
                consultant_profile = req.assigned_consultant
                if not consultant_profile or not consultant_profile.user:
                    continue
                consultant_user = consultant_profile.user
                if consultant_user.id in seen_consultant_ids:
                    continue

                seen_consultant_ids.add(consultant_user.id)
                result.append({
                    'consultant_id': consultant_user.id,
                    'name': consultant_user.get_full_name() or consultant_user.username,
                    'email': consultant_user.email,
                    'has_access': consultant_user.id in granted_ids,
                    'service_title': req.service.title if req.service else '',
                })

            return Response(result)
        except Exception:
            logger.exception("Failed to list document access for document %s", pk)
            return Response(
                {'error': 'Failed to load consultant access list'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @decorators.action(detail=True, methods=['post'], url_path='grant-access', permission_classes=[IsClientUser])
    def grant_access(self, request, pk=None):
        """
        Syncs access grants for this document.
        Body: { "consultant_ids": [1, 2, 3] }
        Adds missing grants and removes revoked ones atomically.
        """
        try:
            document = self.get_object()
            active_user = get_active_profile(request)

            if not _has_document_access_table():
                return Response(
                    {'error': 'Document access feature is not ready. Please run migrations.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )

            if document.client != active_user:
                return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

            consultant_ids = request.data.get('consultant_ids', [])
            if not isinstance(consultant_ids, list):
                return Response({'error': 'consultant_ids must be a list'}, status=status.HTTP_400_BAD_REQUEST)

            valid_ids = set(
                ClientServiceRequest.objects.filter(
                    client=active_user,
                    status__in=ClientServiceRequest.ACTIVE_STATUSES
                ).exclude(
                    assigned_consultant__isnull=True
                ).values_list('assigned_consultant__user_id', flat=True)
            )

            try:
                requested_ids = set(int(i) for i in consultant_ids)
            except (TypeError, ValueError):
                return Response({'error': 'consultant_ids must contain valid numeric IDs'}, status=status.HTTP_400_BAD_REQUEST)

            invalid = requested_ids - valid_ids
            if invalid:
                return Response({'error': f'Invalid consultant IDs: {sorted(list(invalid))}'}, status=status.HTTP_400_BAD_REQUEST)

            for consultant_id in requested_ids:
                DocumentAccess.objects.get_or_create(document=document, consultant_id=consultant_id)

            DocumentAccess.objects.filter(document=document).exclude(consultant_id__in=requested_ids).delete()

            granted_ids = list(document.access_grants.values_list('consultant_id', flat=True))
            return Response({'granted_consultant_ids': granted_ids})
        except Exception:
            logger.exception("Failed to update document access for document %s", pk)
            return Response(
                {'error': 'Failed to update consultant access'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def perform_update(self, serializer):
        user = get_active_profile(self.request)
        # For updates, client and consultant are read-only in serializer, 
        # but we must still ensure the NEW folder belongs to the document's client.
        document = self.get_object()
        folder_id = self.request.data.get('folder')
        
        # If folder is being changed
        if folder_id and 'folder' in self.request.data:
            self._validate_folder_client(folder_id, document.client)
            
        serializer.save()

    @decorators.action(detail=True, methods=['post'], url_path='upload', permission_classes=[IsClientUser])
    def upload_file(self, request, pk=None):
        """
        Allows a client to upload a file to a PENDING document request.
        """
        document = self.get_object()
            
        # Security check: only the assigned client can upload
        active_user = get_active_profile(request)
        if document.client != active_user:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)
            
        serializer = DocumentUploadSerializer(document, data=request.data, partial=True)
        if serializer.is_valid():
            # Explicitly handle file_password if provided
            file_password = request.data.get('file_password')
            serializer.save(
                status='UPLOADED', 
                uploaded_at=timezone.now(),
                file_password=file_password if file_password else document.file_password
            )
            return Response(DocumentSerializer(document, context={'request': request}).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @decorators.action(detail=True, methods=['post'], url_path='review', permission_classes=[IsConsultantUser])
    def review_document(self, request, pk=None):
        """
        Allows a consultant to VERIFY or REJECT a document.
        Optionally accepts a 'rejection_reason' for rejected documents.
        """
        document = self.get_object()
        new_status = request.data.get('status')
        rejection_reason = request.data.get('rejection_reason', '')
        
        if new_status not in ['VERIFIED', 'REJECTED']:
            return Response({'error': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
        
        document.status = new_status
        
        # Store rejection reason without destroying original description (metadata)
        if new_status == 'REJECTED' and rejection_reason:
            if document.description:
                document.description = f"{document.description} | REJECTION REASON: {rejection_reason}"
            else:
                document.description = f"REJECTION REASON: {rejection_reason}"
        
        document.save()
        return Response(DocumentSerializer(document, context={'request': request}).data)

    @decorators.action(detail=False, methods=['get'], url_path='pending-count', permission_classes=[IsClientUser])
    def pending_count(self, request):
        """
        Returns the count of pending and rejected document requests for the authenticated client.
        """
        user = get_active_profile(request)
        pending = Document.objects.filter(client=user, status='PENDING').count()
        rejected = Document.objects.filter(client=user, status='REJECTED').count()
        
        return Response({
            'count': pending + rejected,
            'pending': pending,
            'rejected': rejected
        })


class SharedReportViewSet(viewsets.ModelViewSet):
    """
    ViewSet for consultant-to-client report sharing.
    - Consultants: can create, list (their shared reports), delete
    - Clients: can list (reports shared with them)
    """
    serializer_class = SharedReportSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = get_active_profile(self.request)
        if user.role == 'CONSULTANT':
            # Get clients assigned via active service requests
            service_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user,
                status__in=ClientServiceRequest.ACTIVE_STATUSES
            ).values_list('client_id', flat=True)
            
            # Consultants see reports for clients who are:
            # 1. Primary assigned clients OR
            # 2. Clients with active service assignments
            return SharedReport.objects.filter(
                client_id__in=service_client_ids
            ).filter(consultant=user).distinct()
        # Clients see reports shared with them
        return SharedReport.objects.filter(client=user)

    def perform_create(self, serializer):
        user = get_active_profile(self.request)
        if user.role != 'CONSULTANT':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only consultants can share reports.")
        
        client_id = self.request.data.get('client')
        # Security: Ensure client is assigned to this consultant via active service
        from core_auth.models import ClientProfile
        is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
        if not is_service:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("This client is not assigned to you.")
            
        serializer.save(consultant=user)

    def destroy(self, request, *args, **kwargs):
        """Only consultants can delete shared reports."""
        user = get_active_profile(request)
        if user.role != 'CONSULTANT':
            return Response({'error': 'Only consultants can delete reports'}, status=status.HTTP_403_FORBIDDEN)
        
        report = self.get_object()
        if report.consultant != user:
            return Response({'error': 'You can only delete your own shared reports'}, status=status.HTTP_403_FORBIDDEN)
        
        return super().destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=['post'], url_path='mark-read', permission_classes=[permissions.IsAuthenticated])
    def mark_read(self, request, pk=None):
        """Mark a specific shared report as read by the client."""
        report = self.get_object()
        active_user = get_active_profile(request)
        if report.client != active_user:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)
        report.is_read = True
        report.save(update_fields=['is_read'])
        return Response({'status': 'ok', 'is_read': True})

    @decorators.action(detail=False, methods=['get'], url_path='unread-count', permission_classes=[permissions.IsAuthenticated])
    def unread_count(self, request):
        """Returns the count of unread shared reports for the authenticated client."""
        user = get_active_profile(request)
        if user.role != 'CLIENT':
            return Response({'count': 0})
        count = SharedReport.objects.filter(client=user, is_read=False).count()
        return Response({'count': count})


class LegalNoticeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Legal Notices / Orders / Communications.
    Allows both consultants and clients to upload and manage official communications.
    """
    serializer_class = LegalNoticeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = get_active_profile(self.request)
        if user.role == 'CONSULTANT':
            # Get clients assigned via active service requests
            service_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user,
                status__in=ClientServiceRequest.ACTIVE_STATUSES
            ).values_list('client_id', flat=True)
            
            # Consultants see notices for clients who are:
            # 1. Primary assigned clients OR
            # 2. Clients with active service assignments
            return LegalNotice.objects.filter(
                client_id__in=service_client_ids
            ).filter(consultant=user).distinct()
        # Clients see notices for them or uploaded by them
        return LegalNotice.objects.filter(client=user)

    def perform_create(self, serializer):
        user = get_active_profile(self.request)
        if user.role == 'CONSULTANT':
            client_id = self.request.data.get('client')
            from core_auth.models import ClientProfile
            is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
            if not is_service:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This client is not assigned to you.")
            
            serializer.save(consultant=user, uploaded_by=user)
        else:
            # Client uploading a notice
            from consultants.utils import get_active_consultant_for_client
            active_consultant = get_active_consultant_for_client(user)
            if not active_consultant:
                from rest_framework.exceptions import ValidationError
                raise ValidationError("You don't have an assigned consultant yet.")
            
            serializer.save(client=user, consultant=active_consultant, uploaded_by=user)

    @decorators.action(detail=True, methods=['post'], url_path='resolve')
    def toggle_resolved(self, request, pk=None):
        notice = self.get_object()
        notice.is_resolved = not notice.is_resolved
        notice.save()
        return Response({'status': 'success', 'is_resolved': notice.is_resolved})

