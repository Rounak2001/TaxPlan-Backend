from rest_framework import viewsets, permissions, status, decorators
from rest_framework.response import Response
from django.utils import timezone
from .models import Document
from .serializers import DocumentSerializer, DocumentUploadSerializer
from core_auth.serializers import IsConsultantUser, IsClientUser

class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'CONSULTANT':
            # Consultants see docs for their assigned clients
            return Document.objects.filter(client__client_profile__assigned_consultant=user)
        # Clients see only their own docs
        return Document.objects.filter(client=user)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'CONSULTANT':
            # Consultant creating a request.
            client_id = self.request.data.get('client')
            # Security: Ensure client is assigned to this consultant
            from core_auth.models import ClientProfile
            if not ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists():
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("This client is not assigned to you.")
                
            serializer.save(consultant=user, client_id=client_id, status='PENDING')
        else:
            # Client creating a proactive upload
            serializer.save(client=user, status='UPLOADED', uploaded_at=timezone.now())

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
        """
        document = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in ['VERIFIED', 'REJECTED']:
            return Response({'error': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
            
        document.status = new_status
        document.save()
        return Response(DocumentSerializer(document, context={'request': request}).data)
