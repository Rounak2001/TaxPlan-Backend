from django.urls import path
from . import views

urlpatterns = [
    path('sections/', views.get_sections, name='get_sections'),
    path('calculate/', views.calculate_tds, name='calculate_tds'),
    path('generate-excel/', views.generate_excel, name='generate_excel'),
    path('save/', views.save_calculator_data, name='save_calculator_data'),
    path('get/<str:calculator_type>/', views.get_calculator_data, name='get_calculator_data'),
]
