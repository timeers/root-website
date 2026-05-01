from django.apps import AppConfig


class TheForgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_forge'

    def ready(self):
        from . import signals
        signals._connect()
