from django.contrib import admin
from django.utils import timezone
from .models import Ticket, TicketComment

class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 1
    readonly_fields = ['author', 'is_admin_reply', 'created_at']
    
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'message':
            field.widget.attrs['rows'] = 3
        return field

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    inlines = [TicketCommentInline]
    list_display = ['ticket_id', 'user', 'category', 'priority', 'status', 'created_at']
    list_filter = ['status', 'priority', 'category']
    search_fields = ['ticket_id', 'subject', 'user__email', 'user__full_name']
    
    fieldsets = (
        ("Support Info", {
            "fields": ('ticket_id', 'status', 'priority', 'category', 'related_service')
        }),
        ("User Details", {
            "fields": ('user', 'subject', 'description', 'resolution')
        }),
        ("Internal", {
            "fields": ('admin_notes', 'admin_viewed_at', 'created_at', 'updated_at')
        }),
    )
    
    readonly_fields = [
        'ticket_id', 'user', 'category', 'subject', 'description', 
        'related_service', 'created_at', 'updated_at', 'admin_viewed_at'
    ]

    actions = ['mark_in_progress', 'mark_resolved', 'mark_closed']

    def mark_in_progress(self, request, queryset):
        queryset.update(status='in_progress')
    mark_in_progress.short_description = "Mark selected tickets as In Progress"

    def mark_resolved(self, request, queryset):
        queryset.update(status='resolved')
    mark_resolved.short_description = "Mark selected tickets as Resolved"

    def mark_closed(self, request, queryset):
        queryset.update(status='closed')
    mark_closed.short_description = "Mark selected tickets as Closed"

    def save_model(self, request, obj, form, change):
        if change and obj.admin_viewed_at is None:
            obj.admin_viewed_at = timezone.now()
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, TicketComment) and not instance.pk:
                instance.author = request.user
                instance.is_admin_reply = True
            instance.save()
        formset.save_m2m()
