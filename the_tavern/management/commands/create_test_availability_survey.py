"""
Management command to create a test survey with availability responses for testing the grouping algorithm.
"""
import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from the_gatehouse.models import Profile
from the_tavern.models import Survey, Question, Choice, SurveyResponse, Answer


class Command(BaseCommand):
    help = 'Create a test survey with dummy availability responses for testing grouping'

    def add_arguments(self, parser):
        parser.add_argument(
            '--responses',
            type=int,
            default=40,
            help='Number of survey responses to create (default: 40)'
        )
        parser.add_argument(
            '--waitlist',
            type=int,
            default=10,
            help='Waitlist threshold (responses after this are on waitlist, default: 10)'
        )
        parser.add_argument(
            '--title',
            type=str,
            default='Test Availability Survey',
            help='Survey title'
        )

    def handle(self, *args, **options):
        num_responses = options['responses']
        waitlist_threshold = options['waitlist']
        title = options['title']

        self.stdout.write(f"Creating test survey: {title}")
        self.stdout.write(f"  Responses: {num_responses}")
        self.stdout.write(f"  Waitlist threshold: {waitlist_threshold}")

        # Create the survey
        survey = Survey.objects.create(
            title=title,
            description="Test survey for availability grouping algorithm",
            is_public=True,
            is_active=True,
            has_waitlist=True,
            waitlist_threshold=waitlist_threshold,
        )
        self.stdout.write(self.style.SUCCESS(f"Created survey: {survey.title} (ID: {survey.id})"))

        # Create a TIME_AVAILABILITY question
        ta_question = Question.objects.create(
            survey=survey,
            text="What hours are you available to play? (Select all that apply)",
            question_type=Question.QuestionType.TIME_AVAILABILITY,
            order=1,
            required=True,
            ta_enabled_days=['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
        )
        # Create UTC hour choices (0-23)
        ta_question.create_utc_hour_choices()
        self.stdout.write(f"  Created TIME_AVAILABILITY question with 24 hour choices")

        # Create a DAY_AVAILABILITY question
        da_question = Question.objects.create(
            survey=survey,
            text="Which days are you available?",
            question_type=Question.QuestionType.DAY_AVAILABILITY,
            order=2,
            required=True,
        )
        da_question.create_day_choices()
        self.stdout.write(f"  Created DAY_AVAILABILITY question with 7 day choices")

        # Get or create test profiles
        profiles = self._get_or_create_test_profiles(num_responses)

        # Get choices for questions
        hour_choices = list(ta_question.choices.all().order_by('order'))
        day_choices = list(da_question.choices.all().order_by('order'))

        # Create responses with varied availability patterns
        availability_patterns = self._generate_availability_patterns(num_responses)

        for i, (profile, pattern) in enumerate(zip(profiles, availability_patterns)):
            response_position = i + 1
            timezone_offset = random.choice([-8, -7, -6, -5, -4, 0, 1, 2])  # Various timezones

            response = SurveyResponse.objects.create(
                survey=survey,
                profile=profile,
                response_position=response_position,
                timezone_offset_hours=Decimal(str(timezone_offset)),
            )

            # Create TIME_AVAILABILITY answer
            ta_answer = Answer.objects.create(
                response=response,
                question=ta_question,
            )
            # Select hours based on pattern
            selected_hours = [hour_choices[h] for h in pattern['hours']]
            ta_answer.selected_choices.set(selected_hours)

            # Create DAY_AVAILABILITY answer
            da_answer = Answer.objects.create(
                response=response,
                question=da_question,
            )
            # Select days based on pattern
            selected_days = [day_choices[d] for d in pattern['days']]
            da_answer.selected_choices.set(selected_days)

            is_waitlist = response_position > waitlist_threshold
            status = "(waitlist)" if is_waitlist else ""
            self.stdout.write(
                f"  Response {response_position}: {profile.display_name} - "
                f"{len(pattern['hours'])} hours, {len(pattern['days'])} days {status}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nCreated {num_responses} responses for survey '{title}' (ID: {survey.id})"
        ))
        self.stdout.write(f"  Accepted: {min(num_responses, waitlist_threshold)}")
        self.stdout.write(f"  Waitlist: {max(0, num_responses - waitlist_threshold)}")

        # Show sample grouping command
        self.stdout.write(self.style.WARNING(
            f"\nTo test grouping, run in Django shell:\n"
            f"  from the_tavern.models import Survey\n"
            f"  from the_warroom.models import Tournament\n"
            f"  from the_warroom.services.grouping import GroupingService\n"
            f"  survey = Survey.objects.get(id={survey.id})\n"
            f"  tournament = Tournament.objects.first()  # or create one\n"
            f"  print(f'Groups: {{session.grouped_count}}, Ungrouped: {{session.ungrouped_count}}')"
        ))

    def _get_or_create_test_profiles(self, count):
        """Get or create test profiles for survey responses."""
        profiles = []
        existing = list(Profile.objects.filter(
            display_name__startswith='TestPlayer'
        ).order_by('id')[:count])
        profiles.extend(existing)

        # Create additional profiles if needed
        for i in range(len(existing), count):
            profile, created = Profile.objects.get_or_create(
                discord=f'testplayer{i+1}',
                defaults={
                    'display_name': f'TestPlayer{i+1}',
                }
            )
            profiles.append(profile)

        return profiles[:count]

    def _generate_availability_patterns(self, count):
        """
        Generate varied availability patterns to test grouping algorithm.
        Creates clusters of compatible players with guaranteed overlap.

        Key insight: For the algorithm to find groups, players need:
        - Same hours selected (these are UTC hours 0-23)
        - Same days selected (these filter which days those hours apply to)
        - At least 4 consecutive hours of overlap
        """
        patterns = []

        # Define base patterns with guaranteed 5+ consecutive hours
        # Players in the same cluster will have high overlap
        base_patterns = [
            # Cluster 1: Evening players (18:00-23:00 UTC), Mon-Thu
            {'hours': [18, 19, 20, 21, 22, 23], 'days': [0, 1, 2, 3]},
            # Cluster 2: Afternoon players (14:00-19:00 UTC), Sat-Sun
            {'hours': [14, 15, 16, 17, 18, 19], 'days': [5, 6]},
            # Cluster 3: Morning players (9:00-14:00 UTC), Mon/Wed/Fri
            {'hours': [9, 10, 11, 12, 13, 14], 'days': [0, 2, 4]},
            # Cluster 4: Late afternoon (15:00-20:00 UTC), Tue/Thu/Sat
            {'hours': [15, 16, 17, 18, 19, 20], 'days': [1, 3, 5]},
            # Cluster 5: Night owls (20:00-01:00 UTC), Fri-Sun
            {'hours': [20, 21, 22, 23, 0, 1], 'days': [4, 5, 6]},
        ]

        # Assign players to clusters more evenly for testing
        cluster_sizes = [count // 5 + (1 if i < count % 5 else 0) for i in range(5)]

        player_idx = 0
        for cluster_idx, cluster_size in enumerate(cluster_sizes):
            base = base_patterns[cluster_idx]

            for _ in range(cluster_size):
                # Start with the full base pattern
                hours = set(base['hours'])
                days = set(base['days'])

                # Small variations - but keep at least 5 consecutive hours
                # Only remove 0-1 hours from the edges
                if random.random() > 0.6 and len(hours) > 5:
                    edge = random.choice([min(hours), max(hours)])
                    hours.discard(edge)

                # Sometimes add 1-2 adjacent hours
                if random.random() > 0.5:
                    min_h, max_h = min(hours), max(hours)
                    if min_h > 0:
                        hours.add(min_h - 1)
                    if max_h < 23:
                        hours.add(max_h + 1)

                # Occasionally add an extra day (increases overlap chances)
                if random.random() > 0.6:
                    available_days = [d for d in range(7) if d not in days]
                    if available_days:
                        days.add(random.choice(available_days))

                patterns.append({
                    'hours': sorted(list(hours)),
                    'days': sorted(list(days)),
                })
                player_idx += 1

        # Shuffle to mix clusters
        random.shuffle(patterns)
        return patterns
