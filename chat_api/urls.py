from django.urls import path
from .views import ChatbotView, PublicChatbotView

urlpatterns = [
    path('', ChatbotView.as_view(), name='chat'),
    path('public/', PublicChatbotView.as_view(), name='public_chat'),
]