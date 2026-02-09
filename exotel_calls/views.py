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
        
        # Security: Verify this client is assigned to the requesting consultant
        try:
            client_profile = ClientProfile.objects.select_related('user').get(
                user_id=client_id,
                assigned_consultant=user
            )
            client_user = client_profile.user
        except ClientProfile.DoesNotExist:
            return Response(
                {'error': 'Client not found or not assigned to you'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
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
        
        logger.info(f"=== INCOMING CALL ROUTING ===")
        logger.info(f"CallSid: {call_sid}")
        logger.info(f"From: {caller_phone_raw}")
        logger.info(f"To (ExoPhone): {exophone}")
        
        # Normalize phone number - extract just the 10 digit number
        caller_phone = re.sub(r'[^\d]', '', caller_phone_raw)  # Remove non-digits
        if caller_phone.startswith('91') and len(caller_phone) > 10:
            caller_phone = caller_phone[2:]  # Remove 91 prefix
        if caller_phone.startswith('0') and len(caller_phone) > 10:
            caller_phone = caller_phone[1:]  # Remove leading 0
        
        # Now caller_phone should be 10 digits like "9975111931"
        logger.info(f"Normalized caller phone: {caller_phone}")
        
        # Sales team fallback number
        sales_team_phone = getattr(settings, 'SALES_TEAM_PHONE', '+916393645999')
        
        # Try to find the client by phone number
        try:
            # Build all possible phone formats to match
            phone_variants = [
                caller_phone,              # 9975111931
                f"+91{caller_phone}",      # +919975111931
                f"91{caller_phone}",       # 919975111931
                f"0{caller_phone}",        # 09975111931
            ]
            logger.info(f"Trying phone variants: {phone_variants}")
            
            client = None
            for variant in phone_variants:
                client = User.objects.filter(phone_number=variant, role=User.CLIENT).first()
                if client:
                    logger.info(f"Matched with variant: {variant}")
                    break
            
            # Also try icontains as fallback (matches partial)
            if not client:
                client = User.objects.filter(
                    phone_number__icontains=caller_phone,
                    role=User.CLIENT
                ).first()
                if client:
                    logger.info(f"Matched with icontains")
            
            if client:
                logger.info(f"Found client: {client.id} - {client.get_full_name()}")
                
                # Check if client has an assigned consultant
                if hasattr(client, 'client_profile') and client.client_profile:
                    consultant = client.client_profile.assigned_consultant
                    
                    if consultant and consultant.phone_number:
                        # Format consultant phone for Exotel
                        consultant_phone = consultant.phone_number
                        if not consultant_phone.startswith('+'):
                            consultant_phone = f"+91{consultant_phone}"
                        
                        logger.info(f"Routing to assigned consultant: {consultant.get_full_name()} - {consultant_phone}")
                        
                        # Create incoming call log
                        try:
                            CallLog.objects.create(
                                caller=consultant,  # Consultant will receive
                                callee=client,      # Client is calling
                                status='initiated',
                                exotel_sid=call_sid,
                                from_number=caller_phone_raw,
                                to_number=exophone,
                                notes=f"Incoming call routed to consultant"
                            )
                        except Exception as e:
                            logger.warning(f"Could not create call log: {e}")
                        
                        return HttpResponse(consultant_phone, content_type='text/plain')
                    else:
                        logger.info(f"Consultant has no phone number, routing to sales team")
                else:
                    logger.info(f"Client has no assigned consultant, routing to sales team")
            else:
                logger.info(f"No client found with phone {caller_phone}, routing to sales team")
            
        except Exception as e:
            logger.error(f"Error looking up client: {e}")
        
        # Fallback: Route to sales team
        logger.info(f"Routing to sales team: {sales_team_phone}")
        return HttpResponse(sales_team_phone, content_type='text/plain')
