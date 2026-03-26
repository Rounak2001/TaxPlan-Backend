from django.contrib import admin
from .models import (
    ConsultantServiceProfile,
    ServiceCategory,
    Service,
    ConsultantServiceExpertise,
    ClientServiceRequest
)


class ConsultantServiceExpertiseInline(admin.TabularInline):
    model = ConsultantServiceExpertise
    extra = 1
    fields = ['service']
    autocomplete_fields = ['service']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('service', 'service__category')


@admin.register(ConsultantServiceProfile)
class ConsultantServiceProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'email', 'qualification', 'experience_years', 'consultation_fee', 'is_active', 'current_client_count', 'max_concurrent_clients']
    list_filter = ['is_active', 'qualification']
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'user__phone_number']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [ConsultantServiceExpertiseInline]

    # PYTHON-DJANGO-X: Eliminates per-row SELECT on user FK in list view and change form
    list_select_related = ('user',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


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
    # PYTHON-DJANGO-1V: autocomplete on ServiceAdmin needs search_fields + select_related
    list_select_related = ('category',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('category')


@admin.register(ConsultantServiceExpertise)
class ConsultantServiceExpertiseAdmin(admin.ModelAdmin):
    list_display = ['consultant', 'service', 'added_at']
    list_filter = ['service__category']
    search_fields = ['consultant__user__first_name', 'consultant__user__last_name', 'service__title']
    readonly_fields = ['added_at']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'consultant__user', 'service', 'service__category'
        )


@admin.register(ClientServiceRequest)
class ClientServiceRequestAdmin(admin.ModelAdmin):
    list_display = ['client', 'service', 'status', 'assigned_consultant', 'created_at', 'assigned_at']
    list_filter = ['status', 'service__category']
    search_fields = ['client__email', 'service__title', 'assigned_consultant__user__first_name', 'assigned_consultant__user__last_name']
    readonly_fields = ['created_at', 'updated_at']

    # PYTHON-DJANGO-24: Eliminates per-row SELECT on client/service/consultant in list view
    list_select_related = ('client', 'service', 'service__category', 'assigned_consultant__user')

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

    # PYTHON-DJANGO-28: Eliminates per-field SELECT on change form
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'client',
            'service',
            'service__category',
            'assigned_consultant',
            'assigned_consultant__user',
        )
