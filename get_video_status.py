import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from consultant_onboarding.models import VideoResponse

responses = VideoResponse.objects.all().order_by('-uploaded_at')[:10]
for r in responses:
    print(f"ID: {r.id}, Session: {r.session_id}, Status: {getattr(r, 'ai_status', 'UNKNOWN')}, Score: {r.ai_score}")
