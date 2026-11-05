from django.core.management.base import BaseCommand

from the_warroom.models import Round, Stage, Tournament


class Command(BaseCommand):
    help = (
        "Give every tournament its hidden default stage and every stage its default "
        "round. Repairs data left behind by deleting a tournament's last stage or a "
        "stage's last round before that was guarded (see the post_delete signals). "
        "Tournaments with legacy rounds attached directly to them (stage=None) are "
        "skipped, since a new stage would change which rounds their pages show. "
        "Dry-run by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually create the missing stages/rounds. Without this flag the command only reports.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        verb = 'Created' if apply else 'Would create'

        # Both lists are taken before any writes, so a stage created for a
        # stageless tournament (which comes with its own round) is not re-listed.
        stageless = list(Tournament.objects.filter(stages__isnull=True).order_by('pk'))
        roundless = list(
            Stage.objects.filter(rounds__isnull=True)
            .select_related('tournament').order_by('pk')
        )

        if not stageless and not roundless:
            self.stdout.write(self.style.SUCCESS('Every tournament has a stage and every stage a round.'))
            return

        fixed_tournaments = skipped = fixed_stages = 0

        for tournament in stageless:
            # Per-tournament exists() rather than filtering on
            # rounds__stage__isnull, which would also match tournaments with no
            # rounds at all (LEFT JOIN) and skip exactly the ones to fix.
            if Round.objects.filter(tournament=tournament, stage__isnull=True).exists():
                skipped += 1
                self.stdout.write(
                    f'  Skipped tournament {tournament.pk} ({tournament.slug}): '
                    f'has legacy rounds with no stage'
                )
                continue
            if apply:
                tournament.ensure_default_stage()
            fixed_tournaments += 1
            self.stdout.write(
                f'  {verb} default stage + round for tournament {tournament.pk} ({tournament.slug})'
            )

        for stage in roundless:
            if apply:
                stage.ensure_default_round()
            fixed_stages += 1
            self.stdout.write(
                f'  {verb} default round for stage {stage.pk} ({stage.tournament.slug} / {stage.slug})'
            )

        summary = (
            f'{verb} {fixed_tournaments} default stage(s) and {fixed_stages} default round(s); '
            f'skipped {skipped} tournament(s).'
        )
        if apply:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary + ' Dry run: pass --apply to write.'))
