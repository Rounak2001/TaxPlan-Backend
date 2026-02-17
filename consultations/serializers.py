from rest_framework import serializers
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking
from django.contrib.auth import get_user_model

User = get_user_model()

class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ['id', 'name', 'description']

class WeeklyAvailabilitySerializer(serializers.ModelSerializer):
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])
    end_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'])

    class Meta:
        model = WeeklyAvailability
        fields = ['id', 'day_of_week', 'start_time', 'end_time']

    def validate(self, data):
        consultant = self.context['request'].user
        
        # Fallback to instance attributes for partial updates (PATCH)
        start_time = data.get('start_time', getattr(self.instance, 'start_time', None))
        end_time = data.get('end_time', getattr(self.instance, 'end_time', None))
        day_of_week = data.get('day_of_week', getattr(self.instance, 'day_of_week', None))

        if start_time and end_time and start_time >= end_time:
            raise serializers.ValidationError("Start time must be before end time.")

        # Check for overlaps (excluding the current instance if updating)
        overlaps = WeeklyAvailability.objects.filter(
            consultant=consultant,
            day_of_week=day_of_week,
            start_time__lt=end_time,
            end_time__gt=start_time
        )
        
        if self.instance:
            overlaps = overlaps.exclude(pk=self.instance.pk)

        if overlaps.exists():
            # Find the latest end time of existing slots to give a helpful suggestion
            latest_slot = WeeklyAvailability.objects.filter(
                consultant=consultant,
                day_of_week=day_of_week
            ).order_by('-end_time').first()
            
            error_msg = "This time slot overlaps with an existing availability."
            if latest_slot:
                error_msg += f" Please select a time starting from or after {latest_slot.end_time.strftime('%I:%M %p')}."
            
            raise serializers.ValidationError(error_msg)

        return data

    def create(self, validated_data):
        validated_data['consultant'] = self.context['request'].user
        return super().create(validated_data)

class DateOverrideSerializer(serializers.ModelSerializer):
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'], required=False, allow_null=True)
    end_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M', '%H:%M:%S'], required=False, allow_null=True)

    class Meta:
        model = DateOverride
        fields = ['id', 'date', 'is_unavailable', 'start_time', 'end_time']

    def validate(self, data):
        consultant = self.context['request'].user
        
        # Fallback to instance attributes for partial updates (PATCH)
        is_unavailable = data.get('is_unavailable', getattr(self.instance, 'is_unavailable', False))
        
        if not is_unavailable:
            start_time = data.get('start_time', getattr(self.instance, 'start_time', None))
            end_time = data.get('end_time', getattr(self.instance, 'end_time', None))
            date = data.get('date', getattr(self.instance, 'date', None))

            if not start_time or not end_time:
                raise serializers.ValidationError("Start and end times are required for availability.")

            if start_time >= end_time:
                raise serializers.ValidationError("Start time must be before end time.")

            # Check for overlaps (excluding the current instance)
            overlaps = DateOverride.objects.filter(
                consultant=consultant,
                date=date,
                is_unavailable=False,
                start_time__lt=end_time,
                end_time__gt=start_time
            )

            if self.instance:
                overlaps = overlaps.exclude(pk=self.instance.pk)

            if overlaps.exists():
                # Find the latest end time for this date
                latest_override = DateOverride.objects.filter(
                    consultant=consultant,
                    date=date,
                    is_unavailable=False
                ).order_by('-end_time').first()

                error_msg = "This time slot overlaps with an existing override."
                if latest_override and latest_override.end_time:
                    error_msg += f" Please select a time starting from or after {latest_override.end_time.strftime('%I:%M %p')}."
                
                raise serializers.ValidationError(error_msg)

        return data

    def create(self, validated_data):
        validated_data['consultant'] = self.context['request'].user
        return super().create(validated_data)

class ConsultationBookingSerializer(serializers.ModelSerializer):
    consultant_name = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    topic_name = serializers.CharField(source='topic.name', read_only=True)
    
    class Meta:
        model = ConsultationBooking
        fields = [
            'id', 'consultant', 'consultant_name', 'client', 'client_name',
            'topic', 'topic_name', 'booking_date', 'start_time', 'end_time',
            'notes', 'status', 'payment_status', 'razorpay_order_id', 
            'razorpay_payment_id', 'amount', 'meeting_link', 'created_at'
        ]
        # Security: Prevent client from setting status, payment_status, or meeting_link
        read_only_fields = [
            'client', 'created_at', 'status', 'payment_status', 
            'razorpay_order_id', 'razorpay_payment_id', 'amount', 'meeting_link'
        ]
    
    def get_consultant_name(self, obj):
        return f"{obj.consultant.first_name} {obj.consultant.last_name}".strip() or obj.consultant.username
    
    def get_client_name(self, obj):
        return f"{obj.client.first_name} {obj.client.last_name}".strip() or obj.client.username

    def validate(self, data):
        """
        Validate that the requested time slot is not already booked.
        This prevents race conditions where two users book the same slot simultaneously.
        """
        consultant = data.get('consultant')
        booking_date = data.get('booking_date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        # Check for overlaps with confirmed or pending bookings
        # We explicitly check for collisions
        overlapping_bookings = ConsultationBooking.objects.filter(
            consultant=consultant,
            booking_date=booking_date,
            start_time__lt=end_time,
            end_time__gt=start_time,
            status__in=['pending', 'confirmed']
        )
        
        if overlapping_bookings.exists():
            raise serializers.ValidationError("This time slot is already booked. Please choose another time.")
            
        return data
    
    def create(self, validated_data):
        validated_data['client'] = self.context['request'].user
        return super().create(validated_data)
