from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.contrib.auth import get_user_model
from datetime import datetime, time, timedelta
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking
from .emails import send_booking_confirmation
from .serializers import (
    TopicSerializer, WeeklyAvailabilitySerializer, DateOverrideSerializer,
    ConsultationBookingSerializer
)
from .utils import trigger_recording_bot
from .google_meet import GoogleMeetService
import razorpay
from django.conf import settings
from django.db import transaction
import logging

User = get_user_model()

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

logger = logging.getLogger('consultations')


class WeeklyAvailabilityViewSet(viewsets.ModelViewSet):
    serializer_class = WeeklyAvailabilitySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return WeeklyAvailability.objects.filter(consultant=self.request.user)

class DateOverrideViewSet(viewsets.ModelViewSet):
    serializer_class = DateOverrideSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DateOverride.objects.filter(consultant=self.request.user)

class TopicViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Topic.objects.all()
    serializer_class = TopicSerializer
    permission_classes = [permissions.IsAuthenticated]

import threading

def process_booking_confirmation(booking):
    """
    Background task to generate Google Meet link and send confirmation email.
    """
    try:
        # Generate Google Meet link
        if not booking.meeting_link:
            try:
                service = GoogleMeetService()
                meet_link = service.create_meeting(booking)
                if meet_link:
                    booking.meeting_link = meet_link
                    booking.save(update_fields=['meeting_link'])
            except Exception as meet_err:
                logger.error(f"Failed to generate Google Meet link: {meet_err}")

        # Send confirmation email
        if not booking.confirmation_sent:
            send_booking_confirmation(booking)
            
    except Exception as e:
        logger.error(f"Error in background booking processing: {e}")

class ConsultationBookingViewSet(viewsets.GenericViewSet, 
                                 viewsets.mixins.CreateModelMixin,
                                 viewsets.mixins.ListModelMixin,
                                 viewsets.mixins.RetrieveModelMixin):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = ConsultationBookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'CONSULTANT':
            return ConsultationBooking.objects.filter(consultant=user)
        return ConsultationBooking.objects.filter(client=user)

    def create(self, request, *args, **kwargs):
        logger.debug(f"ConsultationBookingViewSet.create called by {request.user.email}")
        response = super().create(request, *args, **kwargs)
        if response.status_code == status.HTTP_201_CREATED:
            # Add Razorpay Key ID for the frontend to initialize checkout
            response.data['razorpay_key_id'] = settings.RAZORPAY_KEY_ID
        return response

    def perform_create(self, serializer):
        # Default status is 'pending' from model
        booking = serializer.save()
        
        # Calculate amount from consultant's service profile
        try:
            booking.amount = booking.consultant.consultant_service_profile.consultation_fee
        except Exception:
            booking.amount = 1.00 # Fallback
        booking.save(update_fields=['amount'])
        
        # Create Razorpay Order
        try:
            # Razorpay amount is in paise
            razor_amount = int(booking.amount * 100)
            order_data = {
                'amount': razor_amount,
                'currency': 'INR',
                'receipt': f"receipt_booking_{booking.id}",
                'payment_capture': 1 # Auto-capture
            }
            razorpay_order = razorpay_client.order.create(data=order_data)
            booking.razorpay_order_id = razorpay_order['id']
            booking.save(update_fields=['razorpay_order_id'])
        except Exception as e:
            logger.error(f"Failed to create Razorpay order: {str(e)}", exc_info=True)
            # We still keep the booking as pending, but it won't have an order ID for checkout

    @action(detail=True, methods=['post'])
    def verify_payment(self, request, pk=None):
        booking = self.get_object()
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_signature = request.data.get('razorpay_signature')

        params_dict = {
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        }

        # 1. Atomic Transaction to prevent race conditions with Webhook
        try:
            with transaction.atomic():
                # Lock the booking row
                booking = ConsultationBooking.objects.select_for_update().get(pk=booking.pk)
                
                # Check if Webhook already processed this
                if booking.payment_status == 'paid':
                    return Response({'status': 'Payment already verified (via webhook)', 'booking_id': booking.id})

                # 2. Verify signature
                try:
                    razorpay_client.utility.verify_payment_signature(params_dict)
                except Exception as sig_err:
                    logger.error(f"Signature verification failed: {str(sig_err)}")
                    # INDUSTRY STANDARD: Do NOT mark as 'failed' here.  
                    # The frontend check might fail for many reasons (network, duplicate, etc.)
                    # We only mark as failed if we receive an explicit 'payment.failed' webhook.
                    return Response({'error': 'Payment verification failed'}, status=status.HTTP_400_BAD_REQUEST)

                # 3. Update status
                booking.payment_status = 'paid'
                booking.status = 'confirmed'
                booking.razorpay_payment_id = razorpay_payment_id
                booking.razorpay_signature = razorpay_signature
                booking.save()

            # Start background task for Meet link and Emails
            threading.Thread(target=process_booking_confirmation, args=(booking,)).start()
            
            return Response({'status': 'Payment verified and booking confirmed'})

        except Exception as e:
            # 3. SAFETY NET: Payment was verified but DB save failed.
            logger.critical(f"CRITICAL: Payment verified but booking update failed: {str(e)}", exc_info=True)
            try:
                booking.refresh_from_db()
                booking.payment_status = 'failed'
                booking.razorpay_payment_id = razorpay_payment_id
                booking.razorpay_signature = razorpay_signature
                booking.save()
            except Exception as save_err:
                logger.critical(f"Double Fault: Could not save error state: {str(save_err)}", exc_info=True)

            return Response({
                'error': 'Payment received but booking update failed. Please contact support.',
                'payment_id': razorpay_payment_id
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def trigger_recording(self, request, pk=None):
        booking = self.get_object()
        if not booking.meeting_link:
            return Response({'error': 'No meeting link found for this booking'}, status=status.HTTP_400_BAD_REQUEST)
        
        success = trigger_recording_bot(booking.meeting_link)
        if success:
            return Response({'status': 'Recording bot triggered successfully'})
        else:
            return Response({'error': 'Failed to trigger recording bot'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def consultants_by_date(request):
    """
    Get consultants available on a specific date (optionally filtered by topic).
    Query params: date (YYYY-MM-DD), topic_id (optional)
    """
    booking_date = request.query_params.get('date')
    topic_id = request.query_params.get('topic_id')

    if not booking_date:
        return Response(
            {'error': 'date is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()
    except ValueError:
        return Response(
            {'error': 'Invalid date format. Use YYYY-MM-DD'},
            status=status.HTTP_400_BAD_REQUEST
        )

    day_of_week = date_obj.weekday()
    if day_of_week == 6:  # Sunday
        day_of_week = 0
    else:
        day_of_week += 1

    # Get all consultants
    if topic_id:
        consultants = User.objects.filter(role='CONSULTANT', topics__id=topic_id)
    else:
        consultants = User.objects.filter(role='CONSULTANT')
    available_consultants = []

    for consultant in consultants:
        # Check if consultant has any availability on this date
        has_availability = False
        
        # Check date override first
        override = DateOverride.objects.filter(
            consultant=consultant,
            date=date_obj
        ).first()

        if override:
            if not override.is_unavailable and override.start_time and override.end_time:
                has_availability = True
        else:
            # Check weekly availability
            weekly_slots = WeeklyAvailability.objects.filter(
                consultant=consultant,
                day_of_week=day_of_week
            ).exists()
            
            if weekly_slots:
                has_availability = True

        if has_availability:
            available_consultants.append({
                'id': consultant.id,
                'username': consultant.username,
                'first_name': consultant.first_name,
                'last_name': consultant.last_name,
                'email': consultant.email,
                'consultation_fee': getattr(getattr(consultant, 'consultant_service_profile', None), 'consultation_fee', 200.00),
            })

    return Response({'consultants': available_consultants})

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def razorpay_webhook(request):
    """
    Handle Razorpay Webhooks for direct server-to-server confirmation.
    """
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not webhook_secret:
        return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    payload = request.body.decode('utf-8')
    signature = request.headers.get('X-Razorpay-Signature')

    try:
        # Verify the webhook signature
        razorpay_client.utility.verify_webhook_signature(payload, signature, webhook_secret)
        
        event_data = request.data
        event = event_data.get('event')

        if event == 'payment.captured':
            payment_entity = event_data['payload']['payment']['entity']
            razorpay_order_id = payment_entity.get('order_id')
            razorpay_payment_id = payment_entity.get('id')
            
            try:
                booking = ConsultationBooking.objects.get(razorpay_order_id=razorpay_order_id)
                
                # Check if it's already confirmed to avoid duplicate logic
                # Check if it's already confirmed to avoid duplicate logic
                if booking.payment_status != 'paid':
                    with transaction.atomic():
                        booking.payment_status = 'paid'
                        booking.status = 'confirmed'
                        booking.razorpay_payment_id = razorpay_payment_id
                        booking.save()

                    # Logic to generate link and send email - MOVED OUTSIDE TRANSACTION
                    # If this fails, we do NOT want to roll back the payment status.
                    if not booking.meeting_link:
                        try:
                            service = GoogleMeetService()
                            meet_link = service.create_meeting(booking)
                            if meet_link:
                                booking.meeting_link = meet_link
                                booking.save(update_fields=['meeting_link'])
                        except Exception as meet_err:
                            logger.error(f"Webhook error generating link: {meet_err}")

                    if not booking.confirmation_sent:
                        booking.refresh_from_db() # Ensure we have latest data
                        threading.Thread(target=process_booking_confirmation, args=(booking,)).start()
            except ConsultationBooking.DoesNotExist:
                logger.warning(f"Webhook received for unknown order: {razorpay_order_id}")
                
        return Response({'status': 'Webhook processed'}, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Webhook verification failed: {str(e)}", exc_info=True)
        # Return 200 even on error to stop Razorpay from retrying uselessly if sig is wrong
        return Response({'status': 'Invalid signature ignored'}, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def consultant_slots(request):
    """
    Get available 30-minute time slots for a specific consultant on a date.
    Query params: consultant_id, date (YYYY-MM-DD)
    """
    consultant_id = request.query_params.get('consultant_id')
    booking_date = request.query_params.get('date')

    if not all([consultant_id, booking_date]):
        return Response(
            {'error': 'consultant_id and date are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()
        consultant = User.objects.get(id=consultant_id, role='CONSULTANT')
    except (ValueError, User.DoesNotExist):
        return Response(
            {'error': 'Invalid consultant_id or date format'},
            status=status.HTTP_400_BAD_REQUEST
        )

    day_of_week = date_obj.weekday()
    if day_of_week == 6:
        day_of_week = 0
    else:
        day_of_week += 1

    # Get consultant's availability for this date
    available_ranges = []
    
    # Check date override
    override = DateOverride.objects.filter(
        consultant=consultant,
        date=date_obj
    ).first()

    if override:
        if override.is_unavailable:
            return Response({'slots': []})
        if override.start_time and override.end_time:
            available_ranges.append((override.start_time, override.end_time))
    else:
        # Get weekly availability
        weekly_slots = WeeklyAvailability.objects.filter(
            consultant=consultant,
            day_of_week=day_of_week
        )
        for slot in weekly_slots:
            available_ranges.append((slot.start_time, slot.end_time))

    # Generate 30-minute time slots
    time_slots = []
    for start_time, end_time in available_ranges:
        current_time = datetime.combine(date_obj, start_time)
        end_datetime = datetime.combine(date_obj, end_time)
        
        while current_time + timedelta(minutes=30) <= end_datetime:
            slot_start = current_time.time()
            slot_end = (current_time + timedelta(minutes=30)).time()
            
            # Check if this slot is already booked
            is_booked = ConsultationBooking.objects.filter(
                consultant=consultant,
                booking_date=date_obj,
                start_time__lt=slot_end,
                end_time__gt=slot_start,
                status__in=['pending', 'confirmed']
            ).exists()
            
            time_slots.append({
                'start': slot_start.strftime('%H:%M'),
                'end': slot_end.strftime('%H:%M'),
                'is_booked': is_booked
            })
            
            current_time += timedelta(minutes=30)

    return Response({'slots': time_slots})

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def available_consultants(request):
    """
    Find available consultants based on date, time, and topic.
    Query params: date (YYYY-MM-DD), start_time (HH:MM), end_time (HH:MM), topic_id
    """
    booking_date = request.query_params.get('date')
    start_time_str = request.query_params.get('start_time')
    end_time_str = request.query_params.get('end_time')
    topic_id = request.query_params.get('topic_id')

    if not all([booking_date, start_time_str, end_time_str]):
        return Response(
            {'error': 'date, start_time, and end_time are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()
        start_time_obj = datetime.strptime(start_time_str, '%H:%M').time()
        end_time_obj = datetime.strptime(end_time_str, '%H:%M').time()
    except ValueError:
        return Response(
            {'error': 'Invalid date or time format'},
            status=status.HTTP_400_BAD_REQUEST
        )

    day_of_week = date_obj.weekday()
    if day_of_week == 6:  # Sunday in Python is 6, in our model it's 0
        day_of_week = 0
    else:
        day_of_week += 1

    # Get all consultants
    if topic_id:
        consultants = User.objects.filter(role='CONSULTANT', topics__id=topic_id)
    else:
        consultants = User.objects.filter(role='CONSULTANT')
    available_consultants = []

    for consultant in consultants:
        # Check if consultant has date override for this date
        override = DateOverride.objects.filter(
            consultant=consultant,
            date=date_obj
        ).first()

        if override:
            if override.is_unavailable:
                continue  # Skip this consultant
            # Check if time falls within override hours
            if override.start_time and override.end_time:
                if start_time_obj >= override.start_time and end_time_obj <= override.end_time:
                    is_available = True
                else:
                    continue
        else:
            # Check weekly availability
            weekly_slots = WeeklyAvailability.objects.filter(
                consultant=consultant,
                day_of_week=day_of_week
            )
            
            is_available = False
            for slot in weekly_slots:
                if start_time_obj >= slot.start_time and end_time_obj <= slot.end_time:
                    is_available = True
                    break
            
            if not is_available:
                continue

        # Check if slot is already booked
        existing_booking = ConsultationBooking.objects.filter(
            consultant=consultant,
            booking_date=date_obj,
            start_time__lt=end_time_obj,
            end_time__gt=start_time_obj,
            status__in=['pending', 'confirmed']
        ).exists()

        if existing_booking:
            continue

        # Add consultant to available list
        available_consultants.append({
            'id': consultant.id,
            'username': consultant.username,
            'first_name': consultant.first_name,
            'last_name': consultant.last_name,
            'email': consultant.email,
        })

    return Response({'consultants': available_consultants})
