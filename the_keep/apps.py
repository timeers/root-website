from django.apps import AppConfig


class TheKeepConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_keep'

    def ready(self):
        import the_keep.signals