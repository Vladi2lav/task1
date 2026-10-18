"""Маршруты проекта: админка, проверка работоспособности /healthz и портал."""
from django.contrib import admin
from django.urls import include, path

from .health import healthz

urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz', healthz, name='healthz'),
    path('', include('portal.urls')),
]
