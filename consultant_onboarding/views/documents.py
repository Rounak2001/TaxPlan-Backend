import time
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.core.files.storage import default_storage

from ..models import ConsultantDocument
from ..serializers import ConsultantDocumentSerializer
from ..authentication import IsApplicant
from ..credential_service import trigger_auto_credential_check
from ..utils.name_matching import first_last_name, first_last_names_match, get_latest_verified_identity_name


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
            claimed_doc_type = str(document_type or "").strip().lower()

            # If the user re-uploads a bachelor/master degree, replace the previous one to avoid
            # leaving stale/unverified docs that would block credential generation.
            if claimed_doc_type in {"bachelors_degree", "masters_degree"}:
                previous = ConsultantDocument.objects.filter(application=application, document_type=document_type)
                for old_doc in previous:
                    try:
                        if old_doc.file_path:
                            default_storage.delete(str(old_doc.file_path).lstrip('/'))
                    except Exception:
                        pass
                previous.delete()

            document = ConsultantDocument.objects.create(
                application=application,
                qualification_type=qualification_type,
                document_type=document_type,
                file_path=s3_path
            )

            # Verify with Gemini (best-effort, but enforce validity for bachelor's submissions)
            try:
                from ..services import QualificationDocumentVerifier
                verifier = QualificationDocumentVerifier()
                result = verifier.verify_document(document)
                document.verification_status = result.get('verification_status')
                document.gemini_raw_response = result.get('raw_response')
                document.save()
            except Exception as e:
                result = {"verification_status": "Error", "rejection_reason": "Verification service error", "raw_response": str(e)}
                document.verification_status = "Error"
                document.gemini_raw_response = str(e)
                document.save()

            verification_status = str(document.verification_status or "").strip().lower()
            extracted_name = str((result or {}).get("extracted_name") or "").strip()
            if claimed_doc_type == "bachelors_degree" and verification_status != "verified":
                rejection_reason = (
                    (result or {}).get("rejection_reason")
                    or (result or {}).get("notes")
                    or "Bachelor's degree verification failed. Please upload the correct Bachelor's degree certificate."
                )
                try:
                    if document.file_path:
                        default_storage.delete(str(document.file_path).lstrip('/'))
                except Exception:
                    pass
                document.delete()
                return Response(
                    {
                        "error": rejection_reason,
                        "document_type": document_type,
                        "verification_status": document.verification_status,
                        "details": result,
                    },
                    status=400,
                )

            if claimed_doc_type == "bachelors_degree":
                gov_id_name = get_latest_verified_identity_name(application)
                if not gov_id_name:
                    gov_id_name = f"{application.first_name or ''} {application.last_name or ''}".strip()

                if not extracted_name:
                    try:
                        if document.file_path:
                            default_storage.delete(str(document.file_path).lstrip('/'))
                    except Exception:
                        pass
                    document.delete()
                    return Response(
                        {
                            "error": "Could not read the name on the Bachelor's degree. Please upload a clearer certificate.",
                            "document_type": document_type,
                            "verification_status": document.verification_status,
                            "details": {
                                **(result or {}),
                                "name_match_rule": "first_and_last_name_only",
                            },
                        },
                        status=400,
                    )

                if gov_id_name and not first_last_names_match(gov_id_name, extracted_name):
                    try:
                        if document.file_path:
                            default_storage.delete(str(document.file_path).lstrip('/'))
                    except Exception:
                        pass
                    document.delete()
                    return Response(
                        {
                            "error": "First and last name on the Bachelor's degree must match the verified Government ID.",
                            "document_type": document_type,
                            "verification_status": document.verification_status,
                            "details": {
                                **(result or {}),
                                "name_match_rule": "first_and_last_name_only",
                                "government_id_first_last_name": first_last_name(gov_id_name),
                                "document_first_last_name": first_last_name(extracted_name),
                            },
                        },
                        status=400,
                    )

            serializer = ConsultantDocumentSerializer(document)
            response_data = serializer.data
            response_data['verification_status'] = document.verification_status
            response_data['name_match_rule'] = 'first_and_last_name_only'
            if claimed_doc_type == "bachelors_degree":
                gov_id_name = get_latest_verified_identity_name(application) or f"{application.first_name or ''} {application.last_name or ''}".strip()
                response_data['government_id_first_last_name'] = first_last_name(gov_id_name)
                response_data['document_first_last_name'] = first_last_name(extracted_name)

            trigger_auto_credential_check(application, f"qualification_upload:{document_type}")
            
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
