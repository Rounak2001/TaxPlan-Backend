from datetime import timedelta
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from gst_reports.models import UnifiedGSTSession
from gst_reports.utils import safe_api_call, get_sandbox_access_token, get_gst_headers, cleanup_expired_sessions


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
    if request.user.role == 'CONSULTANT':
        from core_auth.models import ClientProfile
        # Check if any assigned client has this GSTIN
        # Note: A consultant might have multiple clients, we need to check if *any* assigned client matches the requested GSTIN.
        # However, ClientProfile has a 1-to-1 with User. 
        # The query should be: Is there a ClientProfile assigned to this consultant THAT HAS this GSTIN?
        is_assigned = ClientProfile.objects.filter(
            assigned_consultant=request.user,
            gstin=gstin
        ).exists()
        
        if not is_assigned:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You are not authorized to access this GSTIN. Please ensure the client is assigned to you.")

    
    access_token, error = get_sandbox_access_token()
    if error:
        return Response({"error": error}, status=500)
    
    status_code, otp_data = safe_api_call(
        "POST",
        "https://api.sandbox.co.in/gst/compliance/tax-payer/otp",
        json={"username": username, "gstin": gstin},
        headers=get_gst_headers(access_token)
    )
    
    data = otp_data.get("data", {})
    if data.get("status_cd") == "0":
        return Response({
            "error": data.get("message", "Failed to send OTP"),
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
