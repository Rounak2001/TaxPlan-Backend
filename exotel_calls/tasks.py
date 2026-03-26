import logging
import requests
import base64
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.db import OperationalError
from .models import ScheduledCall, CallLog

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),  # Neon cold-start OperationalError
    max_retries=3,
    retry_backoff=30,   # 30s, 60s, 120s
    retry_backoff_max=120,
    retry_jitter=False,
)
def process_scheduled_calls(self):
    """
    Query ScheduledCall records that are pending and run_at <= now()
    Make Exotel API calls to connect them to the specific applet.
    """
    now = timezone.now()
    # Get all pending calls that are due to be run
    calls_to_make = ScheduledCall.objects.filter(
        status='pending',
        run_at__lte=now
    ).select_related('booking', 'booking__client', 'booking__consultant')

    for scheduled_call in calls_to_make:
        booking = scheduled_call.booking
        client = booking.client
        client_phone = client.phone_number
        
        if not client_phone:
            scheduled_call.status = 'failed'
            scheduled_call.error_message = 'Client phone number missing'
            scheduled_call.save()
            continue
            
        # Ensure proper number format
        if not client_phone.startswith('+'):
            if len(client_phone) == 10:
                client_phone = f"+91{client_phone}"
            else:
                client_phone = f"+{client_phone}"

        # Connect to Exotel Applet
        api_key = settings.EXOTEL_API_KEY
        api_token = settings.EXOTEL_API_TOKEN
        sid = settings.EXOTEL_SID
        caller_id = settings.EXOTEL_CALLER_ID
        subdomain = settings.EXOTEL_SUBDOMAIN
        
        # Predict Applet ID based on the time difference from booking start
        booking_datetime = timezone.make_aware(
            timezone.datetime.combine(booking.booking_date, booking.start_time)
        )
        time_diff_minutes = (booking_datetime - scheduled_call.run_at).total_seconds() / 60
        
        if time_diff_minutes < 5:
            # At the time of consultation (0 mins)
            app_id = "1212575"
        elif time_diff_minutes <= 20:
            # 15 minutes before
            app_id = "1212552"
        else:
            # 1 hour before
            app_id = "1201422"
        # The URL for connecting to a flow
        applet_url = f"http://my.exotel.com/{sid}/exoml/start_voice/{app_id}"
        
        connect_url = f"https://{subdomain}/v1/Accounts/{sid}/Calls/connect.json"
        
        auth_string = f"{api_key}:{api_token}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        headers = {
            'Authorization': f'Basic {auth_bytes}',
        }
        
        # We need to map to CallLog to keep history and track callbacks if desired
        call_log = CallLog.objects.create(
            caller=booking.consultant,  # System is making it on behalf of the consultant
            callee=client,
            status='initiated',
            notes=f"Automated consultation reminder for booking {booking.id}"
        )
        
        data = {
            'From': client_phone,  # We call the client
            'CallerId': caller_id, # Our exo phone
            'Url': applet_url,     # Connect to applet
            'StatusCallback': f"{settings.BACKEND_URL}/api/calls/status-callback/",
            'StatusCallbackContentType': 'application/json',
            'CustomField': str(call_log.id), # Pass call log so callback logs it
        }
        
        try:
            response = requests.post(connect_url, headers=headers, data=data, timeout=30)
            
            if response.status_code == 200:
                response_data = response.json()
                exotel_sid = response_data.get('Call', {}).get('Sid')
                
                # Update ScheduledCall
                scheduled_call.status = 'completed'
                scheduled_call.exotel_sid = exotel_sid
                scheduled_call.save()
                
                # Update CallLog
                call_log.exotel_sid = exotel_sid
                call_log.status = 'queued'
                call_log.save()

                # --- NEW: Trigger WhatsApp Reminder ---
                # This template is sent as a reminder before/at the meeting.
                if client_phone:
                    from notifications.tasks import send_whatsapp_template_task
                    from urllib.parse import urlparse
                    
                    # Variables for Template: [Client Name, Consultant Name, Date, Time, Meeting Code]
                    client_name = client.first_name or client.username
                    consultant_name = booking.consultant.get_full_name() or booking.consultant.username
                    booking_date = booking.booking_date.strftime('%d %b %Y')
                    start_time = booking.start_time.strftime('%I:%M %p')
                    
                    # Extract meeting code from link (e.g. 'abc-defg-hij' from https://meet.google.com/abc-defg-hij)
                    # This code is used as the dynamic suffix for the "Join Meeting" button
                    meeting_code = ""
                    if booking.meeting_link:
                        path = urlparse(booking.meeting_link).path
                        meeting_code = path.strip('/')
                    
                    send_whatsapp_template_task.delay(
                        phone_number=client_phone,
                        template_name="consultation_reminder_final",
                        variables=[
                            client_name,
                            consultant_name,
                            booking_date,
                            start_time,
                            meeting_code
                        ]
                    )
                    logger.info(f"Queued WhatsApp consultation reminder for client {client.id} (Booking {booking.id})")
                
            else:
                scheduled_call.status = 'failed'
                scheduled_call.error_message = f"Exotel API {response.status_code}: {response.text}"
                scheduled_call.save()
                
                call_log.status = 'failed'
                call_log.notes += f"\nFailed: {response.status_code}"
                call_log.save()
                
        except Exception as e:
            scheduled_call.status = 'failed'
            scheduled_call.error_message = str(e)
            scheduled_call.save()
            
            call_log.status = 'failed'
            call_log.notes += f"\nException: {str(e)}"
            call_log.save()
