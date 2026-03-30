from io import BytesIO
import logging
import mimetypes
import os

from django.db import models, connection
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import viewsets, permissions, status, decorators
from rest_framework.response import Response

from notifications.models import Notification
from .models import Document, SharedReport, LegalNotice, Folder, DocumentAccess, DocumentDownloadLog
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


def _safe_display_name(user):
    return (user.get_full_name() or user.username or "Consultant").strip()


def _resolve_notification_phone(user):
    """
    Resolve a notification phone number and gracefully support sub-accounts.
    """
    phone = getattr(user, 'phone_number', None)
    if not phone and getattr(user, 'parent_account_id', None):
        phone = getattr(user.parent_account, 'phone_number', None)
    return phone


def _build_download_alert_message(document, consultant, purpose):
    consultant_name = _safe_display_name(consultant)
    return (
        f"Consultant {consultant_name} downloaded your document {document.title}. "
        f"Purpose: {purpose}"
    )


def _consultant_has_document_access(document, consultant):
    if not _has_document_access_table():
        return False
    return DocumentAccess.objects.filter(document=document, consultant=consultant).exists()


def _build_preview_watermark_text(consultant):
    consultant_name = _safe_display_name(consultant)
    return f"CONFIDENTIAL - PREVIEW ONLY - {consultant_name}"


def _read_document_bytes(document):
    document.file.open('rb')
    try:
        return document.file.read()
    finally:
        document.file.close()


def _generate_image_preview_bytes(source_bytes, watermark_text):
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    image = Image.open(BytesIO(source_bytes))
    image = ImageOps.exif_transpose(image)
    target_size = (max(1, image.width // 2), max(1, image.height // 2))
    image = image.resize(target_size, Image.LANCZOS).convert('RGBA')

    overlay = Image.new('RGBA', image.size, (255, 255, 255, 0))
    font_size = max(20, min(image.size) // 22)

    font = None
    for font_name in ("DejaVuSans-Bold.ttf", "Arial.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe_draw = ImageDraw.Draw(Image.new('RGBA', (8, 8), (255, 255, 255, 0)))
    text_bbox = probe_draw.textbbox((0, 0), watermark_text, font=font)
    text_width = max(1, text_bbox[2] - text_bbox[0])
    text_height = max(1, text_bbox[3] - text_bbox[1])

    pattern_size = (image.width * 2, image.height * 2)
    pattern = Image.new('RGBA', pattern_size, (255, 255, 255, 0))
    pattern_draw = ImageDraw.Draw(pattern)
    step_x = max(text_width + 120, image.width // 3)
    step_y = max(text_height + 90, image.height // 4)

    # Subtle two-pass text (light underlay + darker foreground) improves legibility
    # while keeping a clean, professional watermark appearance.
    underlay_fill = (255, 255, 255, 45)
    main_fill = (28, 44, 64, 52)
    for y in range(-image.height // 2, pattern_size[1], step_y):
        x_offset = 0 if ((y // step_y) % 2 == 0) else step_x // 2
        for x in range(-image.width // 2, pattern_size[0], step_x):
            pattern_draw.text((x + x_offset + 2, y + 2), watermark_text, font=font, fill=underlay_fill)
            pattern_draw.text((x + x_offset, y), watermark_text, font=font, fill=main_fill)

    rotated_pattern = pattern.rotate(30, expand=True)
    crop_left = max(0, (rotated_pattern.width - image.width) // 2)
    crop_top = max(0, (rotated_pattern.height - image.height) // 2)
    tiled_overlay = rotated_pattern.crop((
        crop_left,
        crop_top,
        crop_left + image.width,
        crop_top + image.height,
    ))
    overlay.alpha_composite(tiled_overlay, (0, 0))

    merged = Image.alpha_composite(image, overlay).convert('RGB')
    out = BytesIO()
    merged.save(out, format='JPEG', quality=60, optimize=True)
    return out.getvalue(), 'image/jpeg'


def _build_watermark_pdf_page(width, height, watermark_text):
    from pypdf import PdfReader
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))

    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(30)
    c.setFont("Helvetica-Bold", max(16, int(min(width, height) * 0.03)))
    c.setFillColor(Color(0.18, 0.26, 0.36, alpha=0.12))

    step_x = max(260, int(width * 0.32))
    step_y = max(140, int(height * 0.18))

    start_x = -int(width * 1.1)
    end_x = int(width * 1.1)
    start_y = -int(height * 1.1)
    end_y = int(height * 1.1)

    row = 0
    for y in range(start_y, end_y, step_y):
        row += 1
        offset = 0 if row % 2 == 0 else step_x // 2
        for x in range(start_x, end_x, step_x):
            c.drawString(x + offset, y, watermark_text)

    c.restoreState()
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]


def _generate_pdf_preview_bytes(source_bytes, watermark_text):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(BytesIO(source_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        watermark_page = _build_watermark_pdf_page(width, height, watermark_text)
        page.merge_page(watermark_page)
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue(), 'application/pdf'

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

    @decorators.action(detail=True, methods=['post'], url_path='request-access', permission_classes=[IsConsultantUser])
    def request_access(self, request, pk=None):
        """
        Consultant asks client to grant access for a locked document.
        """
        try:
            document = self.get_object()
            consultant = get_active_profile(request)
            if consultant.role != 'CONSULTANT':
                return Response({'error': 'Only consultants can request access.'}, status=status.HTTP_403_FORBIDDEN)

            if _consultant_has_document_access(document, consultant):
                return Response({'message': 'Access already granted for this document.'}, status=status.HTTP_200_OK)

            note = str(request.data.get('note', '')).strip()
            consultant_name = _safe_display_name(consultant)
            title = "Access Request for Document"
            message = (
                f"Consultant {consultant_name} requested access to your document '{document.title}'. "
                "To grant access: Client Vault > Records > Manage Access and unlock this document."
            )
            if note:
                message = f"{message} Note: {note}"

            try:
                from notifications.signals import create_and_push_notification
                create_and_push_notification(
                    recipient=document.client,
                    category='document',
                    title=title,
                    message=message,
                    link='/client/vault?tab=records',
                )
            except Exception:
                logger.exception("Failed to push access request notification for document %s", document.id)
                Notification.objects.create(
                    recipient=document.client,
                    category='document',
                    title=title,
                    message=message,
                    link='/client/vault?tab=records',
                )

            return Response({'message': 'Access request sent to client.'}, status=status.HTTP_200_OK)
        except Exception:
            logger.exception("Failed to request document access for document %s", pk)
            return Response({'error': 'Failed to send access request.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

    @decorators.action(detail=True, methods=['post'], url_path='download', permission_classes=[IsConsultantUser])
    def download_document(self, request, pk=None):
        """
        Logs mandatory download purpose and returns a secure download URL or file stream.
        """
        document = self.get_object()
        consultant = get_active_profile(request)

        if consultant.role != 'CONSULTANT':
            return Response({'error': 'Only consultants can download through this endpoint.'}, status=status.HTTP_403_FORBIDDEN)

        if not document.file:
            return Response({'error': 'No file available for download.'}, status=status.HTTP_400_BAD_REQUEST)

        if not _consultant_has_document_access(document, consultant):
            return Response({'error': 'Access denied. Client has not granted access to this document.'}, status=status.HTTP_403_FORBIDDEN)

        purpose = str(request.data.get('purpose', '')).strip()
        if len(purpose) < 10:
            return Response({'error': 'Purpose must be at least 10 characters.'}, status=status.HTTP_400_BAD_REQUEST)

        DocumentDownloadLog.objects.create(
            document=document,
            consultant=consultant,
            purpose=purpose,
        )

        notification_message = _build_download_alert_message(document, consultant, purpose)
        try:
            # Realtime push + DB persist for client-side toast/sound via notification websocket.
            from notifications.signals import create_and_push_notification
            create_and_push_notification(
                recipient=document.client,
                category='document',
                title='Document Download Alert',
                message=notification_message,
                link='/client/vault?tab=records',
            )
        except Exception:
            logger.exception("Failed to create/push in-app download notification for document %s", document.id)
            # Fallback: keep DB notification even if websocket push fails.
            try:
                Notification.objects.create(
                    recipient=document.client,
                    category='document',
                    title='Document Download Alert',
                    message=notification_message,
                    link='/client/vault?tab=records',
                )
            except Exception:
                logger.exception("Fallback Notification row creation failed for document %s", document.id)

        try:
            from notifications.tasks import send_whatsapp_text_task

            phone = _resolve_notification_phone(document.client)
            if phone:
                send_whatsapp_text_task.delay(
                    phone_number=phone,
                    text=notification_message,
                )
        except Exception:
            logger.exception("Failed to queue WhatsApp download alert for document %s", document.id)

        filename = os.path.basename(document.file.name or f"document_{document.id}")
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        force_stream = str(request.query_params.get('stream', '')).strip().lower() in {'1', 'true', 'yes', 'y'}

        if not force_stream:
            try:
                download_url = document.file.storage.url(document.file.name)
                if isinstance(download_url, str) and download_url.startswith(('http://', 'https://')):
                    return Response({
                        'download_url': download_url,
                        'filename': filename,
                        'content_type': content_type,
                    })
            except Exception:
                logger.warning("Failed to build storage URL for document %s; using stream fallback.", document.id, exc_info=True)

        try:
            file_handle = document.file.storage.open(document.file.name, 'rb')
            return FileResponse(
                file_handle,
                as_attachment=True,
                filename=filename,
                content_type=content_type,
            )
        except Exception:
            logger.exception("Failed to stream document %s download", document.id)
            return Response({'error': 'Failed to prepare download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @decorators.action(detail=True, methods=['get'], url_path='preview', permission_classes=[IsConsultantUser])
    def preview_document(self, request, pk=None):
        """
        Returns a dynamically watermarked preview stream for consultant viewing.
        """
        document = self.get_object()
        consultant = get_active_profile(request)

        if consultant.role != 'CONSULTANT':
            return Response({'error': 'Only consultants can view secure previews through this endpoint.'}, status=status.HTTP_403_FORBIDDEN)

        if not document.file:
            return Response({'error': 'No file available for preview.'}, status=status.HTTP_400_BAD_REQUEST)

        if not _consultant_has_document_access(document, consultant):
            return Response({'error': 'Access denied. Client has not granted access to this document.'}, status=status.HTTP_403_FORBIDDEN)

        filename = os.path.basename(document.file.name or f"document_{document.id}")
        ext = os.path.splitext(filename)[1].lower()
        watermark_text = _build_preview_watermark_text(consultant)

        try:
            source_bytes = _read_document_bytes(document)
        except Exception:
            logger.exception("Failed to read document bytes for preview: %s", document.id)
            return Response({'error': 'Failed to load document for preview.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            if ext == '.pdf':
                output_bytes, content_type = _generate_pdf_preview_bytes(source_bytes, watermark_text)
            elif ext in {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tif', '.tiff'}:
                output_bytes, content_type = _generate_image_preview_bytes(source_bytes, watermark_text)
            else:
                output_bytes = source_bytes
                content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        except Exception:
            logger.exception("Failed to generate secure preview for document %s", document.id)
            return Response({'error': 'Failed to generate secure preview.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        preview_name = f"preview_{filename}"
        response = HttpResponse(output_bytes, content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{preview_name}"'
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        response['Pragma'] = 'no-cache'
        return response

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

