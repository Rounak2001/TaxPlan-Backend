import time
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from ..models import ConsultantDocument
from ..serializers import ConsultantDocumentSerializer
from ..authentication import IsApplicant


class UploadDocumentView(APIView):
    permission_classes = [IsApplicant]

    def post(self, request):
        application = request.application
        qualification_type = request.data.get('qualification_type')
        document_type = request.data.get('document_type')
        file_obj = request.FILES.get('file')

        if not all([qualification_type, document_type, file_obj]):
            return Response({'error': 'Missing required fields: qualification_type, document_type, file'}, status=400)

        timestamp = int(time.time())
        filename = "".join(x for x in file_obj.name if x.isalnum() or x in "._- ")
        file_path = f"consultant_documents/{application.id}/{timestamp}_{filename}"

        try:
            from django.core.files.storage import default_storage
            saved_path = default_storage.save(file_path, file_obj)
            
            document = ConsultantDocument.objects.create(
                application=application,
                qualification_type=qualification_type,
                document_type=document_type,
                file_path=saved_path
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
