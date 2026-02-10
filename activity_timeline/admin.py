from django.contrib import admin
from .models import Activity


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    """Admin interface for Activity model"""
    list_display = ['id', 'activity_type', 'title', 'actor', 'target_user', 'created_at']
    list_filter = ['activity_type', 'created_at']
    search_fields = ['title', 'description', 'actor__username', 'target_user__username']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Activity Info', {
            'fields': ('activity_type', 'title', 'description')
        }),
        ('Users', {
            'fields': ('actor', 'target_user')
        }),
        ('Related Object', {
            'fields': ('content_type', 'object_id')
        }),
        ('Metadata', {
            'fields': ('metadata', 'created_at'),
            'classes': ('collapse',)
        }),
    )
