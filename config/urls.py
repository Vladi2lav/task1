"""Маршруты проекта: админка, проверка работоспособности /healthz, выбор языка и портал."""
from django.contrib import admin
from django.urls import include, path

from .health import healthz

urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz', healthz, name='healthz'),
    # переключатель языка: POST /i18n/setlang/ (стандартный set_language, с CSRF);
    # префиксов языка в адресах нет — ссылки и API не меняются
    path('i18n/', include('django.conf.urls.i18n')),
    path('', include('portal.urls')),
]
