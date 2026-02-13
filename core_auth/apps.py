from django.apps import AppConfig


class CoreAuthConfig(AppConfig):
    name = 'core_auth'

    def ready(self):
        import core_auth.signals
