import uuid
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests 

from ..models import ConsultantApplication, IdentityDocument, ConsultantDocument as RealConsultantDocument
from ..serializers import ApplicationSerializer, GoogleAuthSerializer, OnboardingSerializer, AuthConsultantDocumentSerializer
from ..authentication import generate_applicant_token, IsApplicant

def get_profile_response_data(application):
    """Utility to return a consistent profile status object"""
    has_identity_doc = IdentityDocument.objects.filter(application=application).exists()
    
    has_passed_assessment = False
    try:
        from ..models import UserSession
        latest_session = UserSession.objects.filter(application=application, status='completed').order_by('-end_time').first()
        if latest_session and latest_session.score >= 30:
            has_passed_assessment = True
    except Exception:
        pass

    has_documents = RealConsultantDocument.objects.filter(application=application).exists()

    data = {
        'user': ApplicationSerializer(application).data,
        'has_identity_doc': has_identity_doc,
        'has_passed_assessment': has_passed_assessment,
        'has_accepted_declaration': application.has_accepted_declaration,
        'has_documents': has_documents,
    }
    
    # DEBUG LOGGING
    print(f"--- DEBUG: Profile Response for {application.email} ---")
    print(f"    has_accepted_declaration: {data['has_accepted_declaration']}")
    print(f"    is_onboarded: {data['user'].get('is_onboarded')}")
    print(f"    status: {data['user'].get('status')}")
    print("-------------------------------------------------")
    
    return data

@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def google_auth(request):
    """
    Authenticate applicant via Google OAuth token.
    Creates new ConsultantApplication if not exists, returns Applicant JWT token in cookie.
    """
    serializer = GoogleAuthSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    token = serializer.validated_data['token']
    
    try:
        # Verify the Google token
        idinfo = id_token.verify_oauth2_token(
            token, 
            google_requests.Request(), 
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10
        )
        
        email = idinfo.get('email')
        google_id = idinfo.get('sub')
        name = idinfo.get('name', '')
        
        if not email:
            return Response(
                {'error': 'Email not provided by Google'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        name_parts = name.split()
        first_name = name_parts[0] if name_parts else ''
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ''

        application, created = ConsultantApplication.objects.get_or_create(
            email=email,
            defaults={
                'google_id': google_id,
                'first_name': first_name,
                'last_name': last_name,
                'status': 'PENDING'
            }
        )
        
        # Update google_id if application exists but doesn't have one
        if not created and not application.google_id:
            application.google_id = google_id
            application.save()
            
        # If the application is already APPROVED, they shouldn't be here, but we let them pass 
        # to the frontend to redirect them to the main app
        
        # Generate custom applicant JWT token
        jwt_token = generate_applicant_token(application)
        
        # Create response with application data
        response_data = get_profile_response_data(application)
        response_data['is_new_user'] = created
        response_data['needs_onboarding'] = application.status == 'PENDING' and not application.first_name
        
        response = Response(response_data, status=status.HTTP_200_OK)
        
        # Set JWT token in HttpOnly cookie
        # In dev (DEBUG=True), use secure=False and samesite='Lax' so cookie works over plain HTTP.
        # In prod, use secure=True and samesite='None' for cross-domain (apply.yourdomain.com -> api.yourdomain.com).
        is_production = not settings.DEBUG
        response.set_cookie(
            key='applicant_token',
            value=jwt_token,
            max_age=3 * 60 * 60,  
            httponly=True,
            samesite='None' if is_production else 'Lax',
            secure=is_production,
        )
        
        return response
        
    except ValueError as e:
        return Response(
            {'error': f'Invalid Google token: {str(e)}'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return Response(
            {'error': f'Authentication failed: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsApplicant])
def complete_onboarding(request):
    """
    Complete application onboarding with profile details.
    """
    serializer = OnboardingSerializer(data=request.data, instance=request.application)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    application = serializer.save()
    
    return Response(get_profile_response_data(application), status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsApplicant])
def get_user_profile(request):
    """Get current application's profile with step completion flags"""
    application = request.application
    has_identity_doc = IdentityDocument.objects.filter(application=application).exists()
    
    has_passed_assessment = False
    try:
        from ..models import UserSession
        latest_session = UserSession.objects.filter(application=application, status='completed').order_by('-end_time').first()
        if latest_session and latest_session.score >= 30:
            has_passed_assessment = True
    except Exception:
        pass

    has_documents = RealConsultantDocument.objects.filter(application=application).exists()

    return Response(get_profile_response_data(application), status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsApplicant])
def accept_declaration(request):
    """Mark the application as having accepted the onboarding declaration"""
    application = request.application
    application.has_accepted_declaration = True
    application.save(update_fields=['has_accepted_declaration'])
    return Response(get_profile_response_data(application), status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def logout(request):
    """Logout applicant by clearing the JWT cookie"""
    response = Response({'message': 'Logged out successfully'}, status=status.HTTP_200_OK)
    response.delete_cookie('applicant_token', samesite='None')
    return response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """Health check endpoint"""
    return Response({'status': 'ok'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsApplicant])
def upload_document(request):
    """
    Upload consultant documents (Qualification or Certificate).
    Enforces limit of 5 certificates.
    """
    application = request.application
    document_type = request.data.get('document_type')

    if document_type in ('Certificate', 'certificate'):
        cert_count = application.documents.filter(document_type__in=['Certificate', 'certificate']).count()
        if cert_count >= 5:
            return Response(
                {'error': 'You can upload a maximum of 5 certificates.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    serializer = AuthConsultantDocumentSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(application=application)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsApplicant])
def get_user_documents(request):
    """Get all documents uploaded by the applicant"""
    documents = request.application.documents.all()
    serializer = AuthConsultantDocumentSerializer(documents, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsApplicant])
def upload_identity_document(request):
    """
    Upload identity document to S3 and verify with Gemini.
    """
    application = request.application
    uploaded_file = request.FILES.get('identity_document')
    
    if not uploaded_file:
        return Response({"error": "No document uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        file_ext = uploaded_file.name.split('.')[-1]
        file_path = f"identity_documents/{application.id}/identity_{uuid.uuid4()}.{file_ext}"
        
        from django.core.files.storage import default_storage
        saved_path = default_storage.save(file_path, uploaded_file)

        identity_doc = IdentityDocument.objects.create(
            application=application,
            file_path=saved_path
        )
        
        # TODO: Move the Gemini verification into consultant_onboarding/services.py 
        # (This will be done in the next step when we copy the services)
        try:
            from ..services import IdentityDocumentVerifier
            verifier = IdentityDocumentVerifier()
            result = verifier.verify_document(identity_doc)
            
            identity_doc.document_type = result.get('document_type')
            identity_doc.verification_status = result.get('verification_status')
            identity_doc.gemini_raw_response = result.get('raw_response')
            identity_doc.save()
        except Exception as e:
            print(f"Failed to verify via Gemini immediately: {e}")

        return Response({
            "message": "Identity document uploaded successfully", 
            "path": saved_path,
            "verification": {
                "document_type": identity_doc.document_type,
                "status": identity_doc.verification_status
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
