from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, TicketCommentViewSet

router = DefaultRouter()
router.register(r'', TicketViewSet, basename='ticket')

urlpatterns = [
    path('', include(router.urls)),
    path('<int:ticket_pk>/comments/', TicketCommentViewSet.as_view({'get': 'list', 'post': 'create'}), name='ticket-comments'),
]
