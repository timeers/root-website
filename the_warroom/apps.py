from django.apps import AppConfig


class TheWarroomConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'the_warroom'

    def ready(self):
        import the_warroom.signals