from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet
from .whatsapp_webhook import WhatsAppWebhookView

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('webhook/whatsapp/', WhatsAppWebhookView.as_view(), name='whatsapp_webhook'),
    path('', include(router.urls)),
]
