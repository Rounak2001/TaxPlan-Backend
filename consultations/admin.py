from django.contrib import admin
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking

@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)
    filter_horizontal = ('consultants',)

@admin.register(WeeklyAvailability)
class WeeklyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('consultant', 'day_of_week', 'start_time', 'end_time')
    list_filter = ('day_of_week', 'consultant')

@admin.register(DateOverride)
class DateOverrideAdmin(admin.ModelAdmin):
    list_display = ('consultant', 'date', 'is_unavailable', 'start_time', 'end_time')
    list_filter = ('date', 'consultant')

@admin.register(ConsultationBooking)
class ConsultationBookingAdmin(admin.ModelAdmin):
    list_display = ('client', 'consultant', 'topic', 'booking_date', 'start_time', 'status', 'payment_status', 'amount')
    list_filter = ('status', 'payment_status', 'booking_date')
    search_fields = ('client__email', 'consultant__email', 'razorpay_order_id')
    readonly_fields = ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature')
