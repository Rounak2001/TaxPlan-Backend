import logging
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from icalendar import Calendar, Event
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)


def generate_ics_calendar(booking):
    """
    Generate iCalendar (.ics) file for the booking.
    """
    cal = Calendar()
    cal.add('prodid', '-//TaxPlanAdv//Consultation//EN')
    cal.add('version', '2.0')
    
    event = Event()
    event.add('uid', f'booking-{booking.id}@taxplanadv.com')
    
    # Combine date and time for the event
    start_datetime = datetime.combine(booking.booking_date, booking.start_time)
    end_datetime = datetime.combine(booking.booking_date, booking.end_time)
    
    # Add timezone (IST)
    ist = pytz.timezone('Asia/Kolkata')
    start_datetime = ist.localize(start_datetime)
    end_datetime = ist.localize(end_datetime)
    
    event.add('dtstart', start_datetime)
    event.add('dtend', end_datetime)
    event.add('summary', f'{booking.topic.name} with {booking.consultant.get_full_name() or booking.consultant.username}')
    
    description = f'Topic: {booking.topic.name}\n'
    if booking.notes:
        description += f'Notes: {booking.notes}\n'
    description += f'\nConsultant: {booking.consultant.get_full_name() or booking.consultant.username}'
    event.add('description', description)
    
    event.add('location', 'Online Video Consultation')
    event.add('organizer', f'mailto:{booking.consultant.email}')
    event.add('attendee', f'mailto:{booking.client.email}')
    event.add('status', 'CONFIRMED')
    
    cal.add_component(event)
    return cal.to_ical()


def send_booking_confirmation(booking):
    """
    Send booking confirmation email to both client and consultant.
    """
    try:
        # Common context for both emails
        context = {
            'booking': booking,
            'client_name': booking.client.get_full_name() or booking.client.username,
            'consultant_name': booking.consultant.get_full_name() or booking.consultant.username,
        }
        
        # Generate calendar file
        ics_content = generate_ics_calendar(booking)
        
        # Email to CLIENT
        subject_client = f'Booking Confirmed: {booking.topic.name}'
        html_content_client = render_to_string('emails/booking_confirmation_client.html', context)
        text_content_client = render_to_string('emails/booking_confirmation_client.txt', context)
        
        email_client = EmailMultiAlternatives(
            subject=subject_client,
            body=text_content_client,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.client.email]
        )
        email_client.attach_alternative(html_content_client, "text/html")
        email_client.attach('meeting.ics', ics_content, 'text/calendar')
        email_client.send()
        
        logger.info(f"Confirmation email sent to client: {booking.client.email}")
        
        # Email to CONSULTANT
        subject_consultant = f'New Booking: {booking.topic.name} with {context["client_name"]}'
        html_content_consultant = render_to_string('emails/booking_confirmation_consultant.html', context)
        text_content_consultant = render_to_string('emails/booking_confirmation_consultant.txt', context)
        
        email_consultant = EmailMultiAlternatives(
            subject=subject_consultant,
            body=text_content_consultant,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.consultant.email]
        )
        email_consultant.attach_alternative(html_content_consultant, "text/html")
        email_consultant.attach('meeting.ics', ics_content, 'text/calendar')
        email_consultant.send()
        
        logger.info(f"Confirmation email sent to consultant: {booking.consultant.email}")
        
        # Update confirmation_sent flag
        booking.confirmation_sent = True
        booking.save(update_fields=['confirmation_sent'])
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send booking confirmation for booking {booking.id}: {str(e)}")
        return False


def send_booking_reminder(booking):
    """
    Send 24-hour reminder email to both client and consultant.
    """
    try:
        context = {
            'booking': booking,
            'client_name': booking.client.get_full_name() or booking.client.username,
            'consultant_name': booking.consultant.get_full_name() or booking.consultant.username,
        }
        
        # Email to CLIENT
        subject_client = f'Reminder: Meeting Tomorrow at {booking.start_time.strftime("%I:%M %p")}'
        html_content_client = render_to_string('emails/booking_reminder_client.html', context)
        text_content_client = render_to_string('emails/booking_reminder_client.txt', context)
        
        email_client = EmailMultiAlternatives(
            subject=subject_client,
            body=text_content_client,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.client.email]
        )
        email_client.attach_alternative(html_content_client, "text/html")
        email_client.send()
        
        logger.info(f"Reminder email sent to client: {booking.client.email}")
        
        # Email to CONSULTANT
        subject_consultant = f'Reminder: Meeting Tomorrow with {context["client_name"]}'
        html_content_consultant = render_to_string('emails/booking_reminder_consultant.html', context)
        text_content_consultant = render_to_string('emails/booking_reminder_consultant.txt', context)
        
        email_consultant = EmailMultiAlternatives(
            subject=subject_consultant,
            body=text_content_consultant,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.consultant.email]
        )
        email_consultant.attach_alternative(html_content_consultant, "text/html")
        email_consultant.send()
        
        logger.info(f"Reminder email sent to consultant: {booking.consultant.email}")
        
        # Update reminder_sent flag
        booking.reminder_sent = True
        booking.save(update_fields=['reminder_sent'])
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send reminder for booking {booking.id}: {str(e)}")
        return False


def send_booking_cancellation(booking):
    """
    Send cancellation notification email to both parties.
    """
    try:
        context = {
            'booking': booking,
            'client_name': booking.client.get_full_name() or booking.client.username,
            'consultant_name': booking.consultant.get_full_name() or booking.consultant.username,
        }
        
        # Email to CLIENT
        subject_client = f'Booking Cancelled: {booking.topic.name}'
        html_content_client = render_to_string('emails/booking_cancellation.html', context | {'recipient_type': 'client'})
        text_content_client = render_to_string('emails/booking_cancellation.txt', context | {'recipient_type': 'client'})
        
        email_client = EmailMultiAlternatives(
            subject=subject_client,
            body=text_content_client,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.client.email]
        )
        email_client.attach_alternative(html_content_client, "text/html")
        email_client.send()
        
        logger.info(f"Cancellation email sent to client: {booking.client.email}")
        
        # Email to CONSULTANT
        subject_consultant = f'Booking Cancelled: {booking.topic.name} with {context["client_name"]}'
        html_content_consultant = render_to_string('emails/booking_cancellation.html', context | {'recipient_type': 'consultant'})
        text_content_consultant = render_to_string('emails/booking_cancellation.txt', context | {'recipient_type': 'consultant'})
        
        email_consultant = EmailMultiAlternatives(
            subject=subject_consultant,
            body=text_content_consultant,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.consultant.email]
        )
        email_consultant.attach_alternative(html_content_consultant, "text/html")
        email_consultant.send()
        
        logger.info(f"Cancellation email sent to consultant: {booking.consultant.email}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send cancellation email for booking {booking.id}: {str(e)}")
        return False
