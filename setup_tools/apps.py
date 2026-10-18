from django.apps import AppConfig


class SetupToolsConfig(AppConfig):
    """Служебные команды развёртывания (manage.py bootstrap). Моделей нет."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'setup_tools'
    verbose_name = 'Развёртывание'
