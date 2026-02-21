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
    cal.add('method', 'REQUEST')
    
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
    if booking.confirmation_sent:
        return False
        
    try:
        # Generate Google Calendar URL for "Add to Calendar" button
        import urllib.parse
        ist = pytz.timezone('Asia/Kolkata')
        start_dt = ist.localize(datetime.combine(booking.booking_date, booking.start_time))
        end_dt = ist.localize(datetime.combine(booking.booking_date, booking.end_time))
        
        # Convert to UTC for the Google Calendar URL (Z format)
        start_utc = start_dt.astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
        end_utc = end_dt.astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
        
        cal_base_url = "https://www.google.com/calendar/render?action=TEMPLATE"
        cal_text = urllib.parse.quote(f"Tax Consultation: {booking.topic.name}")
        cal_details = urllib.parse.quote(
            f"Consultation with {booking.consultant.get_full_name() or booking.consultant.username}.\n"
            f"Meeting Link: {booking.meeting_link or 'Will be shared shortly'}"
        )
        google_calendar_url = f"{cal_base_url}&text={cal_text}&dates={start_utc}/{end_utc}&details={cal_details}"

        # Common context for both emails
        context = {
            'booking': booking,
            'client_name': booking.client.get_full_name() or booking.client.username,
            'consultant_name': booking.consultant.get_full_name() or booking.consultant.username,
            'dashboard_url': f"{settings.FRONTEND_URL}/dashboard",
            'meeting_link': booking.meeting_link,
            'google_calendar_url': google_calendar_url,
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
        
        # Attach uploaded files to client email
        for attachment in booking.attachments.all():
            try:
                email_client.attach_file(attachment.file.path)
            except Exception as e:
                logger.error(f"Failed to attach file {attachment.id} to client email: {str(e)}")

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
        
        # Attach uploaded files to consultant email
        for attachment in booking.attachments.all():
            try:
                email_consultant.attach_file(attachment.file.path)
            except Exception as e:
                logger.error(f"Failed to attach file {attachment.id} to consultant email: {str(e)}")

        email_consultant.send()
        
        logger.info(f"Confirmation email sent to consultant: {booking.consultant.email}")
        
        # Update confirmation_sent flag
        booking.confirmation_sent = True
        booking.save(update_fields=['confirmation_sent'])
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send booking confirmation email: {str(e)}", exc_info=True)


def send_booking_reschedule(booking):
    """
    Send booking reschedule email to both client and consultant.
    """
    try:
        remaining_reschedules = max(0, 3 - booking.reschedule_count)
        
        context = {
            'booking': booking,
            'remaining_reschedules': remaining_reschedules,
            'client_name': booking.client.get_full_name() or booking.client.username,
            'consultant_name': booking.consultant.get_full_name() or booking.consultant.username,
        }
        
        # 1. Email to Client
        client_subject = f"Rescheduled: Consultation with {booking.consultant.get_full_name() or booking.consultant.username}"
        client_html = render_to_string('emails/booking_reschedule.html', context | {'recipient_type': 'client'})
        client_text = render_to_string('emails/booking_reschedule.txt', context | {'recipient_type': 'client'})
        
        client_msg = EmailMultiAlternatives(
            subject=client_subject,
            body=client_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.client.email]
        )
        client_msg.attach_alternative(client_html, "text/html")
        
        # Generate and attach the updated ICS calendar file
        ics_data = generate_ics_calendar(booking)
        if ics_data:
            client_msg.attach('rescheduled_consultation.ics', ics_data, 'text/calendar')
            
        client_msg.send(fail_silently=True)
        
        # 2. Email to Consultant
        consultant_subject = f"Rescheduled: Consultation with {booking.client.get_full_name() or booking.client.username}"
        consultant_html = render_to_string('emails/booking_reschedule.html', context | {'recipient_type': 'consultant'})
        consultant_text = render_to_string('emails/booking_reschedule.txt', context | {'recipient_type': 'consultant'})
        
        consultant_msg = EmailMultiAlternatives(
            subject=consultant_subject,
            body=consultant_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.consultant.email]
        )
        consultant_msg.attach_alternative(consultant_html, "text/html")
        
        if ics_data:
            consultant_msg.attach('rescheduled_consultation.ics', ics_data, 'text/calendar')
            
        # Attach uploaded files to consultant email
        for attachment in booking.attachments.all():
            try:
                consultant_msg.attach_file(attachment.file.path)
            except Exception as e:
                logger.error(f"Failed to attach file {attachment.id} to consultant reschedule email: {str(e)}")
            
        consultant_msg.send(fail_silently=True)
        
        logger.info(f"Reschedule emails sent for booking {booking.id}")
        
    except Exception as e:
        logger.error(f"Failed to send booking reschedule email: {str(e)}", exc_info=True)


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
