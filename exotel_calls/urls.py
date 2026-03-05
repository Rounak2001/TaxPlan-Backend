from django.urls import path
from .views import (
    InitiateCallView, CallStatusCallbackView, CallLogsListView, 
    UpdateCallOutcomeView, RefreshCallDetailsView, IncomingCallRouteView,
    IncomingCallPassthruView, CheckConsultantCountView, DynamicIVRView
)

urlpatterns = [
    path('initiate/', InitiateCallView.as_view(), name='initiate-call'),
    path('status-callback/', CallStatusCallbackView.as_view(), name='call-status-callback'),
    path('incoming-route/', IncomingCallRouteView.as_view(), name='incoming-call-route'),
    path('incoming-passthru/', IncomingCallPassthruView.as_view(), name='incoming-call-passthru'),
    path('check-consultant-count/', CheckConsultantCountView.as_view(), name='check-consultant-count'),
    path('dynamic-ivr/', DynamicIVRView.as_view(), name='dynamic-ivr'),
    path('logs/', CallLogsListView.as_view(), name='call-logs'),
    path('logs/<int:call_id>/outcome/', UpdateCallOutcomeView.as_view(), name='update-call-outcome'),
    path('logs/<int:call_id>/refresh/', RefreshCallDetailsView.as_view(), name='refresh-call-details'),
]
