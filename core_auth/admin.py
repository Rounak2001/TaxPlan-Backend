from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, ConsultantProfile, ClientProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'phone_number', 'is_phone_verified', 'is_onboarded', 'is_staff')
    list_filter = ('role', 'is_phone_verified', 'is_onboarded', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'phone_number', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Fields', {
            'fields': ('role', 'phone_number', 'is_phone_verified', 'is_onboarded'),
        }),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Custom Fields', {
            'fields': ('role', 'phone_number'),
        }),
    )


class ConsultantProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'consultation_fee', 'current_load', 'max_capacity', 'services')
    list_filter = ('max_capacity',)
    search_fields = ('user__username', 'user__email')

if not admin.site.is_registered(ConsultantProfile):
    admin.site.register(ConsultantProfile, ConsultantProfileAdmin)


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'assigned_consultant', 'pan_number')
    list_filter = ('assigned_consultant',)
    search_fields = ('user__username', 'user__email', 'pan_number')
    autocomplete_fields = ['assigned_consultant']
