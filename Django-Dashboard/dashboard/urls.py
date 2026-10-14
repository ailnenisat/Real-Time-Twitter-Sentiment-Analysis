from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('classify/', views.classify, name='classify'),
    path('api/stats/', views.api_stats, name='api_stats'),
]
