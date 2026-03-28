from datetime import timedelta
from django.db import models
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from gst_reports.models import UnifiedGSTSession
from gst_reports.utils import (
    safe_api_call, get_sandbox_access_token, get_gst_headers, 
    cleanup_expired_sessions, find_active_gst_session
)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_otp(request):
    """Step 1: Generate OTP for GST authentication."""
    gstin = request.data.get("gstin", "").strip().upper()
    username = request.data.get("username", "").strip()
    
    if not gstin or len(gstin) != 15:
        return Response({
            "error": "Valid 15-character GSTIN is required",
            "received_gstin": gstin,
            "received_data": request.data
        }, status=400)
    
    if not username:
        return Response({"error": "GST Portal username is required"}, status=400)

    # Security: If user is a consultant, ensure this GSTIN belongs to an assigned client
    client_id_param = request.data.get("client_id")
    if request.user.role == 'CONSULTANT':
        from core_auth.models import ClientProfile
        from consultants.models import ClientServiceRequest
        # Check if any active service client has this GSTIN
        service_client_ids = ClientServiceRequest.objects.filter(
            assigned_consultant__user=request.user,
        ).exclude(status__in=['completed', 'cancelled']).values_list('client_id', flat=True)
        
        is_assigned = False
        if client_id_param:
            try:
                client_id_val = int(client_id_param)
                if client_id_val not in service_client_ids:
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied("You are not assigned to this client.")
                
                # Fetch profile and update GSTIN/Username (Consultants are trusted for assigned clients)
                client_profile, created = ClientProfile.objects.get_or_create(user_id=client_id_val)
                
                # Update if blank or different
                needs_save = False
                if client_profile.gstin != gstin.strip().upper():
                    client_profile.gstin = gstin.strip().upper()
                    needs_save = True
                if username and client_profile.gst_username != username:
                    client_profile.gst_username = username
                    needs_save = True
                
                if needs_save:
                    client_profile.save(update_fields=['gstin', 'gst_username'])
                
                is_assigned = True
            except (ValueError, ClientProfile.DoesNotExist):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Invalid client specified.")
        else:
            # If no client_id, check if GSTIN already exists for an assigned client
            is_assigned = ClientProfile.objects.filter(
                user_id__in=service_client_ids,
                gstin__iexact=gstin.strip()
            ).exists()
            
            if not is_assigned:
                # Fallback: if they have any assigned clients with a blank GSTIN, allow it.
                blank_profiles = ClientProfile.objects.filter(
                    user_id__in=service_client_ids
                ).filter(models.Q(gstin__isnull=True) | models.Q(gstin=""))
                
                if blank_profiles.exists():
                    is_assigned = True
                    # Auto assign if there's exactly one such client
                    if blank_profiles.count() == 1:
                        p = blank_profiles.first()
                        p.gstin = gstin.strip().upper()
                        if username:
                            p.gst_username = username
                        p.save(update_fields=['gstin', 'gst_username'])
        
        if not is_assigned:
            # If still not assigned, we allow consultants to proceed anyway as requested, 
            # but we won't be able to auto-save the GSTIN to a profile.
            import logging
            logger = logging.getLogger('gst_reports')
            logger.info(f"Consultant {request.user.email} generating OTP for unassigned GSTIN {gstin}")

    access_token, error = get_sandbox_access_token()
    if error:
        return Response({"error": error}, status=500)
    
    status_code, otp_data = safe_api_call(
        "POST",
        "https://api.sandbox.co.in/gst/compliance/tax-payer/otp",
        json={"username": username, "gstin": gstin},
        headers=get_gst_headers(access_token)
    )
    
    if status_code != 200:
        error_msg = otp_data.get("message") or otp_data.get("error", {}).get("message") or str(otp_data)
        return Response({"error": f"Sandbox API Error ({status_code}): {error_msg}"}, status=400)
    
    data = otp_data.get("data", {})
    if data.get("status_cd") == "0" or not data:
        error_msg = data.get("message", otp_data.get("message", "Failed to send OTP"))
        return Response({
            "error": error_msg,
            "error_code": data.get("error", {}).get("error_cd", "")
        }, status=400)
    
    session = UnifiedGSTSession.objects.create(
        user=request.user,
        gstin=gstin,
        gst_username=username,
        access_token=access_token,
        transaction_id=otp_data.get("transaction_id", ""),
        expires_at=timezone.now() + timedelta(minutes=10) 
    )
    cleanup_expired_sessions()
    
    return Response({
        "success": True,
        "message": "OTP sent successfully",
        "session_id": str(session.session_id)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_otp(request):
    """Step 2: Verify OTP and activate session."""
    session_id = request.data.get("session_id")
    otp = request.data.get("otp", "").strip()
    username = request.data.get("username", "").strip()
    
    if not session_id:
        return Response({"error": "Session ID is required"}, status=400)
    if not otp:
        return Response({"error": "OTP is required"}, status=400)
    if not username:
        return Response({"error": "GST Portal username is required"}, status=400)
    
    try:
        session = UnifiedGSTSession.objects.get(session_id=session_id, user=request.user)
    except UnifiedGSTSession.DoesNotExist:

        return Response({"error": "Invalid session"}, status=400)
    
    if session.is_expired():
 
        return Response({"error": "Session expired - please request new OTP"}, status=400)
    
    if session.is_verified:
        return Response({
            "success": True,
            "message": "Session already verified",
            "session_id": str(session.session_id)
        })
    

    status_code, verify_data = safe_api_call(
        "POST",
        "https://api.sandbox.co.in/gst/compliance/tax-payer/otp/verify",
        json={"username": username, "gstin": session.gstin},
        params={"otp": otp},
        headers=get_gst_headers(session.access_token)
    )

    if status_code != 200:
        error_msg = verify_data.get("message") or verify_data.get("error", {}).get("message") or str(verify_data)
        return Response({"error": f"Sandbox API Error ({status_code}): {error_msg}"}, status=400)
    
    data = verify_data.get("data", {})
    taxpayer_token = data.get("access_token")
    
    if data.get("status_cd") == "0" or not taxpayer_token:
        error_msg = data.get("message", verify_data.get("error", {}).get("message", "OTP verification failed"))
        return Response({"error": error_msg}, status=400)
    
    session.taxpayer_token = taxpayer_token
    session.is_verified = True
    session.gst_username = username
    session.expires_at = timezone.now() + timedelta(hours=6)  
    session.save(update_fields=["taxpayer_token", "is_verified", "gst_username", "expires_at", "updated_at"])
    
    return Response({
        "success": True,
        "message": "OTP verified successfully",
        "session_id": str(session.session_id),
        "gstin": session.gstin,
        "username": session.gst_username
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def session_status(request):
    """Check if a session is valid and get remaining time."""
    session_id = request.query_params.get("session_id") 
    if not session_id:
        return Response({"error": "Session ID is required"}, status=400)
    
    try:
        session = UnifiedGSTSession.objects.get(session_id=session_id, user=request.user)
    except UnifiedGSTSession.DoesNotExist:
        return Response({"is_valid": False, "error": "Session not found"})
    
    if session.is_expired():
        return Response({"is_valid": False, "error": "Session expired"})
    
    remaining_seconds = (session.expires_at - timezone.now()).total_seconds()
    return Response({
        "is_valid": session.is_valid(),
        "is_verified": session.is_verified,
        "gstin": session.gstin,
        "username": session.gst_username,
        "expires_in_seconds": int(remaining_seconds),
        "expires_in_minutes": int(remaining_seconds / 60)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_active_session(request):
    """
    Check if there is any active, verified session for a given GSTIN
    that the current user is authorized to use.
    """
    gstin = request.query_params.get("gstin", "").strip().upper()
    if not gstin:
        return Response({"error": "GSTIN is required"}, status=400)
    
    session = find_active_gst_session(request.user, gstin)
    
    if session:
        remaining_seconds = (session.expires_at - timezone.now()).total_seconds()
        return Response({
            "has_active_session": True,
            "session_id": str(session.session_id),
            "gstin": session.gstin,
            "username": session.gst_username,
            "expires_in_seconds": int(remaining_seconds),
            "expires_in_minutes": int(remaining_seconds / 60)
        })
    
    return Response({
        "has_active_session": False,
        "message": "No active session found for this GSTIN"
    })
