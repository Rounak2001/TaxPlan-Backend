from django.apps import AppConfig


class ConsultantsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'consultants'

    def ready(self):
        """Import signals when Django starts"""
        import consultants.signals
