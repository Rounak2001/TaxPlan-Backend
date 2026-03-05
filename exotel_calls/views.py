import requests
import base64
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils import timezone
from core_auth.models import ClientProfile, User
from .models import CallLog


class InitiateCallView(APIView):
    """
    API to initiate a call between a consultant and their assigned client.
    
    Flow:
    1. Consultant clicks "Call" button on the frontend (with client_id).
    2. Backend verifies consultant is authenticated and client is assigned to them.
    3. Backend makes POST request to Exotel API.
    4. Exotel calls the consultant FIRST.
    5. When consultant picks up, Exotel calls the client.
    6. Both are connected via Exotel's virtual number (CallerID).
    
    Security:
    - Client phone number is NEVER sent to the frontend.
    - Only the backend has access to phone numbers for calling.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        # Only consultants can make calls
        if user.role != User.CONSULTANT:
            return Response(
                {'error': 'Only consultants can initiate calls'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        client_id = request.data.get('client_id')
        if not client_id:
            return Response(
                {'error': 'client_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Move imports to appropriate scope or ensure they are at top
        from consultants.models import ClientServiceRequest
        from django.db.models import Q

        # Security: Verify this client is assigned to the requesting consultant (Primary or Service)
        is_primary = ClientProfile.objects.filter(user_id=client_id, assigned_consultant=user).exists()
        is_service = ClientServiceRequest.objects.filter(client_id=client_id, assigned_consultant__user=user).exists()
        
        if not (is_primary or is_service):
            return Response(
                {'error': 'Client not found or not assigned to you'}, 
                status=status.HTTP_404_NOT_FOUND
            )

        client_user = User.objects.get(id=client_id)
        
        # Get phone numbers (securely, never exposing to frontend)
        consultant_phone = user.phone_number
        client_phone = client_user.phone_number
        
        if not consultant_phone:
            return Response(
                {'error': 'Your phone number is not configured. Please update your profile.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not client_phone:
            return Response(
                {'error': 'Client phone number is not available.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create CallLog entry
        call_log = CallLog.objects.create(
            caller=user,
            callee=client_user,
            status='initiated'
        )
        
        # Make Exotel API call
        try:
            exotel_response = self._make_exotel_call(
                from_number=consultant_phone,
                to_number=client_phone,
                call_log_id=call_log.id
            )
            
            if exotel_response.get('success'):
                call_log.exotel_sid = exotel_response.get('sid')
                call_log.status = 'ringing'
                call_log.save()
                
                return Response({
                    'success': True,
                    'message': f'Calling you now. Please pick up to connect with {client_user.get_full_name() or client_user.username}.',
                    'call_id': call_log.id
                })
            else:
                call_log.status = 'failed'
                call_log.save()
                return Response({
                    'success': False,
                    'error': exotel_response.get('error', 'Failed to initiate call')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            call_log.status = 'failed'
            call_log.save()
            return Response({
                'success': False,
                'error': f'Call initiation failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _make_exotel_call(self, from_number, to_number, call_log_id):
        """
        Makes the actual HTTP POST request to Exotel API.
        """
        api_key = settings.EXOTEL_API_KEY
        api_token = settings.EXOTEL_API_TOKEN
        sid = settings.EXOTEL_SID
        caller_id = settings.EXOTEL_CALLER_ID
        subdomain = settings.EXOTEL_SUBDOMAIN
        
        url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/connect.json"
        
        auth_string = f"{api_key}:{api_token}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {
            'Authorization': f'Basic {auth_bytes}',
        }
        
        data = {
            'From': from_number,
            'To': to_number,
            'CallerId': caller_id,
            'Record': 'true',
            'StatusCallback': f"{settings.BACKEND_URL}/api/calls/status-callback/",
            'StatusCallbackContentType': 'application/json',
            'StatusCallbackEvents[0]': 'terminal',
            'CustomField': str(call_log_id),
        }
        
        response = requests.post(url, headers=headers, data=data, timeout=30)
        
        if response.status_code == 200:
            response_data = response.json()
            call_data = response_data.get('Call', {})
            return {
                'success': True,
                'sid': call_data.get('Sid'),
                'status': call_data.get('Status'),
            }
        else:
            print(f"Exotel API Error: {response.status_code} - {response.text}")
            return {
                'success': False,
                'error': f"Exotel API returned {response.status_code}",
                'details': response.text
            }


class CallStatusCallbackView(APIView):
    """
    Webhook endpoint for Exotel StatusCallback.
    
    Receives call status when call ends (terminal event).
    Immediately fetches Price from Call Details API since Price is not in callback.
    
    Callback URL: https://main.taxplanadvisor.co/api/calls/status-callback/
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        """
        Handle Exotel StatusCallback webhook (terminal event).
        
        Expected fields from Exotel (JSON format):
        - CallSid: Unique call identifier
        - Status: completed, busy, failed, no-answer, canceled
        - From/To: Phone numbers
        - RecordingUrl: Recording URL if Record=true
        - ConversationDuration: Duration in seconds
        - StartTime/EndTime: Timestamps (YYYY-MM-DD HH:mm:ss)
        - CustomField: Our call_log_id
        - Legs[]: Array with per-leg status
        
        Note: Price is NOT in callback, must fetch from Call Details API.
        """
        import json
        import logging
        import os
        
        # Set up logging (production: /var/log, dev: /tmp)
        log_dir = '/var/log/taxplanadvisor' if os.path.exists('/var/log/taxplanadvisor') else '/tmp'
        log_file = os.path.join(log_dir, 'exotel.log')
        
        logger = logging.getLogger('exotel_callback')
        if not logger.handlers:
            handler = logging.FileHandler(log_file)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        
        # Parse request data (supports both JSON and form-data)
        if request.content_type == 'application/json':
            data = request.data
        else:
            # Handle form-data (multipart/form-data)
            data = {}
            for key in request.POST:
                data[key] = request.POST[key]
            # Handle Legs[] array from form-data
            legs = []
            i = 0
            while f'Legs[{i}][Status]' in request.POST:
                leg = {
                    'Status': request.POST.get(f'Legs[{i}][Status]'),
                    'OnCallDuration': request.POST.get(f'Legs[{i}][OnCallDuration]'),
                    'RingingDuration': request.POST.get(f'Legs[{i}][RingingDuration]'),
                    'AnsweredBy': request.POST.get(f'Legs[{i}][AnsweredBy]'),
                }
                legs.append(leg)
                i += 1
            if legs:
                data['Legs'] = legs
        
        # Log the raw callback for debugging
        logger.info(f"=== EXOTEL CALLBACK RECEIVED ===")
        logger.info(f"Content-Type: {request.content_type}")
        logger.info(f"Data: {json.dumps(data, indent=2, default=str)}")
        
        # Get our call_log_id from CustomField
        call_log_id = data.get('CustomField')
        if not call_log_id:
            logger.warning("No CustomField in callback - ignoring")
            return Response({'status': 'ignored', 'reason': 'no CustomField'}, status=status.HTTP_200_OK)
        
        try:
            call_log = CallLog.objects.get(id=call_log_id)
            logger.info(f"Found CallLog ID: {call_log_id}")
            
            # === 1. Save CallSid ===
            call_sid = data.get('CallSid') or data.get('Sid')
            if call_sid:
                call_log.exotel_sid = call_sid
                logger.info(f"CallSid: {call_sid}")
            
            # === 2. Update Status ===
            exotel_status = str(data.get('Status', '')).lower()
            status_mapping = {
                'completed': 'completed',
                'busy': 'busy',
                'failed': 'failed',
                'no-answer': 'no-answer',
                'canceled': 'canceled',
                'in-progress': 'in-progress',
                'queued': 'initiated',
                'ringing': 'ringing',
            }
            call_log.status = status_mapping.get(exotel_status, exotel_status or call_log.status)
            logger.info(f"Status: {call_log.status}")
            
            # === 3. Duration ===
            duration = data.get('ConversationDuration') or data.get('Duration') or 0
            try:
                call_log.duration = int(duration)
            except (ValueError, TypeError):
                call_log.duration = 0
            logger.info(f"Duration: {call_log.duration}s")
            
            # === 4. Recording URL ===
            recording_url = data.get('RecordingUrl') or data.get('RecordingURL')
            if recording_url:
                call_log.recording_url = str(recording_url)
                logger.info(f"RecordingUrl: {recording_url}")
            
            # === 5. Phone Numbers ===
            if data.get('From'):
                call_log.from_number = data['From']
            if data.get('To'):
                call_log.to_number = data['To']
            
            # === 6. Timestamps ===
            time_formats = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']
            
            start_time = data.get('StartTime')
            if start_time:
                for fmt in time_formats:
                    try:
                        parsed = datetime.strptime(str(start_time), fmt)
                        call_log.start_time = timezone.make_aware(parsed)
                        break
                    except ValueError:
                        continue
            
            end_time = data.get('EndTime')
            if end_time:
                for fmt in time_formats:
                    try:
                        parsed = datetime.strptime(str(end_time), fmt)
                        call_log.end_time = timezone.make_aware(parsed)
                        break
                    except ValueError:
                        continue
            
            # === 7. Log Legs info (for debugging) ===
            legs = data.get('Legs', [])
            if legs:
                logger.info(f"Legs data: {json.dumps(legs, default=str)}")
            
            # Save first to persist callback data
            call_log.save()
            logger.info(f"CallLog {call_log_id} saved with callback data")
            
            # === 8. Fetch Price from Call Details API ===
            # Price is NOT in StatusCallback, must fetch separately
            if call_sid:
                try:
                    price_data = self._fetch_call_details(call_sid)
                    if price_data.get('success'):
                        details = price_data.get('details', {})
                        
                        # Get Price
                        price = details.get('Price')
                        if price:
                            try:
                                call_log.price = float(price)
                                logger.info(f"Price fetched: {price}")
                            except (ValueError, TypeError):
                                logger.warning(f"Could not parse price: {price}")
                        
                        # Get Recording URL if missing (backup)
                        if not call_log.recording_url:
                            rec_url = details.get('RecordingUrl')
                            if rec_url:
                                call_log.recording_url = rec_url
                                logger.info(f"RecordingUrl from API: {rec_url}")
                        
                        call_log.save()
                        logger.info(f"CallLog {call_log_id} updated with Call Details API data")
                    else:
                        logger.warning(f"Call Details API failed: {price_data.get('error')}")
                        
                except Exception as e:
                    logger.error(f"Error fetching Call Details: {e}")
            
            logger.info(f"=== CALLBACK PROCESSING COMPLETE ===")
            return Response({'status': 'updated', 'call_log_id': call_log_id}, status=status.HTTP_200_OK)
            
        except CallLog.DoesNotExist:
            logger.error(f"CallLog with ID {call_log_id} not found")
            return Response({'status': 'not_found', 'call_log_id': call_log_id}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error processing callback: {e}", exc_info=True)
            return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_200_OK)
    
    def _fetch_call_details(self, call_sid):
        """
        Fetch call details from Exotel API to get Price.
        
        API: GET https://{subdomain}/v1/Accounts/{sid}/Calls/{CallSid}.json
        
        Note: Price may take ~2 minutes after call ends to be populated.
        """
        api_key = settings.EXOTEL_API_KEY
        api_token = settings.EXOTEL_API_TOKEN
        sid = settings.EXOTEL_SID
        subdomain = settings.EXOTEL_SUBDOMAIN
        
        url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/{call_sid}.json"
        
        auth_string = f"{api_key}:{api_token}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {'Authorization': f'Basic {auth_bytes}'}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                response_data = response.json()
                return {
                    'success': True,
                    'details': response_data.get('Call', {})
                }
            else:
                return {
                    'success': False,
                    'error': f"API returned {response.status_code}: {response.text[:200]}"
                }
        except requests.RequestException as e:
            return {
                'success': False,
                'error': str(e)
            }


class CallLogsListView(APIView):
    """
    List all call logs for the authenticated consultant.
    Supports pagination and filtering.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        if user.role != User.CONSULTANT:
            return Response(
                {'error': 'Only consultants can view call logs'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get query params
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        client_id = request.query_params.get('client_id')
        status_filter = request.query_params.get('status')
        
        # Build queryset
        queryset = CallLog.objects.filter(caller=user).select_related('callee')
        
        if client_id:
            queryset = queryset.filter(callee_id=client_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        total = queryset.count()
        call_logs = queryset[offset:offset + limit]
        
        data = []
        for log in call_logs:
            data.append({
                'id': log.id,
                'client_id': log.callee.id,
                'client_name': log.callee.get_full_name() or log.callee.username,
                'status': log.status,
                'duration': log.duration,
                'duration_display': log.duration_display,
                'recording_url': log.recording_url,
                'outcome': log.outcome,
                'notes': log.notes,
                'follow_up_date': log.follow_up_date.isoformat() if log.follow_up_date else None,
                'created_at': log.created_at.isoformat(),
                'price': str(log.price) if log.price else None,
            })
        
        return Response({
            'total': total,
            'limit': limit,
            'offset': offset,
            'results': data
        })


class UpdateCallOutcomeView(APIView):
    """
    Update a call log with outcome, notes, and follow-up date.
    Called after a call ends from the frontend.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, call_id):
        user = request.user
        
        try:
            call_log = CallLog.objects.get(id=call_id, caller=user)
        except CallLog.DoesNotExist:
            return Response(
                {'error': 'Call log not found or not yours'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Update fields
        outcome = request.data.get('outcome')
        notes = request.data.get('notes')
        follow_up_date = request.data.get('follow_up_date')
        
        if outcome:
            call_log.outcome = outcome
        if notes:
            call_log.notes = notes
        if follow_up_date:
            try:
                call_log.follow_up_date = datetime.strptime(follow_up_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        
        call_log.save()
        
        return Response({
            'success': True,
            'message': 'Call outcome saved successfully',
            'id': call_log.id,
            'outcome': call_log.outcome,
            'notes': call_log.notes,
            'follow_up_date': call_log.follow_up_date.isoformat() if call_log.follow_up_date else None
        })


class RefreshCallDetailsView(APIView):
    """
    Manually fetch call details from Exotel API.
    Use this when callback didn't receive all data (recording, price, etc.)
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request, call_id):
        user = request.user
        
        if user.role != User.CONSULTANT:
            return Response(
                {'error': 'Only consultants can refresh call details'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            call_log = CallLog.objects.get(id=call_id, caller=user)
        except CallLog.DoesNotExist:
            return Response(
                {'error': 'Call log not found or not yours'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        if not call_log.exotel_sid:
            return Response(
                {'error': 'No Exotel SID available for this call'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Fetch from Exotel API
        try:
            call_data = self._fetch_call_details(call_log.exotel_sid)
            
            if call_data.get('success'):
                details = call_data.get('details', {})
                
                # Update call log with fetched data
                if details.get('Status'):
                    call_log.status = details['Status'].lower()
                
                if details.get('ConversationDuration'):
                    call_log.duration = int(details['ConversationDuration'])
                
                if details.get('RecordingUrl'):
                    call_log.recording_url = details['RecordingUrl']
                
                if details.get('Price'):
                    try:
                        call_log.price = float(details['Price'])
                    except:
                        pass
                
                if details.get('From'):
                    call_log.from_number = details['From']
                    
                if details.get('To'):
                    call_log.to_number = details['To']
                
                call_log.save()
                
                return Response({
                    'success': True,
                    'message': 'Call details refreshed from Exotel',
                    'status': call_log.status,
                    'duration': call_log.duration,
                    'recording_url': call_log.recording_url,
                    'price': str(call_log.price) if call_log.price else None
                })
            else:
                return Response({
                    'success': False,
                    'error': call_data.get('error', 'Failed to fetch from Exotel')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to refresh: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _fetch_call_details(self, call_sid):
        """Fetch call details from Exotel API."""
        import base64
        
        api_key = settings.EXOTEL_API_KEY
        api_token = settings.EXOTEL_API_TOKEN
        sid = settings.EXOTEL_SID
        subdomain = settings.EXOTEL_SUBDOMAIN
        
        url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/{call_sid}.json"
        
        auth_string = f"{api_key}:{api_token}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {
            'Authorization': f'Basic {auth_bytes}',
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            response_data = response.json()
            call_data = response_data.get('Call', {})
            return {
                'success': True,
                'details': call_data
            }
        else:
            return {
                'success': False,
                'error': f"Exotel API returned {response.status_code}"
            }


def _get_client_and_consultants(caller_phone_raw, logger=None):
    """
    Given a raw phone number from Exotel, find the client and their active consultants.
    Returns: (client, consultants_list)
    where consultants_list is a list of dicts: 
    [{'digit': '1', 'consultant': UserObj, 'services': ['GST', 'ITR']}, ...]
    """
    import logging
    import re
    from consultants.models import ClientServiceRequest
    
    if logger is None:
        logger = logging.getLogger('exotel_incoming')
        
    # Normalize phone number
    caller_phone = re.sub(r'[^\d]', '', caller_phone_raw)
    if caller_phone.startswith('91') and len(caller_phone) > 10:
        caller_phone = caller_phone[2:]
    elif caller_phone.startswith('0') and len(caller_phone) > 10:
        caller_phone = caller_phone[1:]
        
    phone_variants = [
        caller_phone,
        f"+91{caller_phone}",
        f"91{caller_phone}",
        f"0{caller_phone}",
    ]
    
    client = None
    for variant in phone_variants:
        client = User.objects.filter(phone_number=variant, role=User.CLIENT).first()
        if client:
            break
            
    if not client:
        client = User.objects.filter(phone_number__icontains=caller_phone, role=User.CLIENT).first()
        
    if not client:
        logger.info(f"Helper: No client found for phone variants {phone_variants}")
        return None, []
        
    logger.info(f"Helper: Found client {client.id} - {client.get_full_name()}")
    
    # Track unique consultants and their services
    consultants_map = {}
    
    # 1. Check primary assigned consultant from ClientProfile
    primary_consultant_user = None
    if hasattr(client, 'client_profile') and client.client_profile:
        primary_consultant_user = client.client_profile.assigned_consultant
        
    # 2. Add consultants from active ClientServiceRequests
    # We include 'pending' as well because the admin might have just assigned them
    # but the status hasn't moved to 'assigned' yet.
    active_statuses = list(ClientServiceRequest.ACTIVE_STATUSES) + ['pending']
    requests = ClientServiceRequest.objects.filter(
        client=client, 
        status__in=active_statuses,
        assigned_consultant__isnull=False
    ).select_related('assigned_consultant', 'assigned_consultant__user', 'service__category')
    
    for req in requests:
        consultant_user = req.assigned_consultant.user
        service_name = req.service.category.name if req.service.category else req.service.title
        
        if consultant_user.id not in consultants_map:
            consultants_map[consultant_user.id] = {
                'consultant': consultant_user,
                'services': [service_name]
            }
        else:
            if service_name not in consultants_map[consultant_user.id]['services']:
                consultants_map[consultant_user.id]['services'].append(service_name)
    
    # 3. Add primary consultant ONLY if they are NOT already in the map (working on a service)
    # OR if the map is entirely empty (preventing failure)
    if primary_consultant_user and primary_consultant_user.id not in consultants_map:
        consultants_map[primary_consultant_user.id] = {
            'consultant': primary_consultant_user,
            'services': ['General Advisory']
        }
                
    # Format list with digits (max 9)
    consultants_list = []
    digit = 1
    for cid, data in consultants_map.items():
        if digit > 9: break
        consultants_list.append({
            'digit': str(digit),
            'consultant': data['consultant'],
            'services': data['services']
        })
        digit += 1
        
    logger.info(f"Helper: Found {len(consultants_list)} active unique consultants for client")
    return client, consultants_list


class IncomingCallRouteView(APIView):
    """
    Endpoint for Exotel Connect Applet to determine incoming call routing.
    
    When a client calls the virtual number, Exotel calls this endpoint with
    the caller's phone number. We look up the client and return their 
    assigned consultant's phone number, or the sales team if unassigned.
    
    URL: https://main.taxplanadvisor.co/api/calls/incoming-route/
    
    Exotel sends: GET /?CallSid=xxx&From=+919876543210&To=02246183032
    Response: Plain text phone number (e.g., +919123456789)
    """
    permission_classes = []
    authentication_classes = []
    
    def get(self, request):
        import logging
        import os
        import re
        from django.http import HttpResponse
        
        # Set up logging
        log_dir = '/var/log/taxplanadvisor' if os.path.exists('/var/log/taxplanadvisor') else '/tmp'
        log_file = os.path.join(log_dir, 'exotel_incoming.log')
        
        logger = logging.getLogger('exotel_incoming')
        if not logger.handlers:
            handler = logging.FileHandler(log_file)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        
        # Get caller phone number from Exotel request
        caller_phone_raw = request.GET.get('From', '')
        call_sid = request.GET.get('CallSid', '')
        exophone = request.GET.get('To', '')
        digits = request.GET.get('digits', '').strip('"').strip() # Exotel might send "1" with quotes
        
        logger.info(f"=== INCOMING CALL ROUTING ===")
        logger.info(f"CallSid: {call_sid}")
        logger.info(f"From: {caller_phone_raw}")
        logger.info(f"To (ExoPhone): {exophone}")
        logger.info(f"Digits: {digits}")
        
        sales_team_phone = getattr(settings, 'SALES_TEAM_PHONE', '+916393645999')
        
        try:
            client, consultants_list = _get_client_and_consultants(caller_phone_raw, logger)
            
            if client and consultants_list:
                selected_consultant = None
                
                # If digits provided from IVR, find the matching consultant
                if digits:
                    for c_data in consultants_list:
                        if c_data['digit'] == digits:
                            selected_consultant = c_data['consultant']
                            break
                    if not selected_consultant:
                        logger.warning(f"Invalid digit '{digits}' pressed. Falling back to default.")
                        # Fallback to the first consultant if they pressed wrong key
                        selected_consultant = consultants_list[0]['consultant']
                else:
                    # Default: direct call (usually 1 consultant, or user skipped IVR)
                    selected_consultant = consultants_list[0]['consultant']
                
                if selected_consultant and getattr(selected_consultant, 'phone_number', None):
                    consultant_phone = selected_consultant.phone_number
                    if not consultant_phone.startswith('+'):
                        consultant_phone = f"+91{consultant_phone}"
                        
                    logger.info(f"Routing to assigned consultant: {selected_consultant.get_full_name()} - {consultant_phone}")
                    
                    # Create CallLog entry so the Passthru endpoint can update it later
                    if call_sid and not CallLog.objects.filter(exotel_sid=call_sid).exists():
                        try:
                            # Normalize the incoming phones for the CallLog DB format
                            from_phone = caller_phone_raw
                            to_phone = consultant_phone
                            if not from_phone.startswith('+') and len(from_phone) >= 10:
                                from_phone = f"+91{from_phone[-10:]}"
                                
                            CallLog.objects.create(
                                exotel_sid=call_sid,
                                caller=client,
                                callee=selected_consultant,
                                from_number=from_phone,
                                to_number=to_phone,
                                status='in-progress'
                            )
                            logger.info(f"Created initial CallLog for incoming call {call_sid}")
                        except Exception as create_e:
                            logger.error(f"Failed to create initial CallLog: {create_e}")
                            
                    return HttpResponse(consultant_phone, content_type='text/plain')
                else:
                    logger.warning(f"Selected consultant {selected_consultant} has no phone number, routing to sales team")
            else:
                logger.info(f"Client found but has no assigned consultant, routing to sales team" if client else f"No client found with phone {caller_phone_raw}, routing to sales team")
            
        except Exception as e:
            logger.error(f"Error determining routing: {e}", exc_info=True)
        
        # Fallback: Route to sales team
        logger.info(f"Routing to sales team: {sales_team_phone}")
        return HttpResponse(sales_team_phone, content_type='text/plain')


class IncomingCallPassthruView(APIView):
    """
    Endpoint for Exotel Passthru applet to send call end data for incoming calls.
    
    This should be placed AFTER the Connect applet in the Exotel flow.
    Exotel will call this endpoint with call details after the Connect applet completes.
    
    URL: https://main.taxplanadvisor.co/api/calls/incoming-passthru/
    
    Parameters received (GET):
    - CallSid: Unique call identifier
    - DialCallStatus: completed, busy, no-answer, failed, canceled
    - DialCallDuration: Duration in seconds
    - RecordingUrl: Recording URL if enabled
    - From: Caller phone
    - Legs[]: Array of leg details
    """
    permission_classes = []
    authentication_classes = []
    
    def get(self, request):
        import logging
        import os
        import re
        from django.http import HttpResponse
        
        # Set up logging
        log_dir = '/var/log/taxplanadvisor' if os.path.exists('/var/log/taxplanadvisor') else '/tmp'
        log_file = os.path.join(log_dir, 'exotel_incoming.log')
        
        logger = logging.getLogger('exotel_incoming_passthru')
        if not logger.handlers:
            handler = logging.FileHandler(log_file)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        
        # Get call details from Exotel
        call_sid = request.GET.get('CallSid', '')
        dial_status = request.GET.get('DialCallStatus', '')
        dial_duration = request.GET.get('DialCallDuration', '0')
        recording_url = request.GET.get('RecordingUrl', '')
        caller_phone = request.GET.get('From', '')
        dialed_number = request.GET.get('DialWhomNumber', '')
        
        logger.info(f"=== INCOMING CALL PASSTHRU (CALL END) ===")
        logger.info(f"CallSid: {call_sid}")
        logger.info(f"DialCallStatus: {dial_status}")
        logger.info(f"DialCallDuration: {dial_duration}")
        logger.info(f"RecordingUrl: {recording_url}")
        logger.info(f"From: {caller_phone}")
        logger.info(f"DialWhomNumber: {dialed_number}")
        
        # Log all query params for debugging
        logger.info(f"All params: {dict(request.GET)}")
        
        try:
            # Try to find the CallLog by exotel_sid
            call_log = CallLog.objects.filter(exotel_sid=call_sid).first()
            
            if call_log:
                # Update the call log with end data
                
                # Status mapping
                status_map = {
                    'completed': 'completed',
                    'busy': 'busy',
                    'no-answer': 'no-answer',
                    'failed': 'failed',
                    'canceled': 'canceled',
                }
                if dial_status:
                    call_log.status = status_map.get(dial_status.lower(), dial_status.lower())
                
                # Duration
                try:
                    call_log.duration = int(dial_duration) if dial_duration else 0
                except (ValueError, TypeError):
                    pass
                
                # Recording URL
                if recording_url:
                    call_log.recording_url = recording_url
                
                # End time
                call_log.end_time = timezone.now()
                
                call_log.save()
                logger.info(f"Updated CallLog {call_log.id}: status={call_log.status}, duration={call_log.duration}")
                
                # Try to fetch price from Call Details API
                try:
                    result = self._fetch_call_price(call_sid)
                    if result.get('success'):
                        price = result.get('price')
                        if price:
                            call_log.price = price
                            call_log.save()
                            logger.info(f"Fetched price: {price}")
                except Exception as e:
                    logger.warning(f"Could not fetch price: {e}")
                    
            else:
                logger.warning(f"No CallLog found for CallSid: {call_sid}")
                
        except Exception as e:
            logger.error(f"Error updating call log: {e}")
        
        # Return 200 OK for Passthru to continue to next applet
        return HttpResponse("OK", content_type='text/plain')
    
    def _fetch_call_price(self, call_sid):
        """Fetch price from Exotel Call Details API."""
        api_key = settings.EXOTEL_API_KEY
        api_token = settings.EXOTEL_API_TOKEN
        sid = settings.EXOTEL_SID
        subdomain = settings.EXOTEL_SUBDOMAIN
        
        url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/{call_sid}.json"
        
        auth_string = f"{api_key}:{api_token}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {'Authorization': f'Basic {auth_bytes}'}
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                response_data = response.json()
                call_data = response_data.get('Call', {})
                return {
                    'success': True,
                    'price': call_data.get('Price')
                }
            return {'success': False}
        except:
            return {'success': False}


class CheckConsultantCountView(APIView):
    """
    Passthru endpoint to check if a client has multiple active consultants.
    Returns HTTP 200 (OK) if 0 or 1 consultant.
    Returns HTTP 302 (Found) if > 1 consultant (triggering IVR flow).
    """
    permission_classes = []
    authentication_classes = []
    
    def get(self, request):
        import logging
        from django.http import HttpResponse, HttpResponseNotFound
        
        logger = logging.getLogger('exotel_incoming')
        caller_phone_raw = request.GET.get('From', '')
        
        try:
            client, consultants = _get_client_and_consultants(caller_phone_raw, logger)
            
            if len(consultants) > 1:
                logger.info(f"Client has {len(consultants)} consultants. Triggering IVR (404 Not Found to take other branch).")
                # Exotel Passthru considers 200 as Success branch, 3xx/4xx as Failure branch.
                # A 302 redirect causes Exotel to actually try and follow the redirect.
                # A 404 will correctly tell Exotel to execute the "If undefined/failure" applet (Gather).
                return HttpResponseNotFound("Multiple Consultants")
        except Exception as e:
            logger.error(f"Error checking consultant count: {e}", exc_info=True)
            
        logger.info(f"Client has 1 or 0 consultants. Skipping IVR (200 OK).")
        return HttpResponse("OK", content_type='text/plain')


class DynamicIVRView(APIView):
    """
    Programmable Gather endpoint. 
    Returns JSON specifying what audio to play ("Press 1 for X, Press 2 for Y").
    """
    permission_classes = []
    authentication_classes = []
    
    def get(self, request):
        import logging
        from django.http import JsonResponse
        
        logger = logging.getLogger('exotel_incoming')
        caller_phone_raw = request.GET.get('From', '')
        
        try:
            client, consultants = _get_client_and_consultants(caller_phone_raw, logger)
            
            if len(consultants) > 1:
                # Build the text prompt
                prompt_parts = []
                for c_data in consultants:
                    digit = c_data['digit']
                    name = c_data['consultant'].get_full_name() or c_data['consultant'].username
                    # Extract first name to sound better
                    first_name = name.split()[0]
                    services = " and ".join(c_data['services'][:2])
                    prompt_parts.append(f"For {services} with {first_name} press {digit}.")
                
                full_prompt = "Welcome to Tax Plan Advisor. " + " ".join(prompt_parts)
                logger.info(f"Generated IVR prompt: {full_prompt}")
                
                return JsonResponse({
                    "gather_prompt": {
                        "text": full_prompt
                    },
                    "max_input_digits": "1",
                    "input_timeout": "5",
                    "repeat_menu": "2",
                    "repeat_gather_prompt": {
                        "text": full_prompt
                    }
                })
        except Exception as e:
            logger.error(f"Error generating dynamic IVR: {e}", exc_info=True)
            
        # Fallback if error or only 1 consultant ended up here
        return JsonResponse({
            "gather_prompt": {
                "text": "Please wait while we connect your call."
            },
            "max_input_digits": "1",
            "input_timeout": "5",
            "repeat_menu": "2",
            "repeat_gather_prompt": {
                "text": "Please wait while we connect your call."
            }
        })
