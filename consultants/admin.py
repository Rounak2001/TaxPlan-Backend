from django.contrib import admin
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest
)


@admin.register(ConsultantServiceProfile)
class ConsultantServiceProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'email', 'qualification', 'experience_years', 'is_active', 'current_client_count', 'max_concurrent_clients']
    list_filter = ['is_active', 'qualification']
    search_fields = ['full_name', 'email', 'phone']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'price', 'tat', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['title', 'category__name']
    readonly_fields = ['created_at']


@admin.register(ConsultantServiceExpertise)
class ConsultantServiceExpertiseAdmin(admin.ModelAdmin):
    list_display = ['consultant', 'service', 'added_at']
    list_filter = ['service__category']
    search_fields = ['consultant__full_name', 'service__title']
    readonly_fields = ['added_at']


@admin.register(ClientServiceRequest)
class ClientServiceRequestAdmin(admin.ModelAdmin):
    list_display = ['client', 'service', 'status', 'assigned_consultant', 'created_at', 'assigned_at']
    list_filter = ['status', 'service__category']
    search_fields = ['client__email', 'service__title', 'assigned_consultant__full_name']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Request Details', {
            'fields': ('client', 'service', 'status', 'notes', 'priority')
        }),
        ('Assignment', {
            'fields': ('assigned_consultant', 'assigned_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )
