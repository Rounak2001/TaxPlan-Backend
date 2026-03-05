import uuid
import base64
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..models import FaceVerification
from ..authentication import IsApplicant
from ..utils.rekognition_client import get_rekognition_client

# Initialize Rekognition client
rekognition = get_rekognition_client()


@api_view(['POST'])
@permission_classes([IsApplicant])
def upload_photo(request, user_id=None):
    """
    Upload ID photo for face verification – for the current applicant.
    """
    application = request.application
    uploaded_photo = request.FILES.get('uploaded_photo')
    if not uploaded_photo:
        return Response({"error": "No photo uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from django.core.files.storage import default_storage
        file_path = f"face_verification/{application.id}/id_photo_{uuid.uuid4()}.jpg"
        
        saved_path = default_storage.save(file_path, uploaded_photo)

        verification, created = FaceVerification.objects.get_or_create(application=application)
        verification.id_image_path = file_path
        verification.save()

        return Response({"message": "ID photo uploaded successfully", "path": file_path})

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsApplicant])
def verify_face(request, user_id=None):
    """
    Verify face by comparing uploaded ID photo with live camera capture.
    """
    application = request.application

    live_photo_base64 = request.data.get('live_photo_base64')
    if not live_photo_base64:
        return Response({"error": "Live photo required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Get stored ID photo path
        try:
            verification = FaceVerification.objects.get(application=application)
            id_photo_path = verification.id_image_path
            if not id_photo_path:
                return Response({"error": "ID photo not found. Please upload ID photo first."}, status=status.HTTP_404_NOT_FOUND)
        except FaceVerification.DoesNotExist:
            return Response({"error": "ID photo not found. Please upload ID photo first."}, status=status.HTTP_404_NOT_FOUND)

        # 1. Download ID photo from S3
        from django.core.files.storage import default_storage
        try:
            with default_storage.open(id_photo_path, 'rb') as f:
                id_photo_data = f.read()
        except Exception:
            return Response({"error": "Failed to retrieve ID photo"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # 2. Process Live Photo (Base64 -> Bytes)
        if "base64," in live_photo_base64:
            live_photo_base64 = live_photo_base64.split("base64,")[1]
        live_photo_bytes = base64.b64decode(live_photo_base64)

        # 3. Upload Live Photo to S3
        from django.core.files.base import ContentFile
        live_photo_path = f"face_verification/{application.id}/live_photo_{uuid.uuid4()}.jpg"
        default_storage.save(live_photo_path, ContentFile(live_photo_bytes))
        
        verification.live_image_path = live_photo_path
        verification.save()

        # Check for faces in ID Photo
        det_id = rekognition.detect_faces(Image={"Bytes": id_photo_data})
        face_count = len(det_id.get('FaceDetails', []))
        if face_count == 0:
            return Response({"error": "No face detected in the uploaded ID photo. Please upload a clearer photo."}, status=status.HTTP_400_BAD_REQUEST)

        # Check for faces in Live Photo
        det_live = rekognition.detect_faces(Image={"Bytes": live_photo_bytes})
        face_count_live = len(det_live.get('FaceDetails', []))
        if face_count_live == 0:
            return Response({"error": "No face detected in the live capture. Please try again."}, status=status.HTTP_400_BAD_REQUEST)

        # 4. Compare with Amazon Rekognition
        response = rekognition.compare_faces(
            SourceImage={"Bytes": id_photo_data},
            TargetImage={"Bytes": live_photo_bytes},
            SimilarityThreshold=85
        )

        matches = response.get("FaceMatches", [])
        is_match = False
        confidence = 0.0

        if matches:
            similarity = matches[0]["Similarity"]
            is_match = True
            confidence = similarity
        
        verification.is_match = is_match
        verification.confidence = confidence
        verification.save()
        
        if is_match:
            application.is_verified = True
            application.save(update_fields=['is_verified'])

        return Response({
            "match": is_match,
            "similarity": confidence
        })

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
