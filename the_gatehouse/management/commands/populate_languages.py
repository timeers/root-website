from django.core.management.base import BaseCommand
from django.conf import settings
from the_gatehouse.models import Language  # Import your Language model

class Command(BaseCommand):
    help = 'Populates the Language model based on the LANGUAGES setting'

    def handle(self, *args, **kwargs):
        for code, name in settings.LANGUAGES:
            language, created = Language.objects.get_or_create(code=code, defaults={'name': name})
            if created:
                self.stdout.write(self.style.SUCCESS(f'Language "{name}" ({code}) created.'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Language "{name}" ({code}) already exists.'))