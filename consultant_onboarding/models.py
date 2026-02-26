from django.db import models
from django.conf import settings

# ----------------------------------------------------
# ONBOARDING APPLICATION MODEL (Replaces User)
# ----------------------------------------------------

class ConsultantApplication(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Assessment'),
        ('REVIEW', 'Under Admin Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    # Use Email as the primary identifier during onboarding
    email = models.EmailField(unique=True)
    google_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    
    first_name = models.CharField(max_length=150, blank=True)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    
    age = models.PositiveIntegerField(null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    
    # Address Split
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    
    # Professional Details
    PRACTICE_TYPE_CHOICES = [
        ('Individual', 'Individual'),
    ]
    practice_type = models.CharField(max_length=50, choices=PRACTICE_TYPE_CHOICES, null=True, blank=True)
    qualification = models.CharField(max_length=255, blank=True)
    experience_years = models.IntegerField(default=0)
    certifications = models.TextField(blank=True)
    bio = models.TextField(blank=True)
    
    # Status fields
    is_verified = models.BooleanField(default=False)
    has_accepted_declaration = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    # Assessment Data
    test_score = models.IntegerField(null=True, blank=True)
    test_passed = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_onboarded(self):
        """Check if the basic profile information is filled"""
        return all([
            self.first_name,
            self.last_name,
            self.phone_number,
            self.city,
            self.state,
            self.pincode
        ])

    class Meta:
        db_table = 'consultant_applications'
        verbose_name = 'Consultant Application'
        verbose_name_plural = 'Consultant Applications'

    def __str__(self):
        return f"Application: {self.email} ({self.status})"

    def get_full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(filter(None, parts)) or self.email


# ----------------------------------------------------
# DOCUMENT VERIFICATION MODELS 
# ----------------------------------------------------

class AuthConsultantDocument(models.Model):
    DOCUMENT_TYPES = [
        ('Qualification', 'Qualification Degree'),
        ('Certificate', 'Certificate'),
        ('bachelors_degree', "Bachelor's Degree"),
        ('masters_degree', "Master's Degree"),
        ('certificate', 'Certificate (Additional)'),
    ]

    application = models.ForeignKey(ConsultantApplication, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to='consultant_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_consultant_documents'
        verbose_name = 'Auth Consultant Document'
        verbose_name_plural = 'Auth Consultant Documents'
        ordering = ['-uploaded_at']


class IdentityDocument(models.Model):
    application = models.ForeignKey(ConsultantApplication, on_delete=models.CASCADE, related_name='identity_documents')
    file_path = models.CharField(max_length=500)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    # Gemini Verification Fields
    document_type = models.CharField(max_length=100, blank=True, null=True, help_text="Type of document identified by Gemini (e.g., Aadhaar, PAN)")
    verification_status = models.CharField(max_length=50, blank=True, null=True, help_text="Verification status from Gemini (e.g., Verified, Invalid)")
    gemini_raw_response = models.TextField(blank=True, null=True, help_text="Raw JSON response from Gemini")

    class Meta:
        db_table = 'application_identity_documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.application.email} - Identity Document"


class ConsultantDocument(models.Model):
    application = models.ForeignKey(ConsultantApplication, on_delete=models.CASCADE, related_name='consultant_documents')
    qualification_type = models.CharField(max_length=100)
    document_type = models.CharField(max_length=100)
    file_path = models.CharField(max_length=500)
    verification_status = models.CharField(max_length=50, blank=True, null=True)
    gemini_raw_response = models.TextField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_consultant_documents_consultantdocument'

    def __str__(self):
        return f"{self.application.email} - {self.document_type}"


class PANVerification(models.Model):
    """Stores the result of PAN verification for an applicant"""
    application = models.OneToOneField(
        ConsultantApplication, 
        on_delete=models.CASCADE, 
        related_name='pan_verification'
    )
    
    verified_full_name = models.CharField(max_length=255, blank=True)
    verified_dob = models.DateField(null=True, blank=True)
    
    full_name_match = models.BooleanField(default=False)
    dob_match = models.BooleanField(default=False)
    status = models.CharField(max_length=20)
    
    verified_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'application_pan_verifications'
        verbose_name = 'PAN Verification'
        verbose_name_plural = 'PAN Verifications'

    def __str__(self):
        return f"{self.application.email} - {self.status}"


class SandboxToken(models.Model):
    """Stores the Sandbox API access token to handle 24h validity"""
    access_token = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'application_sandbox_tokens'
        verbose_name = 'Sandbox Token'
        verbose_name_plural = 'Sandbox Tokens'

    def __str__(self):
        return f"Token updated at {self.updated_at}"


# ----------------------------------------------------
# ASSESSMENT MODELS
# ----------------------------------------------------

class TestType(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    class Meta:
        db_table = 'application_assessment_testtype'

    def __str__(self):
        return self.name

class VideoQuestion(models.Model):
    text = models.TextField()
    test_type = models.ForeignKey(TestType, on_delete=models.CASCADE, related_name='video_questions', null=True, blank=True)

    class Meta:
        db_table = 'application_assessment_videoquestion'

    def __str__(self):
        return self.text

class UserSession(models.Model):
    application = models.ForeignKey(ConsultantApplication, on_delete=models.CASCADE, related_name='assessment_sessions')
    
    test_type = models.ForeignKey(TestType, on_delete=models.SET_NULL, null=True, blank=True)
    
    selected_domains = models.JSONField(default=list) 
    question_set = models.JSONField(default=list)
    video_question_set = models.JSONField(default=list) 
    score = models.FloatField(default=0.0)

    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='ongoing', choices=[('ongoing', 'Ongoing'), ('completed', 'Completed'), ('flagged', 'Flagged')])
    violation_count = models.IntegerField(default=0)
    tab_switch_count = models.IntegerField(default=0)
    cam_violation_count = models.IntegerField(default=0)
    is_disqualified = models.BooleanField(default=False)

    class Meta:
        db_table = 'application_assessment_usersession'

    def __str__(self):
        return f"Session {self.id} - {self.application.email}"

class Violation(models.Model):
    session = models.ForeignKey(UserSession, on_delete=models.CASCADE, related_name='violations')
    violation_type = models.CharField(max_length=50) 
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_assessment_violation'

class VideoResponse(models.Model):
    session = models.ForeignKey(UserSession, on_delete=models.CASCADE, related_name='video_responses')
    
    question_identifier = models.CharField(max_length=255, default="unknown") 
    video_file = models.TextField() 
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # AI Evaluation Fields
    ai_transcript = models.TextField(null=True, blank=True)
    ai_score = models.IntegerField(null=True, blank=True)
    ai_feedback = models.JSONField(null=True, blank=True)
    ai_status = models.CharField(max_length=20, default='pending', choices=[
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ])

    class Meta:
        db_table = 'application_assessment_videoresponse'

class ProctoringSnapshot(models.Model):
    session = models.ForeignKey(UserSession, on_delete=models.CASCADE, related_name='proctoring_snapshots')
    image_url = models.TextField() 
    timestamp = models.DateTimeField(auto_now_add=True)
    is_violation = models.BooleanField(default=False)
    violation_reason = models.TextField(null=True, blank=True)
    face_count = models.IntegerField(default=0)
    match_score = models.FloatField(default=0.0)

    class Meta:
        db_table = 'application_assessment_proctoringsnapshot'

    def __str__(self):
        return f"Snapshot {self.id} - Session {self.session.id}"


# ----------------------------------------------------
# FACE_VERIFICATION MODELS
# ----------------------------------------------------

class FaceVerification(models.Model):
    application = models.ForeignKey(ConsultantApplication, on_delete=models.CASCADE, related_name='face_verifications')
    id_image_path = models.CharField(max_length=255)
    live_image_path = models.CharField(max_length=255, blank=True, default='')
    confidence = models.FloatField(null=True, blank=True)
    is_match = models.BooleanField(default=False)
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_face_verifications'
        ordering = ['-verified_at']

    def __str__(self):
        return f"{self.application.email} - {'Match' if self.is_match else 'No Match'}"


# ----------------------------------------------------
# CREDENTIAL MANAGEMENT
# ----------------------------------------------------

class ConsultantCredential(models.Model):
    """Stores generated login credentials for approved consultants."""
    application = models.OneToOneField(
        ConsultantApplication, 
        on_delete=models.CASCADE, 
        related_name='credentials'
    )
    username = models.CharField(max_length=100, unique=True)
    password = models.CharField(max_length=255)  # Stored for admin reference; User password is hashed separately
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_consultant_credentials'
        verbose_name = 'Consultant Credential'
        verbose_name_plural = 'Consultant Credentials'

    def __str__(self):
        return f"{self.application.email} - {self.username}"
