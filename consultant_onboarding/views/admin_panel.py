"""
Admin Panel API views for the consultant onboarding system.
Ported from backend1/consultant_core/views/admin.py and adapted
to use ConsultantApplication instead of a custom User model.
"""
import jwt
import random
import string
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.mail import send_mail
from django.core.files.storage import default_storage

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from consultant_onboarding.models import (
    ConsultantApplication,
    AuthConsultantDocument,
    IdentityDocument,
    ConsultantDocument,
    ConsultantCredential,
    FaceVerification,
    UserSession,
    VideoResponse,
    Violation,
    ProctoringSnapshot,
)

# ------------------------------------------------------------------
# Hardcoded admin credentials (matches backend1)
# ------------------------------------------------------------------
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin'


# ------------------------------------------------------------------
# Custom JWT authentication for admin endpoints
# ------------------------------------------------------------------
class AdminJWTAuthentication(BaseAuthentication):
    """JWT authentication that checks for is_admin claim."""

    def authenticate(self, request):
        token = request.headers.get('Authorization', '')
        if not token.startswith('Bearer '):
            return None
        token = token.split(' ')[1]
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=['HS256'])
            if not payload.get('is_admin'):
                raise AuthenticationFailed('Not an admin token')
            # Return a lightweight admin "user" object
            admin_user = type('AdminUser', (), {'is_authenticated': True, 'is_admin': True})()
            return (admin_user, token)
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token expired')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid token')


# ------------------------------------------------------------------
# Helper: get a signed URL from S3 storage
# ------------------------------------------------------------------
def get_storage_url(path):
    """Generate a URL for a file stored in default storage (S3)."""
    if not path:
        return None
    try:
        return default_storage.url(path)
    except Exception as e:
        print(f"Error generating URL for {path}: {e}")
        return None


# ------------------------------------------------------------------
# Views
# ------------------------------------------------------------------

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def admin_login(request):
    """Admin login with hardcoded credentials."""
    username = request.data.get('username', '')
    password = request.data.get('password', '')

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        payload = {
            'is_admin': True,
            'username': username,
            'exp': datetime.now(timezone.utc) + timedelta(hours=12),
            'iat': datetime.now(timezone.utc),
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm='HS256')
        return Response({'token': token, 'message': 'Login successful'})

    return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(['GET'])
@authentication_classes([AdminJWTAuthentication])
@permission_classes([AllowAny])
def consultant_list(request):
    """List all consultant applications with summary info."""
    apps = ConsultantApplication.objects.all().order_by('-created_at')
    data = []

    for app in apps:
        # Assessment status from latest session
        latest_session = UserSession.objects.filter(application=app).order_by('-start_time').first()
        assessment_status = 'Not Attempted'
        assessment_score = None
        violation_count = 0

        if latest_session:
            if latest_session.violation_count > 0:
                assessment_status = 'Violated'
            else:
                assessment_status = latest_session.status.capitalize()
            assessment_score = latest_session.score
            violation_count = latest_session.violation_count

        # Document count
        doc_count = (
            AuthConsultantDocument.objects.filter(application=app).count() +
            ConsultantDocument.objects.filter(application=app).count()
        )

        # Face verification status
        latest_face = FaceVerification.objects.filter(application=app).order_by('-verified_at').first()
        if latest_face:
            face_status = 'Matched' if latest_face.is_match else 'No Match'
        else:
            face_status = 'Not Done'

        # Document verification status
        identity_docs = IdentityDocument.objects.filter(application=app)
        consultant_docs_qs = ConsultantDocument.objects.filter(application=app)
        total_verifiable = identity_docs.count() + consultant_docs_qs.count()
        verified_count = (
            identity_docs.filter(verification_status='Verified').count() +
            consultant_docs_qs.filter(verification_status='Verified').count()
        )
        if total_verifiable == 0:
            doc_verification_status = 'No Docs'
        elif verified_count == total_verifiable:
            doc_verification_status = 'All Verified'
        elif verified_count > 0:
            doc_verification_status = f'{verified_count}/{total_verifiable} Verified'
        else:
            doc_verification_status = 'Pending'

        # Video score from latest session
        video_score = None
        video_total = None
        if latest_session:
            video_responses = VideoResponse.objects.filter(session=latest_session)
            scores = [vr.ai_score for vr in video_responses if vr.ai_score is not None]
            if scores:
                video_score = sum(scores)
                video_total = len(latest_session.video_question_set or []) * 5

        data.append({
            'id': app.id,
            'email': app.email,
            'full_name': app.get_full_name(),
            'phone_number': app.phone_number,
            'is_onboarded': app.is_onboarded,
            'is_verified': app.is_verified,
            'has_accepted_declaration': app.has_accepted_declaration,
            'assessment_status': assessment_status,
            'assessment_score': assessment_score,
            'video_score': video_score,
            'video_total': video_total,
            'document_count': doc_count,
            'face_verification_status': face_status,
            'doc_verification_status': doc_verification_status,
            'has_credentials': hasattr(app, 'credentials') and ConsultantCredential.objects.filter(application=app).exists(),
            'created_at': app.created_at.isoformat() if app.created_at else None,
        })

    return Response({'consultants': data, 'total': len(data)})


@api_view(['GET'])
@authentication_classes([AdminJWTAuthentication])
@permission_classes([AllowAny])
def consultant_detail(request, app_id):
    """Get full detail for a single consultant application."""
    try:
        app = ConsultantApplication.objects.get(id=app_id)
    except ConsultantApplication.DoesNotExist:
        return Response({'error': 'Consultant not found'}, status=status.HTTP_404_NOT_FOUND)

    # Profile
    profile = {
        'id': app.id,
        'email': app.email,
        'first_name': app.first_name,
        'middle_name': app.middle_name,
        'last_name': app.last_name,
        'full_name': app.get_full_name(),
        'age': app.age,
        'dob': str(app.dob) if app.dob else None,
        'phone_number': app.phone_number,
        'address_line1': app.address_line1,
        'address_line2': app.address_line2,
        'city': app.city,
        'state': app.state,
        'pincode': app.pincode,
        'practice_type': app.practice_type,
        'years_of_experience': app.experience_years,
        'is_onboarded': app.is_onboarded,
        'is_verified': app.is_verified,
        'is_active': True,
        'has_accepted_declaration': app.has_accepted_declaration,
        'created_at': app.created_at.isoformat() if app.created_at else None,
        'updated_at': app.updated_at.isoformat() if app.updated_at else None,
        'has_credentials': ConsultantCredential.objects.filter(application=app).exists(),
    }

    # Identity Documents
    identity_docs = []
    for doc in IdentityDocument.objects.filter(application=app):
        identity_docs.append({
            'id': doc.id,
            'file_path': doc.file_path,
            'file_url': get_storage_url(doc.file_path),
            'uploaded_at': doc.uploaded_at,
            'document_type': doc.document_type,
            'verification_status': doc.verification_status,
            'gemini_raw_response': doc.gemini_raw_response,
        })

    # Face Verification
    face_records = []
    for f in FaceVerification.objects.filter(application=app):
        face_records.append({
            'id': f.id,
            'id_image_path': f.id_image_path,
            'id_image_url': get_storage_url(f.id_image_path),
            'live_image_path': f.live_image_path,
            'live_image_url': get_storage_url(f.live_image_path),
            'confidence': f.confidence,
            'is_match': f.is_match,
            'verified_at': f.verified_at,
        })

    # Assessment Sessions
    assessment_data = []
    for s in UserSession.objects.filter(application=app).order_by('-start_time'):
        violations = list(
            Violation.objects.filter(session=s).values('id', 'violation_type', 'timestamp')
        )

        snapshots = []
        for snap in ProctoringSnapshot.objects.filter(session=s).order_by('timestamp'):
            snapshots.append({
                'id': snap.id,
                'image_url': get_storage_url(snap.image_url),
                'timestamp': snap.timestamp,
                'is_violation': snap.is_violation,
                'violation_reason': snap.violation_reason,
                'face_count': snap.face_count,
                'match_score': snap.match_score,
            })

        videos = []
        for v in VideoResponse.objects.filter(session=s):
            videos.append({
                'id': v.id,
                'question_identifier': v.question_identifier,
                'video_file': v.video_file,
                'video_url': get_storage_url(v.video_file),
                'uploaded_at': v.uploaded_at,
                'ai_transcript': v.ai_transcript,
                'ai_score': v.ai_score,
                'ai_feedback': v.ai_feedback,
                'ai_status': v.ai_status,
            })

        assessment_data.append({
            'id': s.id,
            'test_type': s.test_type.name if s.test_type else None,
            'selected_domains': s.selected_domains,
            'score': s.score,
            'status': s.status,
            'violation_count': s.violation_count,
            'start_time': s.start_time.isoformat() if s.start_time else None,
            'end_time': s.end_time.isoformat() if s.end_time else None,
            'question_set': s.question_set,
            'video_question_set': s.video_question_set,
            'violations': violations,
            'proctoring_snapshots': snapshots,
            'video_responses': videos,
        })

    # Auth Documents (qualification uploads)
    auth_docs = []
    for d in AuthConsultantDocument.objects.filter(application=app):
        file_url = None
        if d.file:
            file_url = get_storage_url(str(d.file))
        auth_docs.append({
            'id': d.id,
            'document_type': d.document_type,
            'title': d.title,
            'file': str(d.file) if d.file else None,
            'file_url': file_url,
            'uploaded_at': d.uploaded_at,
        })

    # Consultant Documents (degree / certificates with Gemini verification)
    consultant_docs = []
    for d in ConsultantDocument.objects.filter(application=app):
        consultant_docs.append({
            'id': d.id,
            'qualification_type': d.qualification_type,
            'document_type': d.document_type,
            'file_path': d.file_path,
            'file_url': get_storage_url(d.file_path),
            'uploaded_at': d.uploaded_at,
            'verification_status': d.verification_status,
            'gemini_raw_response': d.gemini_raw_response,
        })

    return Response({
        'profile': profile,
        'identity_documents': identity_docs,
        'face_verification': face_records,
        'assessment_sessions': assessment_data,
        'documents': {
            'qualification_documents': auth_docs,
            'consultant_documents': consultant_docs,
        },
    })


# ------------------------------------------------------------------
# Credential generation & approval
# ------------------------------------------------------------------

def _generate_and_send_credentials(app):
    """
    Generate unique credentials for a consultant application,
    create the live User + ConsultantServiceProfile, and email the creds.
    """
    if ConsultantCredential.objects.filter(application=app).exists():
        return False, "Credentials already generated for this applicant"

    # DEBUG: Check if email settings are loaded
    from django.conf import settings
    logger.info(f"DEBUG: Attempting to send email for {app.email}")
    if not settings.EMAIL_HOST_USER:
        logger.error(f"CRITICAL: EMAIL_HOST_USER is MISSING in this environment! Check if .env is loaded.")
    else:
        logger.info(f"DEBUG: Using EMAIL_HOST_USER: {settings.EMAIL_HOST_USER}")

    try:
        first_name_clean = ''.join(filter(str.isalnum, app.first_name.lower())) if app.first_name else 'consultant'
        if not first_name_clean:
            first_name_clean = 'user'

        # Generate unique username
        username = ''
        for _ in range(10):
            random_digits = ''.join(random.choices(string.digits, k=4))
            candidate = f"taxplanadvisor_{first_name_clean}_{random_digits}"
            if not ConsultantCredential.objects.filter(username=candidate).exists():
                username = candidate
                break

        if not username:
            return False, "Failed to generate unique username"

        # Generate random password
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(random.choices(chars, k=10))

        # Save credential record
        ConsultantCredential.objects.create(
            application=app,
            username=username,
            password=password,
        )

        # Create the live User and ConsultantServiceProfile
        from core_auth.models import User
        from consultants.models import ConsultantServiceProfile

        # OPTION A ENFORCEMENT: If a CLIENT already exists with this email, block credential
        # generation. The admin must ask the applicant to re-apply with a different email.
        existing_user = User.objects.filter(email=app.email).first()
        if existing_user and existing_user.role == User.CLIENT:
            return False, (
                f"Cannot generate credentials: {app.email} is already registered as a Client. "
                "Ask the applicant to use a different email address for their consultant account."
            )

        user, created = User.objects.get_or_create(
            email=app.email,
            defaults={
                'username': username,
                'first_name': app.first_name,
                'last_name': app.last_name,
                'phone_number': app.phone_number,
                'role': User.CONSULTANT,
                'is_onboarded': True,
                'is_phone_verified': True,
                'google_id': app.google_id,
            }
        )

        # Update existing CONSULTANT user (safe — same role, just refresh fields)
        if not created:
            user.username = username
            user.role = User.CONSULTANT
            user.is_onboarded = True
            user.first_name = app.first_name or user.first_name
            user.last_name = app.last_name or user.last_name
            user.phone_number = app.phone_number or user.phone_number

        user.set_password(password)
        user.save()

        # Create consultant service profile
        ConsultantServiceProfile.objects.get_or_create(
            user=user,
            defaults={
                'qualification': app.qualification,
                'experience_years': app.experience_years or 0,
                'certifications': app.certifications,
                'bio': app.bio,
                'is_active': True,
            }
        )

        # Mark application as approved
        app.status = 'APPROVED'
        app.save()

        # Email credentials
        subject = "Your TaxPlan Advisor Consultant Credentials"
        message = (
            f"Hello {app.get_full_name()},\n\n"
            f"Congratulations! Your verification is complete.\n"
            f"Here are your login credentials for the consultant portal:\n\n"
            f"Username: {username}\n"
            f"Password: {password}\n\n"
            f"Login at: https://main.taxplanadvisor.co\n\n"
            f"Please keep these credentials safe and do not share them.\n\n"
            f"Best regards,\nTaxPlan Advisor Team"
        )

        try:
            from_email = settings.DEFAULT_FROM_EMAIL or 'admin@taxplanadvisor.com'
            send_mail(
                subject,
                message,
                from_email,
                [app.email],
                fail_silently=False,  # Raise exception so we can log it
            )
            logger.info(f"Credentials email sent successfully to {app.email}")
        except Exception as email_err:
            # Log prominently but don't fail credential generation — creds are already saved
            logger.error(
                f"CRITICAL: Credentials were generated for {app.email} but email FAILED to send. "
                f"Error: {email_err}. "
                f"Username: {username} | Password saved in ConsultantCredential record."
            )

        return True, {"username": username, "password": password, "message": "Credentials generated and sent successfully"}

    except Exception as e:
        return False, str(e)


@api_view(['POST'])
@authentication_classes([AdminJWTAuthentication])
@permission_classes([AllowAny])
def generate_credentials(request, app_id):
    """Generate credentials for a consultant and email them."""
    try:
        app = ConsultantApplication.objects.get(id=app_id)
    except ConsultantApplication.DoesNotExist:
        return Response({'error': 'Consultant not found'}, status=status.HTTP_404_NOT_FOUND)

    success, result = _generate_and_send_credentials(app)

    if success:
        return Response(result, status=status.HTTP_201_CREATED)
    else:
        status_code = status.HTTP_400_BAD_REQUEST if "already generated" in str(result) else status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response({'error': result}, status=status_code)
