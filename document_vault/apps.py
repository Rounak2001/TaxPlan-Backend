from django.apps import AppConfig

class DocumentVaultConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'document_vault'

    def ready(self):
        import document_vault.signals
