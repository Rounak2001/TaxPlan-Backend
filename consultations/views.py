from rest_framework import viewsets, permissions, status
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from datetime import datetime, time, timedelta
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking
from .emails import send_booking_confirmation
from .serializers import (
    TopicSerializer, WeeklyAvailabilitySerializer, DateOverrideSerializer,
    ConsultationBookingSerializer
)
from .google_meet import GoogleMeetService

User = get_user_model()

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

class ConsultationBookingViewSet(viewsets.ModelViewSet):
    serializer_class = ConsultationBookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'CONSULTANT':
            return ConsultationBooking.objects.filter(consultant=user)
        return ConsultationBooking.objects.filter(client=user)

    def perform_create(self, serializer):
        booking = serializer.save()
        
        # Try to create Google Meet link
        try:
            service = GoogleMeetService()
            meet_link = service.create_meeting(booking)
            if meet_link:
                booking.meeting_link = meet_link
                booking.save()
        except Exception as e:
            print(f"Failed to generate Google Meet link: {str(e)}")

        # Fallback: Send confirmation email if it hasn't been sent yet (e.g. if link failed)
        if not booking.confirmation_sent:
            send_booking_confirmation(booking)

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
            })

    return Response({'consultants': available_consultants})

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
            
            if not is_booked:
                time_slots.append({
                    'start': slot_start.strftime('%H:%M'),
                    'end': slot_end.strftime('%H:%M'),
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
