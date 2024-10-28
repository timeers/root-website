from django.apps import AppConfig


class TheGatehouseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_gatehouse'

    def ready(self):
        import the_gatehouse.signals