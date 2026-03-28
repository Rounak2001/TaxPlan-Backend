from django.urls import path
from .views import (
    create_order,
    verify_payment,
    razorpay_webhook,
    validate_coupon,
    request_additional_service,
    additional_service_options,
    pending_additional_requests,
    decline_additional_request,
)

urlpatterns = [
    path('create-order/', create_order, name='create-order'),
    path('verify-payment/', verify_payment, name='verify-payment'),
    path('razorpay-webhook/', razorpay_webhook, name='razorpay-webhook'),
    path('validate-coupon/', validate_coupon, name='validate-coupon'),
    path('request-additional/', request_additional_service, name='request-additional-service'),
    path(
        'additional-service-options/',
        additional_service_options,
        name='additional-service-options',
    ),
    path('pending-additional/', pending_additional_requests, name='pending-additional'),
    path('decline-additional/', decline_additional_request, name='decline-additional'),
]

