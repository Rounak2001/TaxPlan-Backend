from django.apps import AppConfig


class ConsultationsConfig(AppConfig):
    name = 'consultations'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import consultations.signals  # noqa
