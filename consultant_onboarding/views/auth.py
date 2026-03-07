import uuid
import re
import json
import logging
from datetime import datetime
from difflib import SequenceMatcher
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests 

from ..models import ConsultantApplication, IdentityDocument, ConsultantDocument as RealConsultantDocument
from ..serializers import ApplicationSerializer, GoogleAuthSerializer, OnboardingSerializer, AuthConsultantDocumentSerializer
from ..authentication import generate_applicant_token, IsApplicant
from core_auth.services.whatsapp_otp import (
    generate_otp,
    store_otp,
    send_whatsapp_otp,
    verify_otp as verify_otp_service,
    can_resend_otp,
    RESEND_COOLDOWN_SECONDS,
    OTP_EXPIRY_SECONDS,
)

logger = logging.getLogger(__name__)

NAME_MATCH_THRESHOLD = 50


def _normalize_indian_mobile(raw_phone):
    digits_only = re.sub(r"\D", "", str(raw_phone or ""))

    # +91XXXXXXXXXX / 91XXXXXXXXXX / XXXXXXXXXX
    if len(digits_only) == 12 and digits_only.startswith("91"):
        digits_only = digits_only[2:]

    if len(digits_only) != 10:
        return None, None, None, None, "Please enter a valid 10-digit Indian mobile number."

    if not re.match(r"^[6-9]\d{9}$", digits_only):
        return None, None, None, None, "Please enter a valid Indian mobile number starting with 6-9."

    full_phone = f"+91{digits_only}"
    wa_phone = f"91{digits_only}"
    display = f"+91 {digits_only[:5]} {digits_only[5:]}"
    return digits_only, full_phone, wa_phone, display, None


def _normalize_name(value):
    text = str(value or '').strip().lower()
    text = re.sub(r'[^a-z\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _fuzzy_name_similarity_pct(left, right):
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 0
    return int(round(SequenceMatcher(None, left_norm, right_norm).ratio() * 100))


def _first_last_name(value):
    tokens = _normalize_name(value).split()
    if not tokens:
        return ''
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0]} {tokens[-1]}"


def _fuzzy_first_last_similarity_pct(left, right):
    left_norm = _first_last_name(left)
    right_norm = _first_last_name(right)
    if not left_norm or not right_norm:
        return 0
    return int(round(SequenceMatcher(None, left_norm, right_norm).ratio() * 100))


def _parse_date_text(value):
    raw = str(value or '').strip()
    if not raw:
        return None

    formats = (
        '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
        '%Y-%m-%d', '%Y/%m/%d',
        '%d/%m/%y', '%d-%m-%y',
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    dmy_match = re.search(r'(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})', raw)
    if dmy_match:
        day = int(dmy_match.group(1))
        month = int(dmy_match.group(2))
        year = int(dmy_match.group(3))
        if year < 100:
            year += 2000 if year < 50 else 1900
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None

    ymd_match = re.search(r'(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})', raw)
    if ymd_match:
        year = int(ymd_match.group(1))
        month = int(ymd_match.group(2))
        day = int(ymd_match.group(3))
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None

    return None

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

@csrf_exempt
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
        # Verify the Google token against the ONBOARDING portal's OAuth client.
        # The onboarding frontend sends a token issued for GOOGLE_ONBOARDING_CLIENT_ID
        # (App.jsx: VITE_GOOGLE_CLIENT_ID). Using the wrong client_id here causes an
        # "audience mismatch" ValueError from Google's library → 400 Bad Request.
        onboarding_client_id = getattr(settings, 'GOOGLE_ONBOARDING_CLIENT_ID', None) or settings.GOOGLE_CLIENT_ID
        idinfo = id_token.verify_oauth2_token(
            token, 
            google_requests.Request(), 
            onboarding_client_id,
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

        # OPTION A ENFORCEMENT: Clients cannot use their existing email to register as a consultant.
        # Check if this email is already a core_auth User with CLIENT role
        from core_auth.models import User
        if User.objects.filter(email=email, role=User.CLIENT).exists():
            return Response(
                {'error': 'This email is already registered as a Client. To become a Consultant, please use a different email address (e.g. yourname+consultant@gmail.com).'},
                status=status.HTTP_403_FORBIDDEN
            )

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
        # Also expose the token in the body so the frontend can store it in
        # localStorage and send it as 'Authorization: Bearer' on cross-origin
        # requests (the Vercel reverse proxy handles same-domain in production,
        # but the Bearer path is a reliable fallback for all environments).
        response_data['applicant_token'] = jwt_token
        
        response = Response(response_data, status=status.HTTP_200_OK)
        
        # Set JWT token in HttpOnly cookie
        # We use SameSite='None' and Secure=True in production to allow cross-subdomain
        # authentication (e.g., from apply.taxplanadvisor.co to main.taxplanadvisor.co)
        is_production = not settings.DEBUG
        
        # Determine cookie security based on whether the request is secure or in production
        is_secure = request.is_secure() or is_production
        
        response.set_cookie(
            key='applicant_token',
            value=jwt_token,
            max_age=3 * 60 * 60,  # 3 hours
            httponly=True,
            samesite='None' if is_secure else 'Lax',
            secure=is_secure,
            domain=None, # Defaults to current host; set to '.taxplanadvisor.co' if shared across ALL subdomains
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


@api_view(["POST"])
@permission_classes([IsApplicant])
def send_phone_otp(request):
    """
    Send OTP to a phone number via WhatsApp for onboarding applicants.

    In DEBUG (dev) mode, if WhatsApp credentials are not configured, the OTP is returned
    as `debug_otp` so you can test the flow locally without Meta setup.
    """
    application = request.application
    phone_number = request.data.get("phone_number", "").strip()

    digits_only, full_phone, wa_phone, display, error = _normalize_indian_mobile(phone_number)
    if error:
        return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

    existing_app = ConsultantApplication.objects.filter(
        phone_number=full_phone, is_phone_verified=True
    ).exclude(id=application.id).first()
    if existing_app:
        return Response(
            {"error": "This phone number is already registered with another account."},
            status=status.HTTP_409_CONFLICT,
        )

    can_send, reason, wait_seconds = can_resend_otp(full_phone)
    if not can_send:
        return Response(
            {"error": reason, "cooldown": wait_seconds},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    otp = generate_otp()
    store_otp(full_phone, otp)

    if (application.phone_number or "").strip() != full_phone or application.is_phone_verified:
        application.phone_number = full_phone
        application.is_phone_verified = False
        application.save(update_fields=["phone_number", "is_phone_verified"])

    meta_configured = bool(
        getattr(settings, "META_PHONE_NUMBER_ID", None)
        and getattr(settings, "META_ACCESS_TOKEN", None)
    )

    if not meta_configured:
        if settings.DEBUG:
            logger.info("DEV OTP for %s: %s", full_phone[-4:], otp)
            return Response(
                {
                    "success": True,
                    "message": "DEV mode: OTP generated (WhatsApp not configured).",
                    "cooldown": RESEND_COOLDOWN_SECONDS,
                    "expiry": OTP_EXPIRY_SECONDS,
                    "phone_display": display,
                    "debug_otp": otp,
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": "WhatsApp service not configured. Please contact support."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    success, message = send_whatsapp_otp(wa_phone, otp)
    if not success:
        return Response({"error": message}, status=status.HTTP_502_BAD_GATEWAY)

    logger.info("OTP sent to %s for application %s", full_phone[-4:], application.id)
    return Response(
        {
            "success": True,
            "message": "OTP sent to your WhatsApp number.",
            "cooldown": RESEND_COOLDOWN_SECONDS,
            "expiry": OTP_EXPIRY_SECONDS,
            "phone_display": display,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsApplicant])
def verify_phone_otp(request):
    """Verify OTP and mark applicant phone number as verified."""
    application = request.application
    phone_number = request.data.get("phone_number", "").strip()
    otp = request.data.get("otp", "").strip()

    if not phone_number or not otp:
        return Response(
            {"error": "Phone number and OTP are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(otp) != 6 or not otp.isdigit():
        return Response(
            {"error": "OTP must be a 6-digit number."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    _, full_phone, _, _, error = _normalize_indian_mobile(phone_number)
    if error:
        return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

    success, message, remaining_attempts = verify_otp_service(full_phone, otp)
    if not success:
        return Response(
            {
                "success": False,
                "verified": False,
                "message": message,
                "remaining_attempts": remaining_attempts,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    application.phone_number = full_phone
    application.is_phone_verified = True
    application.save(update_fields=["phone_number", "is_phone_verified"])

    return Response(
        {
            **get_profile_response_data(application),
            "success": True,
            "verified": True,
            "message": message,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
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
def get_identity_upload_url(request):
    """Get presigned URL to upload identity document directly to S3"""
    file_ext = request.data.get('file_ext', 'pdf').strip('.')
    content_type = request.data.get('content_type', 'application/pdf')
    
    import uuid
    file_path = f"identity_documents/{request.application.id}/identity_{uuid.uuid4()}.{file_ext}"
    
    try:
        from consultant_onboarding.utils.s3_utils import generate_presigned_upload_url
        url_data = generate_presigned_upload_url(file_path, content_type=content_type)
    except Exception as e:
        # Log the error or handle it appropriately
        print(f"Error generating presigned URL: {e}")
        url_data = None
    
    if not url_data:
        return Response({'error': 'Failed to generate upload URL'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    return Response(url_data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsApplicant])
def upload_identity_document(request):
    """
    Upload identity document directly (multipart) and verify with Gemini.
    Checks name and DOB extracted from the ID against the application profile.
    Returns PERSONAL_DETAILS_MISMATCH if they don't match.
    """
    application = request.application
    uploaded_file = request.FILES.get('identity_document')

    if not uploaded_file:
        return Response({"error": "No document uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        file_ext = uploaded_file.name.split('.')[-1]
        file_path = f"identity_documents/{application.id}/identity_{uuid.uuid4()}.{file_ext}"

        # Save to S3 via default storage
        from django.core.files.storage import default_storage
        saved_path = default_storage.save(file_path, uploaded_file)

        # Create database record
        identity_doc = IdentityDocument.objects.create(
            application=application,
            file_path=saved_path
        )

        # Verify with Gemini
        from consultant_onboarding.services import IdentityDocumentVerifier
        verifier = IdentityDocumentVerifier()
        result = verifier.verify_document(identity_doc)

        # Save Gemini results
        identity_doc.document_type = result.get('document_type')
        identity_doc.verification_status = result.get('verification_status')
        identity_doc.gemini_raw_response = result.get('raw_response')
        identity_doc.save()

        # ---- Name & DOB matching ----
        verification_status = str(identity_doc.verification_status or '').strip().lower()
        extracted_name = str(result.get('extracted_name') or '').strip()
        extracted_dob = str(result.get('extracted_dob') or '').strip()
        profile_name = f"{application.first_name or ''} {application.last_name or ''}".strip()
        profile_dob = getattr(application, 'dob', None)
        name_similarity_pct = _fuzzy_name_similarity_pct(profile_name, extracted_name) if extracted_name else 0
        name_match = (name_similarity_pct >= NAME_MATCH_THRESHOLD) if extracted_name else None
        parsed_extracted_dob = _parse_date_text(extracted_dob)
        # Only compare DOB if BOTH the profile and the ID have a date; skip otherwise
        if profile_dob and parsed_extracted_dob:
            dob_match = (profile_dob == parsed_extracted_dob)
        else:
            dob_match = None  # not enough data to compare — skip

        # DEBUG: Log matching results
        print(f"--- IDENTITY VERIFICATION DEBUG ---")
        print(f"  Gemini verification_status: {verification_status}")
        print(f"  Extracted name: '{extracted_name}' | Profile name: '{profile_name}'")
        print(f"  Name similarity: {name_similarity_pct}% (threshold: {NAME_MATCH_THRESHOLD}%) → match: {name_match}")
        print(f"  Extracted DOB: '{extracted_dob}' | Profile DOB: {profile_dob} | Parsed: {parsed_extracted_dob}")
        print(f"  DOB match: {dob_match}")
        print(f"-----------------------------------")

        # If we can detect personal-details mismatch, delete the doc and reject.
        has_detectable_mismatch = (name_match is False) or (dob_match is False)
        if has_detectable_mismatch:
            try:
                default_storage.delete(saved_path)
            except Exception:
                pass
            identity_doc.delete()
            return Response({
                "error": "Personal details do not match the uploaded Government ID.",
                "code": "PERSONAL_DETAILS_MISMATCH",
                "verification": {
                    "document_type": result.get('document_type'),
                    "status": result.get('verification_status'),
                    "name_similarity_pct": name_similarity_pct,
                    "name_threshold_pct": NAME_MATCH_THRESHOLD,
                    "name_match": name_match,
                    "dob_match": dob_match,
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        if verification_status != 'verified':
            # If verification couldn't be performed (quota/outage), don't tell the user their ID is "invalid".
            error_code = result.get('error_code')
            if verification_status in ('error', 'failed') or error_code in ('GEMINI_QUOTA_EXCEEDED', 'IDENTITY_VERIFICATION_UNAVAILABLE'):
                try:
                    default_storage.delete(saved_path)
                except Exception:
                    pass
                identity_doc.delete()
                retry_in_s = result.get('retry_in_s')
                return Response({
                    "error": "Identity verification is temporarily unavailable. Please try again shortly.",
                    "code": error_code or "IDENTITY_VERIFICATION_UNAVAILABLE",
                    "verification": {
                        "document_type": result.get('document_type'),
                        "status": result.get('verification_status'),
                        "privacy_notes": result.get('privacy_notes', ''),
                        "retry_in_s": retry_in_s,
                    }
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

            try:
                default_storage.delete(saved_path)
            except Exception:
                pass
            identity_doc.delete()
            return Response({
                "error": "Document verification failed. Please upload a valid government ID.",
                "code": "IDENTITY_INVALID",
                "verification": {
                    "document_type": result.get('document_type'),
                    "status": result.get('verification_status'),
                    "privacy_notes": result.get('privacy_notes', ''),
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": "Identity document uploaded and verified successfully",
            "path": saved_path,
            "verification": {
                "document_type": identity_doc.document_type,
                "status": identity_doc.verification_status,
                "name_similarity_pct": name_similarity_pct,
                "name_threshold_pct": NAME_MATCH_THRESHOLD,
                "name_match": name_match,
                "dob_match": dob_match,
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
