"""
Create the public 'Timezone' question template if it doesn't already exist.

A Timezone (TZ) question: the same timezone picker as /availability, pre-filled
with the respondent's profile timezone (or the one their browser reports), whose
answer updates their profile. It carries no choices -- the list comes from
the_databot.services.time_parsing.timezone_choice_groups at render time.

Safe to re-run: an existing public 'Timezone' template is left untouched unless
--update is passed. --update also converts the older Multiple Choice version of
this template (25 hard-coded zones) and clears its choices. Copies of that old
MC question already on surveys no longer update profiles -- only TZ questions
do -- so replace them with a Timezone question to restore that.

    python manage.py seed_timezone_template
"""
from django.core.management.base import BaseCommand

from the_tavern.models import Question, QuestionTemplate

TEMPLATE_NAME = "Timezone"

TEMPLATE_FIELDS = {
    "text": "What is your timezone?",
    "question_type": Question.QuestionType.TIMEZONE,
    "required": True,
    "help_text": "Confirm or update your timezone. This also updates your profile.",
    "display_as_dropdown": False,
    "allow_other": False,
    # Explicitly empty so --update clears the old MC template's 25 zones.
    "choices_data": [],
    "correct_choice_index": None,
    "correct_choice_indices": [],
    "correct_ranking_indices": [],
}


class Command(BaseCommand):
    help = "Create the public 'Timezone' question template if it doesn't exist"

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help=("Overwrite an existing public 'Timezone' template's text "
                  "and settings with the ones defined here.")
        )

    def handle(self, *args, **options):
        existing = QuestionTemplate.objects.filter(name=TEMPLATE_NAME, is_public=True).first()

        if existing and not options['update']:
            self.stdout.write(f"Public '{TEMPLATE_NAME}' template already exists "
                              f"(id {existing.pk}); nothing to do. Pass --update to refresh it.")
            return

        if existing:
            for field, value in TEMPLATE_FIELDS.items():
                setattr(existing, field, value)
            existing.save()
            self.stdout.write(self.style.SUCCESS(
                f"Updated public '{TEMPLATE_NAME}' template (id {existing.pk})."))
            return

        template = QuestionTemplate.objects.create(
            name=TEMPLATE_NAME,
            is_public=True,
            created_by=None,
            **TEMPLATE_FIELDS,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Created public '{TEMPLATE_NAME}' template (id {template.pk})."))
