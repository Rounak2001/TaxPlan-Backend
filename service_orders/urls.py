from django.urls import path
from .views import create_order, verify_payment

urlpatterns = [
    path('create-order/', create_order, name='create-order'),
    path('verify-payment/', verify_payment, name='verify-payment'),
]
