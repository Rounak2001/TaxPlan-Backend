from rest_framework import viewsets, permissions, status, decorators
from rest_framework.response import Response
from django.utils import timezone
from django.db import models
from .models import Document, SharedReport, LegalNotice, Folder
from .serializers import DocumentSerializer, DocumentUploadSerializer, SharedReportSerializer, LegalNoticeSerializer, FolderSerializer
from core_auth.serializers import IsConsultantUser, IsClientUser
from consultants.models import ClientServiceRequest

def create_system_folders(client_user):
    """Creates default system folders for a client."""
    system_folders = ["KYC", "Bank Details", "GST Details", "Company Docs"]
    for folder_name in system_folders:
        Folder.objects.get_or_create(
            client=client_user,
            name=folder_name,
            defaults={'is_system': True}
        )

class FolderViewSet(viewsets.ModelViewSet):
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        client_id = self.request.query_params.get('client_id')
        
        if user.role == 'CONSULTANT':
            # Get clients assigned via service requests
            service_client_ids = ClientServiceRequest.objects.filter(
                assigned_consultant__user=user
            ).values_list('client_id', flat=True)
            
            if client_id:
                # Consultant viewing folders for a specific client
                # Allow if primary OR assigned via service
                return Folder.objects.filter(
                    client_id=client_id
                ).filter(
                    models.Q(client__client_profile__assigned_consultant=user) |
                    models.Q(client_id__in=service_client_ids)
                ).distinct()
            
            # Default to all folders for any assigned/service clients
            return Folder.objects.filter(
                models.Q(client__client_profile__assigned_consultant=user) |
                models.Q(client_id__in=service_client_ids)
            ).distinct()
        
        # Clients see their own folders
        return Folder.objects.filter(client=user)

    def perform_create(self, serializer):
        user = self.request.user
        client_id = self.request.data.get('client')
        name = serializer.validated_data.get('name')
        
        target_client = user
        if user.role == 'CONSULTANT':
            # Security: Ensure client is assigned (Primary or Service)
            from core_auth.models import ClientProfile
            
            is_primary = ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists()
            is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
            
            if not (is_primary or is_service):
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
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        folder_id = self.request.query_params.get('folder_id')
        
        if user.role == 'CONSULTANT':
            # Consultants see docs for their assigned clients
            qs = Document.objects.filter(client__client_profile__assigned_consultant=user)
        else:
            # Clients see only their own docs
            qs = Document.objects.filter(client=user)
            
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
        user = self.request.user
        folder_id = self.request.data.get('folder')
        client_id = self.request.data.get('client')

        if user.role == 'CONSULTANT':
            # Security: Ensure client is assigned (Primary or Service)
            from core_auth.models import ClientProfile
            from consultants.models import ClientServiceRequest
            
            is_primary = ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists()
            is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
            
            if not (is_primary or is_service):
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
                
            serializer.save(consultant=user, client=target_client, folder_id=folder_id, status='PENDING')
        else:
            # Client creating a proactive upload
            # Ensure system folders exist
            create_system_folders(user)
            # Validate folder belongs to this client
            self._validate_folder_client(folder_id, user)
            
            serializer.save(client=user, folder_id=folder_id, status='UPLOADED', uploaded_at=timezone.now())

    def perform_update(self, serializer):
        user = self.request.user
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
        if document.client != request.user:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)
            
        serializer = DocumentUploadSerializer(document, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save(status='UPLOADED', uploaded_at=timezone.now())
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
        
        # Store rejection reason in description field if rejecting
        if new_status == 'REJECTED' and rejection_reason:
            document.description = rejection_reason
        
        document.save()
        return Response(DocumentSerializer(document, context={'request': request}).data)

    @decorators.action(detail=False, methods=['get'], url_path='pending-count', permission_classes=[IsClientUser])
    def pending_count(self, request):
        """
        Returns the count of pending and rejected document requests for the authenticated client.
        """
        user = request.user
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
        user = self.request.user
        if user.role == 'CONSULTANT':
            # Consultants see reports they've shared
            return SharedReport.objects.filter(consultant=user)
        # Clients see reports shared with them
        return SharedReport.objects.filter(client=user)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'CONSULTANT':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only consultants can share reports.")
        
        client_id = self.request.data.get('client')
        # Security: Ensure client is assigned to this consultant
        from core_auth.models import ClientProfile
        if not ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("This client is not assigned to you.")
            
        serializer.save(consultant=user)

    def destroy(self, request, *args, **kwargs):
        """Only consultants can delete shared reports."""
        if request.user.role != 'CONSULTANT':
            return Response({'error': 'Only consultants can delete reports'}, status=status.HTTP_403_FORBIDDEN)
        
        report = self.get_object()
        if report.consultant != request.user:
            return Response({'error': 'You can only delete your own shared reports'}, status=status.HTTP_403_FORBIDDEN)
        
        return super().destroy(request, *args, **kwargs)


class LegalNoticeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Legal Notices / Orders / Communications.
    Allows both consultants and clients to upload and manage official communications.
    """
    serializer_class = LegalNoticeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'CONSULTANT':
            # Consultants see notices for their assigned clients and notices shared by them
            return LegalNotice.objects.filter(consultant=user)
        # Clients see notices for them or uploaded by them
        return LegalNotice.objects.filter(client=user)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'CONSULTANT':
            client_id = self.request.data.get('client')
            from core_auth.models import ClientProfile
            if not ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists():
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This client is not assigned to you.")
            
            serializer.save(consultant=user, uploaded_by=user)
        else:
            # Client uploading a notice
            from core_auth.models import ClientProfile
            try:
                profile = user.client_profile
                if not profile.assigned_consultant:
                    from rest_framework.exceptions import ValidationError
                    raise ValidationError("You don't have an assigned consultant yet.")
                
                serializer.save(client=user, consultant=profile.assigned_consultant, uploaded_by=user)
            except ClientProfile.DoesNotExist:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Complete your profile first.")

    @decorators.action(detail=True, methods=['post'], url_path='resolve')
    def toggle_resolved(self, request, pk=None):
        notice = self.get_object()
        notice.is_resolved = not notice.is_resolved
        notice.save()
        return Response({'status': 'success', 'is_resolved': notice.is_resolved})

