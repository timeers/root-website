"""Render setup cards for completed Forge maps that don't have one yet (maps
completed before setup cards existed), and refresh their stored TTS JSON so it
carries the card. Run once after deploying setup cards:

    python manage.py render_map_setup_cards [--all]

--all re-checks every completed map (only out-of-date cards are re-rendered)."""
from django.core.management.base import BaseCommand

from the_forge.models import ForgedMap
from the_forge.services.forge_map import refresh_forge_setup_card, store_map_tts


class Command(BaseCommand):
    help = "Render setup cards for completed Forge maps that don't have one."

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Check every completed map, not just those without a card.')

    def handle(self, *args, **options):
        maps = ForgedMap.objects.exclude(image_preview='').exclude(image_preview__isnull=True).select_related('faction')
        if not options['all']:
            maps = maps.filter(card_image='') | maps.filter(card_image__isnull=True)
        rendered = 0
        for forged_map in maps:
            try:
                changed = refresh_forge_setup_card(forged_map)
            except Exception as err:   # one bad map mustn't stop the rest
                self.stderr.write(f'#{forged_map.faction_id} {forged_map.title}: {err}')
                continue
            if changed:
                rendered += 1
                if forged_map.faction.tts_json:
                    store_map_tts(forged_map)
                self.stdout.write(f'#{forged_map.faction_id} {forged_map.title}: rendered')
        self.stdout.write(self.style.SUCCESS(f'{rendered} setup card(s) rendered.'))
