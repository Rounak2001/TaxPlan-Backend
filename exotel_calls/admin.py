from django.contrib import admin
from django.utils.html import format_html
from .models import CallLog


@admin.register(CallLog)
class CallLogAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'caller', 'callee', 'callee_phone', 'status', 'outcome', 
        'duration_display', 'price', 'recording_link', 'created_at'
    )
    list_filter = ('status', 'outcome', 'created_at')
    search_fields = ('caller__username', 'callee__username', 'callee__phone', 'exotel_sid', 'notes')
    readonly_fields = (
        'exotel_sid', 'recording_url', 'from_number', 'to_number',
        'price', 'start_time', 'end_time', 'created_at', 'updated_at',
        'callee_phone_display'
    )
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Call Details', {
            'fields': ('caller', 'callee', 'callee_phone_display', 'status', 'duration')
        }),
        ('Outcome & Notes', {
            'fields': ('outcome', 'notes', 'follow_up_date')
        }),
        ('Exotel Data', {
            'fields': ('exotel_sid', 'from_number', 'to_number', 'recording_url', 'price'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('start_time', 'end_time', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def callee_phone(self, obj):
        """Display callee's phone number from User model."""
        if obj.callee and obj.callee.phone_number:
            return obj.callee.phone_number
        return '-'
    callee_phone.short_description = 'Client Phone'
    
    def callee_phone_display(self, obj):
        """Display callee's phone number in detail view."""
        if obj.callee and obj.callee.phone_number:
            return obj.callee.phone_number
        return 'Not available'
    callee_phone_display.short_description = 'Client Phone Number'
    
    def duration_display(self, obj):
        """Display duration in MM:SS format."""
        return obj.duration_display
    duration_display.short_description = 'Duration'
    
    def recording_link(self, obj):
        """Display recording as clickable link."""
        if obj.recording_url:
            return format_html(
                '<a href="{}" target="_blank">▶ Play</a>',
                obj.recording_url
            )
        return '-'
    recording_link.short_description = 'Recording'


