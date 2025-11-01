from django.core.management.base import BaseCommand
from django.conf import settings
from the_gatehouse.models import Language
from django.conf.locale import LANG_INFO 

class Command(BaseCommand):
    help = 'Populates the Language model based on the LANGUAGES setting'

    def handle(self, *args, **kwargs):
        for code, _ in settings.LANGUAGES:
            # Fallback to code if LANG_INFO is missing it
            native_name = LANG_INFO.get(code, {}).get('name_local', code).capitalize()
            language, created = Language.objects.get_or_create(code=code)
            if created:
                language.name = native_name
                language.save()
                self.stdout.write(self.style.SUCCESS(f'Language "{native_name}" ({code}) created.'))
            else:
                if language.name != native_name:
                    language.name = native_name
                    language.save()
                    self.stdout.write(self.style.WARNING(f'Language "{code}" updated to native name "{native_name}".'))
                else:
                    self.stdout.write(self.style.SUCCESS(f'Language "{native_name}" ({code}) already exists and is up-to-date.'))