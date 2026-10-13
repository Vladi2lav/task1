from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'portal'
    verbose_name = 'Портал итогов торгов'

    def ready(self):
        # Подключаем регистронезависимый поиск по кириллице (см. portal/search.py)
        from . import search

        search.register()
