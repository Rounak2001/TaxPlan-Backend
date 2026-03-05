from django.contrib import admin
from django.contrib import messages
from .models import (
    ConsultantApplication,
    AuthConsultantDocument,
    IdentityDocument,
    ConsultantDocument,
    PANVerification,
    TestType,
    VideoQuestion,
    UserSession,
    Violation,
    VideoResponse,
    ProctoringSnapshot,
    ConsultantCredential,
)
from core_auth.models import User
from consultants.models import ConsultantServiceProfile

@admin.action(description='Approve applications and create live Consultant users')
def approve_applications(modeladmin, request, queryset):
    success_count = 0
    error_count = 0

    for app in queryset:
        if app.status == 'APPROVED':
            continue

        try:
            # 1. Create the real User if they don't exist
            user, created = User.objects.get_or_create(
                email=app.email,
                defaults={
                    'username': app.email.split('@')[0],
                    'first_name': app.first_name,
                    'last_name': app.last_name,
                    'phone_number': app.phone_number,
                    'role': User.CONSULTANT,
                    'is_onboarded': True,
                    'is_phone_verified': True, 
                    'google_id': app.google_id
                }
            )
            
            # If the user already exists, update their role to Consultant
            if not created and user.role != User.CONSULTANT:
                 user.role = User.CONSULTANT
                 user.is_onboarded = True
                 user.save()

            # 2. Create their live profile
            ConsultantServiceProfile.objects.get_or_create(
                user=user,
                defaults={
                    'qualification': app.qualification,
                    'experience_years': app.experience_years or 0,
                    'certifications': app.certifications,
                    'bio': app.bio,
                    'is_active': True
                }
            )
            
            # 3. Mark approved
            app.status = 'APPROVED'
            app.save()
            success_count += 1
            
        except Exception as e:
            error_count += 1
            messages.error(request, f"Error processing {app.email}: {str(e)}")

    if success_count:
        messages.success(request, f"Successfully approved {success_count} consultants.")
    if error_count:
        messages.warning(request, f"Failed to approve {error_count} applications.")

class ConsultantApplicationAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'status', 'test_score', 'created_at')
    list_filter = ('status', 'practice_type')
    search_fields = ('email', 'first_name', 'last_name')
    actions = [approve_applications]

admin.site.register(ConsultantApplication, ConsultantApplicationAdmin)

# Register the rest of the models for viewing in admin
admin.site.register(AuthConsultantDocument)
admin.site.register(IdentityDocument)
admin.site.register(ConsultantDocument)
admin.site.register(PANVerification)

class TestTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
admin.site.register(TestType, TestTypeAdmin)

class VideoQuestionAdmin(admin.ModelAdmin):
    list_display = ('text', 'test_type')
admin.site.register(VideoQuestion, VideoQuestionAdmin)

class UserSessionAdmin(admin.ModelAdmin):
    list_display = ('application', 'test_type', 'score', 'status', 'start_time')
    list_filter = ('status', 'test_type')
admin.site.register(UserSession, UserSessionAdmin)

admin.site.register(Violation)
admin.site.register(VideoResponse)
admin.site.register(ProctoringSnapshot)
admin.site.register(ConsultantCredential)
