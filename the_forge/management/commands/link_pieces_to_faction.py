from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, OuterRef, Subquery

from the_forge.models import FactionBack, Piece


class Command(BaseCommand):
    help = "Backfill Piece.faction from Piece.parent.faction (one-time migration)."

    def handle(self, *args, **opts):
        total = Piece.objects.count()
        already_linked = Piece.objects.filter(faction__isnull=False).count()
        to_link = Piece.objects.filter(faction__isnull=True, parent__isnull=False)
        to_link_count = to_link.count()
        orphan_count = Piece.objects.filter(faction__isnull=True, parent__isnull=True).count()

        self.stdout.write(f"Total pieces:        {total}")
        self.stdout.write(f"Already linked:      {already_linked}")
        self.stdout.write(f"Needing backfill:    {to_link_count}")
        self.stdout.write(f"Orphans (no parent): {orphan_count}")

        parent_faction = FactionBack.objects.filter(pk=OuterRef('parent_id')).values('faction_id')[:1]
        with transaction.atomic():
            updated = to_link.update(faction_id=Subquery(parent_faction))

        post_total = Piece.objects.count()
        post_linked = Piece.objects.filter(faction__isnull=False).count()
        post_unlinked = Piece.objects.filter(faction__isnull=True).count()

        parent_faction_eq = FactionBack.objects.filter(pk=OuterRef('parent_id')).values('faction_id')[:1]
        mismatched = Piece.objects.filter(parent__isnull=False).annotate(
            expected=Subquery(parent_faction_eq)
        ).exclude(faction_id=F('expected')).count()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} pieces."))
        self.stdout.write(f"After: {post_linked}/{post_total} pieces linked to a faction.")
        if post_unlinked:
            self.stdout.write(self.style.WARNING(
                f"{post_unlinked} pieces still have no faction (likely orphan parents)."
            ))
        if mismatched:
            self.stdout.write(self.style.ERROR(
                f"{mismatched} pieces have a faction that doesn't match parent.faction "
                f"-- investigate before proceeding."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("All linked pieces match parent.faction."))

        if post_unlinked == 0 and mismatched == 0:
            self.stdout.write(self.style.SUCCESS("OK -- safe to proceed to Phase 3."))
        else:
            self.stdout.write(self.style.WARNING("NOT SAFE to proceed -- resolve issues above first."))
