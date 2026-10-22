"""
Create the public 'Timezone' question template if it doesn't already exist.

A Multiple Choice question rendered as a dropdown (display_as_dropdown=True),
pre-filled with a curated list of the timezones players actually use. Labels
include the IANA name (or a bare "UTC") in parentheses: _survey_timezone_answer
(views.py) reads it back out of the chosen answer, so a label without one never
sets the response's timezone.

Safe to re-run: an existing public 'Timezone' template is left untouched unless
--update is passed.

    python manage.py seed_timezone_template
"""
from django.core.management.base import BaseCommand

from the_tavern.models import Question, QuestionTemplate

TEMPLATE_NAME = "Timezone"

# Curated US + common gameplay zones. Label is what respondents see and what is
# stored as the Choice text; the IANA name in parentheses keeps it unambiguous.
TIMEZONE_CHOICES = [
    # US
    "US Pacific — PT (America/Los_Angeles)",
    "US Mountain — MT (America/Denver)",
    "US Arizona — MST, no DST (America/Phoenix)",
    "US Central — CT (America/Chicago)",
    "US Eastern — ET (America/New_York)",
    "US Alaska — AKT (America/Anchorage)",
    "US Hawaii — HST (Pacific/Honolulu)",
    # Americas (other)
    "Atlantic — AT (America/Halifax)",
    "Mexico City (America/Mexico_City)",
    "Brazil — São Paulo (America/Sao_Paulo)",
    # Europe
    "UK / Ireland — GMT/BST (Europe/London)",
    "Central Europe — CET/CEST (Europe/Paris)",
    "Eastern Europe — EET/EEST (Europe/Athens)",
    "Moscow — MSK (Europe/Moscow)",
    # Asia / Pacific
    "India — IST (Asia/Kolkata)",
    "Gulf — Dubai (Asia/Dubai)",
    "China (Asia/Shanghai)",
    "Japan — JST (Asia/Tokyo)",
    "Korea — KST (Asia/Seoul)",
    "Singapore / Hong Kong (Asia/Singapore)",
    "Australia Western — AWST (Australia/Perth)",
    "Australia Central — ACST/ACDT (Australia/Adelaide)",
    "Australia Eastern — AEST/AEDT (Australia/Sydney)",
    "New Zealand — NZST/NZDT (Pacific/Auckland)",
    "Coordinated Universal Time (UTC)",
]

TEMPLATE_FIELDS = {
    "text": "What is your timezone?",
    "question_type": Question.QuestionType.MULTIPLE_CHOICE,
    "required": True,
    "help_text": "Select the timezone you most commonly play in.",
    "display_as_dropdown": True,
}


class Command(BaseCommand):
    help = "Create the public 'Timezone' question template if it doesn't exist"

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help=("Overwrite an existing public 'Timezone' template's text, "
                  "settings and choices with the ones defined here.")
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
            existing.choices_data = list(TIMEZONE_CHOICES)
            existing.save()
            self.stdout.write(self.style.SUCCESS(
                f"Updated public '{TEMPLATE_NAME}' template (id {existing.pk})."))
            return

        template = QuestionTemplate.objects.create(
            name=TEMPLATE_NAME,
            is_public=True,
            created_by=None,
            choices_data=list(TIMEZONE_CHOICES),
            **TEMPLATE_FIELDS,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Created public '{TEMPLATE_NAME}' template (id {template.pk})."))
