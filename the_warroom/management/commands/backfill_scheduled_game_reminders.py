from django.core.management.base import BaseCommand

from the_warroom.models import ScheduledGameReminder, Tournament


class Command(BaseCommand):
    """Carry Tournament.match_reminder_minutes over to ScheduledGameReminder rows.

    Migrations are gitignored in this project, so the data move lives here
    rather than in a data migration.

    MUST RUN BEFORE Tournament.match_reminder_minutes is removed -- it is the
    only thing this reads. Once that field is dropped this command stops working
    and can be deleted along with it.
    """

    help = ('Create a ScheduledGameReminder for each tournament that still has '
            'match_reminder_minutes set. Idempotent.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be created without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # `is not None` rather than truthiness: 0 is a real lead time meaning
        # "ping at start time", and only NULL means reminders were off.
        tournaments = (Tournament.objects
                       .filter(match_reminder_minutes__isnull=False)
                       .prefetch_related('reminders')
                       .order_by('pk'))

        created = skipped = 0
        for tournament in tournaments:
            minutes = tournament.match_reminder_minutes
            # Idempotent: re-running must not add a second row at the same lead,
            # which the unique constraint would reject anyway.
            if any(r.match_reminder_minutes == minutes
                   for r in tournament.reminders.all()):
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    f'  would create: {tournament} -> {minutes}m before')
            else:
                # reminder_text is left at its model default: the old field
                # carried no message, so there is nothing to copy.
                ScheduledGameReminder.objects.create(
                    tournament=tournament, match_reminder_minutes=minutes)
            created += 1

        verb = 'would create' if dry_run else 'created'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {created} reminder(s); {skipped} already had one.'))
