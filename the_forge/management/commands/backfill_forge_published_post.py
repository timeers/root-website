"""Move forge drafts' publish links from the deprecated ForgedFaction.published_faction
to the generic ForgedFaction.published_post.

Each row is a MOVE: published_post takes the value and published_faction is
cleared in the same update. The legacy column therefore drains to NULL, so
re-running is a no-op and a stale legacy value can never re-link a draft.

Deploy order: migrate -> run this -> restart Apache and Celery -> run this
again (catches links old code wrote before the restart).

TODO: remove ForgedFaction.published_faction and this command once it has run
in every environment and published_faction is NULL everywhere.
"""
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models import F

from the_forge.models import ForgedFaction


class Command(BaseCommand):
    help = "Move ForgedFaction.published_faction links into published_post."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report what would change without writing.")

    def handle(self, *args, dry_run=False, **options):
        moved = cleared = 0
        conflicts = []

        drafts = (
            ForgedFaction.objects.filter(published_faction__isnull=False)
            .values_list('pk', 'faction_name', 'published_faction_id', 'published_post_id')
            .order_by('pk')
        )
        for pk, name, legacy_id, post_id in drafts:
            if post_id and post_id != legacy_id:
                conflicts.append(f"#{pk} {name}: published_faction={legacy_id} but published_post={post_id}")
                continue
            if dry_run:
                if post_id:
                    cleared += 1
                else:
                    moved += 1
                continue
            try:
                with transaction.atomic():
                    if post_id:
                        # Already moved (same post): just drain the legacy value.
                        ForgedFaction.objects.filter(pk=pk).update(published_faction=None)
                        cleared += 1
                    else:
                        ForgedFaction.objects.filter(pk=pk, published_post__isnull=True).update(
                            published_post_id=F('published_faction_id'), published_faction=None,
                        )
                        moved += 1
            except IntegrityError:
                conflicts.append(f"#{pk} {name}: post {legacy_id} is already linked to another draft")

        prefix = "[dry run] would have " if dry_run else ""
        self.stdout.write(f"{prefix}moved {moved}, cleared {cleared} already-moved legacy value(s).")
        for line in conflicts:
            self.stdout.write(self.style.WARNING(f"Skipped {line}"))
        if not conflicts:
            self.stdout.write(self.style.SUCCESS("No conflicts."))
