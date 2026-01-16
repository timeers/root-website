from django.core.management.base import BaseCommand
from the_gatehouse.models import LikertScale


class Command(BaseCommand):
    help = "Populate default Likert scales for board game and tournament surveys"

    DEFAULT_SCALES = [
        {
            "name": "5-point Agreement",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Strongly Disagree",
            "max_label": "Strongly Agree",
            "labels": {
                "1": "Strongly Disagree",
                "2": "Disagree",
                "3": "Neutral",
                "4": "Agree",
                "5": "Strongly Agree",
            },
        },
        {
            "name": "3-point Agreement",
            "min_value": 1,
            "max_value": 3,
            "min_label": "Disagree",
            "max_label": "Agree",
            "labels": {
                "1": "Disagree",
                "2": "Neutral",
                "3": "Agree",
            },
        },
        {
            "name": "5-point Enjoyment",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Not at all enjoyable",
            "max_label": "Extremely enjoyable",
            "labels": {
                "1": "Not at all enjoyable",
                "2": "Slightly enjoyable",
                "3": "Moderately enjoyable",
                "4": "Very enjoyable",
                "5": "Extremely enjoyable",
            },
        },
        {
            "name": "5-point Complexity",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very Simple",
            "max_label": "Very Complex",
            "labels": {
                "1": "Very Simple",
                "2": "Simple",
                "3": "Moderate",
                "4": "Complex",
                "5": "Very Complex",
            },
        },

        {
            "name": "5-point Length",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Far too long",
            "max_label": "Very short",
            "labels": {
                "1": "Far too long",
                "2": "Somewhat long",
                "3": "Acceptable",
                "4": "Short",
                "5": "Very short",
            },
        },
        {
            "name": "5-point Clarity",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very unclear",
            "max_label": "Very clear",
            "labels": {
                "1": "Very unclear",
                "2": "Unclear",
                "3": "Adequate",
                "4": "Clear",
                "5": "Very clear",
            },
        },
        {
            "name": "5-point Balance",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very Unbalanced",
            "max_label": "Extremely Well Balanced",
            "labels": {
                "1": "Very Unbalanced",
                "2": "Somewhat Unbalanced",
                "3": "Acceptably Balanced",
                "4": "Well Balanced",
                "5": "Extremely Well Balanced",
            },
        },
        {
            "name": "5-point Preparedness",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very unprepared",
            "max_label": "Extremely prepared",
            "labels": {
                "1": "Very unprepared",
                "2": "Somewhat unprepared",
                "3": "Adequately prepared",
                "4": "Well prepared",
                "5": "Extremely prepared",
            },
        },
        {
            "name": "5-point Excellent/Poor",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very poor",
            "max_label": "Excellent",
            "labels": {
                "1": "Very poor",
                "2": "Poor",
                "3": "Acceptable",
                "4": "Good",
                "5": "Excellent",
            },
        },
        {
            "name": "5-point Fairness",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very unfair",
            "max_label": "Very fair",
            "labels": {
                "1": "Very unfair",
                "2": "Somewhat unfair",
                "3": "Neutral",
                "4": "Fair",
                "5": "Very fair",
            },
        },
        {
            "name": "5-point Stress Level",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very stressful",
            "max_label": "Very relaxed",
            "labels": {
                "1": "Very stressful",
                "2": "Stressful",
                "3": "Neutral",
                "4": "Relaxed",
                "5": "Very relaxed",
            },
        },
        {
            "name": "5-point Confidence",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very unsure",
            "max_label": "Very confident",
            "labels": {
                "1": "Very unsure",
                "2": "Unsure",
                "3": "Neutral",
                "4": "Confident",
                "5": "Very confident",
            },
        },
        {
            "name": "5-point Likeliness",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Very unlikely",
            "max_label": "Very likely",
            "labels": {
                "1": "Very unlikely",
                "2": "Unlikely",
                "3": "Neutral",
                "4": "Likely",
                "5": "Very likely",
            },
        },
        {
            "name": "5-point Would/Would Not",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Definitely would not",
            "max_label": "Definitely would",
            "labels": {
                "1": "Definitely would not",
                "2": "Probably would not",
                "3": "Might or might not",
                "4": "Probably would",
                "5": "Definitely would",
            },
        },
        {
            "name": "7-point Better/Worse",
            "min_value": 1,
            "max_value": 7,
            "min_label": "Much worse",
            "max_label": "Much better",
            "labels": {
                "1": "Much worse",
                "2": "Worse",
                "3": "Slightly worse",
                "4": "About the same",
                "5": "Slightly better",
                "6": "Better",
                "7": "Much better",
            },
        },
        {
            "name": "5-point Frequency",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Never",
            "max_label": "Very often",
            "labels": {
                "1": "Never",
                "2": "Rarely",
                "3": "Sometimes",
                "4": "Often",
                "5": "Very often",
            },
        },
        {
            "name": "5-point Importance",
            "min_value": 1,
            "max_value": 5,
            "min_label": "Not important",
            "max_label": "Extremely important",
            "labels": {
                "1": "Not important",
                "2": "Slightly important",
                "3": "Moderately important",
                "4": "Very important",
                "5": "Extremely important",
            },
        },
    ]

    def handle(self, *args, **options):
        created = 0
        skipped = 0

        for scale_data in self.DEFAULT_SCALES:
            scale, was_created = LikertScale.objects.get_or_create(
                name=scale_data["name"],
                defaults=scale_data,
            )

            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {scale.name}"))
            else:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Exists: {scale.name}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {created} created, {skipped} already existed."
            )
        )
