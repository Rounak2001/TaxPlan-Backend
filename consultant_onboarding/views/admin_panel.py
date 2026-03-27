"""
Admin Panel API views for the consultant onboarding system.
Ported from backend1/consultant_core/views/admin.py and adapted
to use ConsultantApplication instead of a custom User model.
"""
import jwt
import logging
import random
import string
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from django.conf import settings
from django.core.mail import send_mail
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone as dj_timezone

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
from core_auth.models import User

logger = logging.getLogger(__name__)

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
# Optional dev-only bypass (for local testing)
# ------------------------------------------------------------------
_ALLOW_INSECURE_ADMIN = (
    bool(getattr(settings, "DEBUG", False))
    and str(os.getenv("ALLOW_INSECURE_ADMIN", "")).strip().lower() in {"1", "true", "yes", "y"}
)
ADMIN_AUTH_CLASSES = [] if _ALLOW_INSECURE_ADMIN else [AdminJWTAuthentication]

DEV_BOOTSTRAP_EMAIL = os.getenv('DEV_CONSULTANT_EMAIL', 'dev.consultant@local.test').strip().lower()
DEV_BOOTSTRAP_USERNAME = os.getenv('DEV_CONSULTANT_USERNAME', 'dev_consultant').strip() or 'dev_consultant'
DEV_BOOTSTRAP_PASSWORD = os.getenv('DEV_CONSULTANT_PASSWORD', 'DevConsultant@123').strip() or 'DevConsultant@123'


def _require_debug_mode():
    return bool(getattr(settings, 'DEBUG', False))


def _build_live_user_defaults(app, username):
    return {
        'username': username,
        'first_name': app.first_name,
        'last_name': app.last_name,
        'phone_number': app.phone_number,
        'role': User.CONSULTANT,
        'is_onboarded': True,
        'is_phone_verified': True,
    }


def _find_phone_conflict(app, *, existing_user=None):
    phone_number = (app.phone_number or '').strip()
    if not phone_number:
        return None

    conflicts = User.objects.filter(phone_number=phone_number)
    if existing_user is not None:
        conflicts = conflicts.exclude(id=existing_user.id)

    return conflicts.first()


def _ensure_unique_consultant_username(base_username, *, existing_user=None, application=None):
    candidate = (base_username or 'consultant').strip() or 'consultant'
    suffix = 0

    while True:
        username = candidate if suffix == 0 else f"{candidate}_{suffix}"
        credential_taken = ConsultantCredential.objects.filter(username=username)
        user_taken = User.objects.filter(username=username)

        if application is not None:
            credential_taken = credential_taken.exclude(application=application)

        if existing_user is not None:
            credential_taken = credential_taken.exclude(application__email=existing_user.email)
            user_taken = user_taken.exclude(id=existing_user.id)

        if not credential_taken.exists() and not user_taken.exists():
            return username

        suffix += 1


def _ensure_live_consultant_user(app, username, password=None):
    """
    Create or refresh the live consultant account/profile from an onboarding application.
    """
    from consultants.models import ConsultantServiceProfile

    existing_user = User.objects.filter(email=app.email).first()
    if existing_user and existing_user.role == User.CLIENT:
        raise ValueError(
            f"Cannot generate credentials: {app.email} is already registered as a Client. "
            "Ask the applicant to use a different email address for their consultant account."
        )

    phone_conflict = _find_phone_conflict(app, existing_user=existing_user)
    if phone_conflict:
        raise ValueError(
            f"Cannot generate credentials: phone number {app.phone_number} is already used by "
            f"{phone_conflict.email} ({phone_conflict.role})."
        )

    resolved_username = _ensure_unique_consultant_username(
        username,
        existing_user=existing_user,
        application=app,
    )

    user, created = User.objects.get_or_create(
        email=app.email,
        defaults=_build_live_user_defaults(app, resolved_username),
    )

    if not created:
        user.username = resolved_username
        user.role = User.CONSULTANT
        user.is_onboarded = True
        user.is_phone_verified = True
        user.first_name = app.first_name or user.first_name
        user.last_name = app.last_name or user.last_name
        user.phone_number = app.phone_number or user.phone_number

    if password:
        user.set_password(password)
    user.save()

    consultant_profile, _profile_created = ConsultantServiceProfile.objects.get_or_create(
        user=user,
        defaults={
            'qualification': app.qualification,
            'experience_years': app.experience_years or 0,
            'certifications': app.certifications,
            'bio': app.bio,
            'is_active': True,
        }
    )

    from ..expertise_sync import sync_passed_sessions_to_consultant
    sync_passed_sessions_to_consultant(app, consultant_profile=consultant_profile)

    return user, consultant_profile


def _ensure_dev_bootstrap_assessment(application):
    """
    In DEBUG mode, create a minimal passed assessment session if the application has none.
    """
    if UserSession.objects.filter(application=application, status='completed', score__gte=35).exists():
        return

    UserSession.objects.create(
        application=application,
        selected_domains=['itr'],
        selected_test_details={
            'itr': {
                'selected_service_ids': ['itr_salary_filing', 'itr_general_consultation'],
            }
        },
        question_set=[{'id': 1, 'domain': 'itr'}],
        video_question_set=[],
        score=35,
        status='completed',
        end_time=dj_timezone.now(),
    )


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


def _storage_path_from_url_or_path(value):
    """Best-effort conversion of a storage URL/path to a key usable by default_storage.delete."""
    if not value:
        return None

    text = str(value)
    # Already looks like a storage-relative path.
    if not text.startswith('http://') and not text.startswith('https://'):
        return text.lstrip('/')

    try:
        parsed = urlparse(text)
        return parsed.path.lstrip('/')
    except Exception:
        return None


def _safe_delete_storage_file(value):
    path = _storage_path_from_url_or_path(value)
    if not path:
        return
    try:
        default_storage.delete(path)
    except Exception:
        # Storage cleanup should not block deletion of database records.
        pass


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
@authentication_classes(ADMIN_AUTH_CLASSES)
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
            'is_phone_verified': app.is_phone_verified,
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
@authentication_classes(ADMIN_AUTH_CLASSES)
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
        'is_phone_verified': app.is_phone_verified,
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
                'snapshot_id': snap.snapshot_id,
                'image_path': snap.image_url,
                'image_url': get_storage_url(snap.image_url),
                'timestamp': snap.timestamp.isoformat() if snap.timestamp else None,
                'is_violation': snap.is_violation,
                'violation_reason': snap.violation_reason,
                'face_count': snap.face_count,
                'match_score': snap.match_score,
                'pose_yaw': snap.pose_yaw,
                'pose_pitch': snap.pose_pitch,
                'pose_roll': snap.pose_roll,
                'mouth_state': snap.mouth_state,
                'audio_detected': snap.audio_detected,
                'gaze_violation': snap.gaze_violation,
                'label_detection_results': snap.label_detection_results,
                'rule_outcomes': snap.rule_outcomes,
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
            'mcq_score': s.score,
            'mcq_total': len(s.question_set or []),
            'mcq_answered': (
                sum(1 for _k, v in (s.mcq_answers or {}).items() if v not in {None, ''})
                if isinstance(s.mcq_answers, dict) else 0
            ),
            'mcq_answers': s.mcq_answers if isinstance(s.mcq_answers, dict) else {},
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
    existing_credential = ConsultantCredential.objects.filter(application=app).first()
    existing_live_user = User.objects.filter(email=app.email, role=User.CONSULTANT).first()
    if existing_credential:
        if existing_live_user or _require_debug_mode():
            user, consultant_profile = _ensure_live_consultant_user(
                app,
                existing_credential.username,
                password=existing_credential.password,
            )
            app.status = 'APPROVED'
            app.save(update_fields=['status', 'updated_at'])
            return True, {
                'username': user.username,
                'password': existing_credential.password,
                'message': 'Existing credentials restored',
                'email': app.email,
                'profile_id': consultant_profile.id,
            }
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
            if (
                not ConsultantCredential.objects.filter(username=candidate).exists()
                and not User.objects.filter(username=candidate).exists()
            ):
                username = candidate
                break

        if not username:
            return False, "Failed to generate unique username"

        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(random.choices(chars, k=10))

        with transaction.atomic():
            credential = ConsultantCredential.objects.create(
                application=app,
                username=username,
                password=password,
            )

            user, _consultant_profile = _ensure_live_consultant_user(
                app,
                username,
                password=password,
            )
            final_username = user.username
            if credential.username != final_username:
                credential.username = final_username
                credential.save(update_fields=['username'])

            app.status = 'APPROVED'
            app.save()

        subject = "Your TaxPlan Advisor Consultant Credentials"
        message = (
            f"Hello {app.get_full_name()},\n\n"
            f"Congratulations! Your verification is complete.\n"
            f"Here are your login credentials for the consultant portal:\n\n"
            f"Username: {final_username}\n"
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
                fail_silently=False,
            )
            logger.info(f"Credentials email sent successfully to {app.email}")
        except Exception as email_err:
            logger.error(
                f"CRITICAL: Credentials were generated for {app.email} but email FAILED to send. "
                f"Error: {email_err}. Username: {final_username} | Password saved in ConsultantCredential record."
            )

        return True, {
            "username": final_username,
            "password": password,
            "message": "Credentials generated and sent successfully",
        }


    except Exception as e:
        return False, str(e)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def dev_bootstrap_consultant(request):
    """
    DEBUG-only helper to spin up a consultant account with predictable credentials.
    """
    if not _require_debug_mode():
        return Response({'error': 'Not available outside DEBUG mode.'}, status=status.HTTP_404_NOT_FOUND)

    email = str(request.data.get('email') or DEV_BOOTSTRAP_EMAIL).strip().lower()
    first_name = str(request.data.get('first_name') or 'Dev').strip() or 'Dev'
    last_name = str(request.data.get('last_name') or 'Consultant').strip() or 'Consultant'
    username_seed = str(request.data.get('username') or DEV_BOOTSTRAP_USERNAME).strip() or DEV_BOOTSTRAP_USERNAME
    password = str(request.data.get('password') or DEV_BOOTSTRAP_PASSWORD)
    phone_number = str(request.data.get('phone_number') or '+919876543210').strip() or '+919876543210'

    existing_user = User.objects.filter(email=email).first()
    if existing_user and existing_user.role == User.CLIENT:
        return Response(
            {'error': f'{email} is already registered as a client. Use a different dev email.'},
            status=status.HTTP_409_CONFLICT,
        )

    application, _created = ConsultantApplication.objects.get_or_create(
        email=email,
        defaults={
            'first_name': first_name,
            'last_name': last_name,
            'phone_number': phone_number,
            'is_phone_verified': True,
            'is_verified': True,
            'has_accepted_declaration': True,
            'status': 'APPROVED',
            'qualification': 'CA',
            'experience_years': 3,
            'city': 'Pune',
            'state': 'Maharashtra',
            'pincode': '411001',
            'address_line1': 'Dev Mode Address',
        }
    )

    application.first_name = first_name
    application.last_name = last_name
    application.phone_number = phone_number
    application.is_phone_verified = True
    application.is_verified = True
    application.has_accepted_declaration = True
    application.status = 'APPROVED'
    application.qualification = application.qualification or 'CA'
    application.experience_years = application.experience_years or 3
    application.city = application.city or 'Pune'
    application.state = application.state or 'Maharashtra'
    application.pincode = application.pincode or '411001'
    application.address_line1 = application.address_line1 or 'Dev Mode Address'
    application.save()

    _ensure_dev_bootstrap_assessment(application)

    username = _ensure_unique_consultant_username(
        username_seed,
        existing_user=existing_user,
        application=application,
    )
    credential, _credential_created = ConsultantCredential.objects.update_or_create(
        application=application,
        defaults={'username': username, 'password': password},
    )
    user, consultant_profile = _ensure_live_consultant_user(
        application,
        credential.username,
        password=password,
    )

    return Response(
        {
            'message': 'DEBUG consultant ready',
            'email': application.email,
            'application_id': application.id,
            'username': user.username,
            'password': password,
            'profile_id': consultant_profile.id,
            'passed_categories': ['itr'],
            'auto_unlocked_categories': ['registrations'],
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@authentication_classes(ADMIN_AUTH_CLASSES)
@permission_classes([AllowAny])
def generate_credentials(request, app_id):
    """Generate credentials for a consultant and email them."""
    try:
        app = ConsultantApplication.objects.get(id=app_id)
    except ConsultantApplication.DoesNotExist:
        return Response({'error': 'Consultant not found'}, status=status.HTTP_404_NOT_FOUND)

    success, result = _generate_and_send_credentials(app)

    if success:
        response_status = (
            status.HTTP_200_OK
            if "Existing credentials returned" in str(result.get('message', ''))
            else status.HTTP_201_CREATED
        )
        return Response(result, status=response_status)
    else:
        status_code = status.HTTP_400_BAD_REQUEST if "already generated" in str(result) else status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response({'error': result}, status=status_code)


@api_view(['DELETE'])
@authentication_classes(ADMIN_AUTH_CLASSES)
@permission_classes([AllowAny])
def delete_consultant(request, app_id):
    """Delete onboarding consultant application and linked consultant user (if any)."""
    try:
        app = ConsultantApplication.objects.get(id=app_id)
    except ConsultantApplication.DoesNotExist:
        return Response({'error': 'Consultant not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        with transaction.atomic():
            # Delete stored onboarding files before deleting rows.
            for d in AuthConsultantDocument.objects.filter(application=app):
                if d.file:
                    _safe_delete_storage_file(str(d.file))

            for d in IdentityDocument.objects.filter(application=app):
                _safe_delete_storage_file(d.file_path)

            for d in ConsultantDocument.objects.filter(application=app):
                _safe_delete_storage_file(d.file_path)

            for f in FaceVerification.objects.filter(application=app):
                _safe_delete_storage_file(f.id_image_path)
                _safe_delete_storage_file(f.live_image_path)

            for s in UserSession.objects.filter(application=app):
                for v in VideoResponse.objects.filter(session=s):
                    _safe_delete_storage_file(v.video_file)
                for snap in ProctoringSnapshot.objects.filter(session=s):
                    _safe_delete_storage_file(snap.image_url)

            # Remove the live consultant account only (never delete client accounts by email).
            User.objects.filter(email=app.email, role=User.CONSULTANT).delete()

            app_email = app.email
            app_id_value = app.id
            app.delete()
    except Exception:
        logger.exception(
            "Failed to delete consultant app_id=%s email=%s",
            app.id,
            app.email,
        )
        return Response(
            {'error': 'Failed to delete consultant. Please retry or check server logs.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {'message': 'Consultant deleted successfully', 'id': app_id_value, 'email': app_email},
        status=status.HTTP_200_OK
    )
