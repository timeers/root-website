"""Time a Forge draft's sync to its keep post, stage by stage, outside the
request (so without mod_wsgi's 120 s request-timeout).

    python3 manage.py time_forge_sync <forged_pk>              # the sync page's load
    python3 manage.py time_forge_sync <forged_pk> --apply-tts  # + a real sync

Without --apply-tts it does what loading the sync page does: refresh any stale
previews and build the form and plans. With --apply-tts it then syncs for real,
as clicking "Sync Selected" with only the Tabletop Simulator row ticked: that
row forces the rows it depends on (images, pieces, decks, TTS assets), and
nothing else (text fields) is applied.

On production, run it as the web server's user (e.g. sudo -u www-data ...) so
the files it writes are owned the way Apache expects.
"""
from django.core.management.base import BaseCommand, CommandError

from the_forge.models import ForgedFaction
from the_forge.services.sync_timing import SyncTimer, sync_stage


class Command(BaseCommand):
    help = "Time a Forge draft's sync to its keep post, stage by stage."

    def add_arguments(self, parser):
        parser.add_argument('forged_pk', type=int)
        parser.add_argument('--apply-tts', action='store_true',
                            help="Also sync for real, ticking only the Tabletop Simulator row.")

    def handle(self, *args, forged_pk, apply_tts=False, **options):
        from the_forge.forms_publish import ForgedFactionSyncForm
        from the_forge.publishing import publisher_for
        from the_forge.publishing.diff import sync_mode
        from the_forge.views import _sync_keep_tts_json

        forged = ForgedFaction.objects.filter(pk=forged_pk).first()
        if forged is None:
            raise CommandError(f"No Forge draft #{forged_pk}.")
        publisher = publisher_for(forged)
        if publisher is None:
            raise CommandError(f"#{forged_pk} ({forged.component}) can't be published.")
        mode = sync_mode(forged)
        if mode is None:
            raise CommandError(f"#{forged_pk} isn't linked to a keep post or translation.")
        self.stdout.write(f"#{forged.pk} {forged.faction_name} ({forged.component}, linked: {mode})")

        with SyncTimer(f'{forged.pk} command', echo=self.stdout.write):
            with sync_stage('refresh'):
                publisher.refresh(forged, for_sync=True)
            if (blocked := publisher.sync_blocked(forged)):
                raise CommandError(blocked)
            with sync_stage('form'):
                form = ForgedFactionSyncForm(forged=forged)
            # The form only has a field for each row that's out of date.
            self.stdout.write(f"out of date: {', '.join(form.fields) or 'nothing'}")
            if not apply_tts:
                return
            if mode != 'post':
                raise CommandError("--apply-tts needs a draft linked to a keep post (not a translation).")
            if 'sync_tts_json' not in form.fields:
                self.stdout.write("The Tabletop Simulator row is up to date: nothing to apply.")
                return

            with sync_stage('form'):
                form = ForgedFactionSyncForm({'sync_tts_json': 'on'}, forged=forged)
                valid = form.is_valid()
            if not valid:
                raise CommandError(f"The sync form is invalid: {form.errors.as_text()}")
            applied = [name for name, value in form.cleaned_data.items() if value]
            self.stdout.write(f"applying: {', '.join(applied)}")
            with sync_stage('save'):
                form.save()
            with sync_stage('tts'):
                error = _sync_keep_tts_json(forged.pk, profile=forged.designer)
        if error:
            raise CommandError(f"Tabletop Simulator wasn't updated: {error}")
        self.stdout.write(self.style.SUCCESS("Synced."))
