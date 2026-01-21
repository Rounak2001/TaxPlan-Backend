from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core_auth.urls')),
    path('api/consultations/', include('consultations.urls')),
]
