import os
import datetime
import logging
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from django.conf import settings

logger = logging.getLogger('consultations')

class GoogleMeetService:
    def __init__(self):
        self.scopes = ['https://www.googleapis.com/auth/calendar.events']
        self.credentials = self._get_credentials()
        self.service = build('calendar', 'v3', credentials=self.credentials)

    def _get_credentials(self):
        """
        Loads credentials from settings/environment.
        """
        try:
            creds = Credentials(
                token=None,  # Will be refreshed automatically
                refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                scopes=self.scopes
            )
            
            # Refresh the access token if it's expired
            if not creds.valid:
                creds.refresh(Request())
                
            return creds
        except Exception as e:
            logger.error(f"Failed to initialize Google credentials: {str(e)}")
            raise

    def create_meeting(self, booking):
        """
        Creates a Google Calendar event with a Google Meet link.
        """
        try:
            # Combine date and time for ISO format
            start_dt = datetime.datetime.combine(booking.booking_date, booking.start_time)
            end_dt = datetime.datetime.combine(booking.booking_date, booking.end_time)
            
            event = {
                'summary': f'Tax Consultancy: {booking.topic.name}',
                'description': f'Consultation between {booking.consultant.get_full_name()} and {booking.client.get_full_name()}.\nNotes: {booking.notes}',
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'conferenceData': {
                    'createRequest': {
                        'requestId': f"booking-{booking.id}-{datetime.datetime.now().timestamp()}",
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                    }
                },
                'attendees': [
                    {'email': booking.consultant.email},
                    {'email': booking.client.email},
                ],
                'guestsCanModify': True,
                'guestsCanInviteOthers': True,
                'guestsCanSeeOtherGuests': True,
                'visibility': 'public',  # Helps with access if attendees are outside org
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 24 * 60},
                        {'method': 'popup', 'minutes': 10},
                    ],
                },
            }

            event = self.service.events().insert(
                calendarId='primary',
                body=event,
                conferenceDataVersion=1,
                sendUpdates='all'
            ).execute()
            
            # Extract the Meet link
            meet_link = event.get('hangoutLink')
            logger.info(f"Successfully generated Google Meet link: {meet_link} for booking {booking.id}")
            return meet_link
        except Exception as e:
            logger.error(f"Error creating Google Meet for booking {booking.id}: {str(e)}", exc_info=True)
            return None
