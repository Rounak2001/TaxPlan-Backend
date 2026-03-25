from django.contrib import admin
from .models import Topic, WeeklyAvailability, DateOverride, ConsultationBooking


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)
    filter_horizontal = ('consultants',)

    # PYTHON-DJANGO-26: select_related on FK fields shown in the change form;
    # prefetch_related on the M2M 'consultants' to avoid per-row SELECTs on the
    # filter_horizontal widget and inline rendering.
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'category', 'service'
        ).prefetch_related('consultants')


@admin.register(WeeklyAvailability)
class WeeklyAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('consultant', 'day_of_week', 'start_time', 'end_time')
    list_filter = ('day_of_week', 'consultant')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('consultant')


@admin.register(DateOverride)
class DateOverrideAdmin(admin.ModelAdmin):
    list_display = ('consultant', 'date', 'is_unavailable', 'start_time', 'end_time')
    list_filter = ('date', 'consultant')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('consultant')


@admin.register(ConsultationBooking)
class ConsultationBookingAdmin(admin.ModelAdmin):
    list_display = ('client', 'consultant', 'topic', 'booking_date', 'start_time', 'status', 'payment_status', 'amount')
    list_filter = ('status', 'payment_status', 'booking_date')
    search_fields = ('client__email', 'consultant__email', 'razorpay_order_id')
    readonly_fields = ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature')
    list_select_related = ('client', 'consultant', 'topic')

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('client', 'consultant', 'topic')
        # Exclude completely pending (abandoned checkout) bookings from admin view by default
        return qs.exclude(status='pending', payment_status='pending')
