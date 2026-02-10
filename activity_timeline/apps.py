from django.apps import AppConfig


class ActivityTimelineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'activity_timeline'
    verbose_name = 'Activity Timeline'
    
    def ready(self):
        """Import signals when app is ready"""
        import activity_timeline.signals
