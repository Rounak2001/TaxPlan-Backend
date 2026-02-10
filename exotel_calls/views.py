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
    Webhook endpoint for Exotel to send call status updates.
    Saves all available data from Exotel callback.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        """
        Handle Exotel StatusCallback webhook.
        Exotel sends: Sid, Status, From, To, RecordingUrl, ConversationDuration, 
                      StartTime, EndTime, Price, CustomField, Legs[], etc.
        """
        import json
        import logging
        
        # Set up file logging for debugging
        logger = logging.getLogger('exotel_callback')
        if not logger.handlers:
            handler = logging.FileHandler('/tmp/exotel_callbacks.log')
            handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        
        # Get data from request (supports both JSON and form-data)
        if request.content_type == 'application/json':
            data = request.data
        else:
            data = request.POST.dict() if hasattr(request, 'POST') else request.data
        
        # Log full callback for debugging
        logger.info(f"Exotel Callback: {json.dumps(data, indent=2, default=str)}")
        print(f"Exotel Callback Data: {data}")
        
        call_log_id = data.get('CustomField')
        if not call_log_id:
            logger.warning("No CustomField in callback, ignoring")
            return Response({'status': 'ignored'}, status=status.HTTP_200_OK)
        
        try:
            call_log = CallLog.objects.get(id=call_log_id)
            
            # Update Exotel SID if present
            exotel_sid = data.get('Sid') or data.get('CallSid')
            if exotel_sid:
                call_log.exotel_sid = exotel_sid
            
            # Update status
            exotel_status = data.get('Status', '').lower()
            status_mapping = {
                'completed': 'completed',
                'busy': 'busy',
                'failed': 'failed',
                'no-answer': 'no-answer',
                'canceled': 'canceled',
                'in-progress': 'in-progress',
            }
            call_log.status = status_mapping.get(exotel_status, exotel_status)
            
            # Duration - check multiple possible field names
            duration = (
                data.get('ConversationDuration') or 
                data.get('Duration') or 
                data.get('CallDuration') or 
                0
            )
            call_log.duration = int(duration) if duration else 0
            
            # Recording URL - check multiple possible field names and formats
            recording_url = (
                data.get('RecordingUrl') or 
                data.get('RecordingURL') or 
                data.get('Recording') or 
                data.get('Recordings')
            )
            if recording_url:
                # Handle if it's a list
                if isinstance(recording_url, list) and len(recording_url) > 0:
                    recording_url = recording_url[0]
                call_log.recording_url = str(recording_url)
                logger.info(f"Recording URL saved: {recording_url}")
            
            # Price - check multiple possible field names
            price = data.get('Price') or data.get('CallPrice') or data.get('Cost')
            if price:
                try:
                    call_log.price = float(price)
                except (ValueError, TypeError):
                    pass
            
            # Store full numbers (admin can see)
            from_num = data.get('From') or data.get('DialWhomNumber')
            to_num = data.get('To') or data.get('CallTo')
            if from_num:
                call_log.from_number = from_num
            if to_num:
                call_log.to_number = to_num
            
            # Timestamps - try multiple formats
            start_time = data.get('StartTime') or data.get('DateCreated')
            end_time = data.get('EndTime') or data.get('DateUpdated')
            
            time_formats = [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%d',
            ]
            
            if start_time:
                for fmt in time_formats:
                    try:
                        parsed_time = datetime.strptime(str(start_time), fmt)
                        call_log.start_time = timezone.make_aware(parsed_time)
                        break
                    except ValueError:
                        continue
                        
            if end_time:
                for fmt in time_formats:
                    try:
                        parsed_time = datetime.strptime(str(end_time), fmt)
                        call_log.end_time = timezone.make_aware(parsed_time)
                        break
                    except ValueError:
                        continue
            
            call_log.save()
            logger.info(f"CallLog {call_log_id} updated: status={call_log.status}, duration={call_log.duration}, recording={call_log.recording_url}")
            return Response({'status': 'updated'}, status=status.HTTP_200_OK)
            
        except CallLog.DoesNotExist:
            logger.error(f"CallLog {call_log_id} not found")
            return Response({'status': 'not_found'}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error processing callback: {e}")
            print(f"Error processing callback: {e}")
            return Response({'status': 'error'}, status=status.HTTP_200_OK)


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
