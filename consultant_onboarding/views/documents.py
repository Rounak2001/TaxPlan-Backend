import time
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from ..models import ConsultantDocument
from ..serializers import ConsultantDocumentSerializer
from ..authentication import IsApplicant


class GetDocumentUploadUrlView(APIView):
    permission_classes = [IsApplicant]

    def post(self, request):
        application = request.application
        filename = request.data.get('filename', 'document')
        file_ext = request.data.get('file_ext', 'pdf').strip('.')
        content_type = request.data.get('content_type', 'application/pdf')
        
        timestamp = int(time.time())
        safe_filename = "".join(x for x in filename if x.isalnum() or x in "._- ")
        if not safe_filename:
            safe_filename = "doc"
            
        file_path = f"consultant_documents/{application.id}/{timestamp}_{safe_filename}.{file_ext}"
        
        from consultant_onboarding.utils.s3_utils import generate_presigned_upload_url
        url_data = generate_presigned_upload_url(file_path, content_type=content_type)
        
        if not url_data:
            return Response({'error': 'Failed to generate upload URL'}, status=500)
            
        return Response(url_data, status=200)


class UploadDocumentView(APIView):
    permission_classes = [IsApplicant]

    def post(self, request):
        application = request.application
        qualification_type = request.data.get('qualification_type')
        document_type = request.data.get('document_type')
        s3_path = request.data.get('s3_path')

        if not all([qualification_type, document_type, s3_path]):
            return Response({'error': 'Missing required fields: qualification_type, document_type, s3_path'}, status=400)

        try:
            document = ConsultantDocument.objects.create(
                application=application,
                qualification_type=qualification_type,
                document_type=document_type,
                file_path=s3_path
            )

            # Verify with Gemini asynchronously
            try:
                from ..services import QualificationDocumentVerifier
                verifier = QualificationDocumentVerifier()
                result = verifier.verify_document(document)
                document.verification_status = result.get('verification_status')
                document.gemini_raw_response = result.get('raw_response')
                document.save()
            except Exception as e:
                print(f"Gemini verification failed (non-critical): {e}")

            serializer = ConsultantDocumentSerializer(document)
            response_data = serializer.data
            response_data['verification_status'] = document.verification_status
            
            return Response(response_data, status=201)

        except Exception as e:
            return Response({'error': str(e)}, status=500)


class DocumentListView(APIView):
    permission_classes = [IsApplicant]

    def get(self, request):
        application = request.application
        documents = ConsultantDocument.objects.filter(application=application).order_by('-uploaded_at')
        serializer = ConsultantDocumentSerializer(documents, many=True)
        return Response(serializer.data)
