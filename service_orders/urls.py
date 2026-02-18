from django.urls import path
from .views import create_order, verify_payment, razorpay_webhook

urlpatterns = [
    path('create-order/', create_order, name='create-order'),
    path('verify-payment/', verify_payment, name='verify-payment'),
    path('razorpay-webhook/', razorpay_webhook, name='razorpay-webhook'),
]
