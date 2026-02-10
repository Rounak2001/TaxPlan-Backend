from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core_auth.urls')),
    path('api/consultations/', include('consultations.urls')),
    path('api/vault/', include('document_vault.urls')),
    path('api/chat/', include('chat_api.urls')),  # AI Chat API
    path('api/gst/', include('gst_reports.urls')),
    path('api/tds/', include('tds_api.urls')),
    path('api/calculator/', include('calculator.urls')),
    path('api/payments/', include('service_orders.urls')),
    path('api/consultants/', include('consultants.urls')),
    path('api/calls/', include('exotel_calls.urls')),
    path('api/activity/', include('activity_timeline.urls')),
    path('api/conversations/', include('chat.urls')),  # Real-time Chat
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
