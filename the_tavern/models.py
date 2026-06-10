import calendar

from django.core.validators import MaxValueValidator, MinValueValidator
from django.urls import reverse
from django.db.models import Q, Avg, Count, Max, Min, Exists, OuterRef, Case, When, Value, F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import models
from django.apps import apps
from django.utils import timezone 
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from the_gatehouse.models import DiscordGuild, Profile
from the_keep.models import Post
from the_warroom.models import Game

#  Comments are not currently used
#  Discussions should be kept in Discord on the linked threads.
class Comment(models.Model):
    public = models.BooleanField(default=False)
    body = models.CharField(max_length=300)
    date_posted = models.DateTimeField(default=timezone.now)
    class Meta:
        abstract = True

class PostComment(Comment):
    type = "post"
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='post_comments')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    def __str__(self):
        return f"{self.player.name}: {self.body[:30]}"

class GameComment(Comment):
    type = "game"
    player = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='game_comments')
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='comments')
    class Meta:
        ordering = ['-date_posted']
    def __str__(self):
        return f"{self.player.name}: {self.body[:30]}"

# Survey Models
class SurveyQuerySet(models.QuerySet):
    def not_available(self):
        now = timezone.now()
        return self.filter(
            Q(is_active=False) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now)
        )
    
    def is_available(self):
        now = timezone.now()
        return self.exclude(
            Q(is_active=False) |
            Q(start_date__gt=now) |
            Q(end_date__lt=now)
        )
    
    def user_has_invite(self, profile):
        """
        Return surveys where the user has an invite.
        Checks:
        - Direct invite (invited_players)
        - Series membership (player is a TournamentPlayer for the series)
        - Stage membership (player is a participant in the stage)
        - Guild membership
        """
        invited_qs = self.model.objects.filter(invited_players=profile).values('pk')
        series_qs = self.model.objects.filter(series__tournament_players__profile=profile).values('pk')
        stage_qs = self.model.objects.filter(stage__participants__tournament_player__profile=profile).values('pk')
        guild_qs = self.model.objects.filter(guild__members=profile).values('pk')

        return self.filter(
            Q(is_public=False) & (
                Q(pk__in=invited_qs) |
                Q(pk__in=series_qs) |
                Q(pk__in=stage_qs) |
                Q(pk__in=guild_qs)
            )
        ).exclude(created_by=profile)
    
    def annotate_for_user(self, profile, owned=False):
        """Annotate surveys with user-specific status fields for list display.

        When ``owned=True``, skip the membership-check subqueries used to
        compute ``can_take_survey`` — appropriate for querysets the caller has
        already filtered to surveys created by ``profile``.
        """
        SurveyResponse = apps.get_model('the_tavern', 'SurveyResponse')
        is_banned = profile.group == Profile.GroupChoices.BANNED

        user_response_exists = Exists(
            SurveyResponse.objects.filter(survey=OuterRef('pk'), profile=profile)
        )

        is_survey_full = Case(
            When(
                limit_responses=True, has_waitlist=False,
                waitlist_threshold__isnull=False,
                response_count__gte=F('waitlist_threshold'),
                then=Value(True)
            ),
            default=Value(False),
            output_field=models.BooleanField()
        )

        if owned:
            return self.annotate(
                response_count=Count('responses'),
                user_has_responded=user_response_exists,
                can_take_survey=Value(False, output_field=models.BooleanField()) if is_banned else (
                    Case(
                        When(allow_multiple_responses=True, then=Value(True)),
                        When(user_has_responded=False, then=Value(True)),
                        default=Value(False),
                        output_field=models.BooleanField(),
                    )
                ),
                is_survey_full=is_survey_full,
            )

        user_invited = Exists(
            self.model.invited_players.through.objects.filter(
                survey_id=OuterRef('pk'), profile_id=profile.pk
            )
        )
        user_in_guild = Exists(
            Profile.guilds.through.objects.filter(
                profile_id=profile.pk, discordguild_id=OuterRef('guild_id')
            )
        )
        user_in_series = Exists(
            self.model.objects.filter(
                pk=OuterRef('pk'), series__tournament_players__profile=profile
            )
        )
        user_in_stage = Exists(
            self.model.objects.filter(
                pk=OuterRef('pk'), stage__participants__tournament_player__profile=profile
            )
        )

        return self.annotate(
            response_count=Count('responses'),
            user_has_responded=user_response_exists,
            user_is_invited=user_invited,
            user_in_guild=user_in_guild,
            user_in_series=user_in_series,
            user_in_stage=user_in_stage,
            can_respond=Q(allow_multiple_responses=True) | Q(user_has_responded=False),
            can_take_survey=Case(
                When(Q(can_respond=False), then=Value(False)),
                When(Q(is_public=True), then=Value(True)),
                When(Q(user_is_invited=True), then=Value(True)),
                When(Q(user_in_guild=True), then=Value(True)),
                When(Q(stage__isnull=False, user_in_stage=True), then=Value(True)),
                When(Q(stage__isnull=False, user_in_stage=False), then=Value(False)),
                When(Q(series__isnull=False, user_in_series=True), then=Value(True)),
                When(Q(series__isnull=False, user_in_series=False), then=Value(False)),
                default=Value(False),
                output_field=models.BooleanField()
            ) if not is_banned else Value(False, output_field=models.BooleanField()),
            is_survey_full=is_survey_full,
        )

    def can_see_results(self, profile):
        now = timezone.now()
        return self.filter(
            Q(created_by=profile) |
            Q(show_results_to_respondents=True, responses__profile=profile) |
            Q(show_results_on_close=True, end_date__lt=now, responses__isnull=False)
        ).distinct()


class Survey(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    slug = models.SlugField(unique=True, max_length=250, null=True, blank=True)
    
    # Survey Owner Objects
    post = models.ForeignKey('the_keep.Post', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Post that this survey is about.")
    series = models.ForeignKey('the_warroom.Tournament', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament or Series that this survey is about.")
    stage = models.ForeignKey('the_warroom.Stage', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament Stage that this survey is about.")

    is_public = models.BooleanField(default=True, help_text="If False, only certain players can access.")    
    is_pinned = models.BooleanField(default=False, help_text="If True, will appear at the top of lists.")    

    invited_players = models.ManyToManyField(Profile, blank=True, related_name='survey_invites', help_text="Players invited to take this survey.")
    guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="Players in this guild will be able to take this survey.")

    is_active = models.BooleanField(default=True, help_text="Whether this survey is currently accepting responses")
    created_at = models.DateTimeField(auto_now_add=True)
    
    start_date = models.DateTimeField(null=True, blank=True, help_text="When survey becomes available")
    end_date = models.DateTimeField(null=True, blank=True, help_text="When survey closes")
    
    allow_multiple_responses = models.BooleanField(default=False, help_text="Allow players to submit multiple times")
    allow_edit_responses = models.BooleanField(default=False, help_text="Allow players to edit their responses while survey is open")
    show_results_to_respondents = models.BooleanField(default=False, help_text="Allow respondents to view results")
    show_results_on_close = models.BooleanField(default=True, help_text="Make results public when closed")
    show_correct_answers = models.BooleanField(default=False, help_text="Show correct answers to respondents after submission")
    show_score_summary = models.BooleanField(default=False, help_text="Show score summary (e.g., '7/10 correct') after submission")
    is_quiz = models.BooleanField(default=False, help_text="Enable quiz mode to set correct answers")
    
    limit_responses = models.BooleanField(default=False, help_text="Set a cap on the number of accepted responses.")
    waitlist_threshold = models.IntegerField(null=True, blank=True)
    has_waitlist = models.BooleanField(default=False)
    is_registration = models.BooleanField(default=False, help_text='Display tournament rules with a required agreement checkbox.')

    created_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_surveys')

    objects = SurveyQuerySet.as_manager()

    class Meta:
        ordering = ['-is_pinned', '-created_at', 'id']
        verbose_name = 'Survey'
        verbose_name_plural = 'Surveys'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('survey-detail', kwargs={'slug': self.slug})

    def get_settings_url(self):
        return reverse('survey-settings', kwargs={'slug': self.slug})

    def is_available(self):
        """Check if survey is currently available to take"""
        now = timezone.now()
        if not self.is_active:
            return False
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True

    def has_started(self):
        """Check if survey has started"""
        now = timezone.now()
        if self.start_date and now < self.start_date:
            return False
        return True

    def has_ended(self):
        """Check if survey has ended"""
        now = timezone.now()
        if self.end_date and now > self.end_date:
            return True
        return False

    def is_full(self):
        """Check if survey has reached its response limit (hard limit, no waitlist)"""
        if not self.limit_responses or self.waitlist_threshold is None:
            return False
        if self.has_waitlist:
            return False
        return self.responses.count() >= self.waitlist_threshold

    def get_sections_with_questions(self):
        """Return ordered sections with their visible questions.
        Questions without a section are grouped into an implicit first section.
        Returns list of dicts: [{section: SurveySection|None, questions: QuerySet}, ...]
        """
        visible_questions = self.questions.filter(is_hidden=False)
        unsectioned = visible_questions.filter(section__isnull=True)
        sections_data = []

        if unsectioned.exists():
            sections_data.append({
                'section': None,
                'questions': unsectioned,
            })

        for section in self.sections.all():
            section_questions = visible_questions.filter(section=section)
            if section_questions.exists():
                sections_data.append({
                    'section': section,
                    'questions': section_questions,
                })

        return sections_data

    def question_count(self):
        """Get total number of visible (non-hidden) questions"""
        return self.questions.filter(is_hidden=False).count()

    def response_count(self):
        """Get total number of responses"""
        return self.responses.count()

    def has_user_responded(self, user_profile):
        """Check if a specific user has already responded"""
        if not user_profile:
            return False
        return self.responses.filter(profile=user_profile).exists()

    def can_edit_response(self, user_profile):
        """Check if a user can edit their response"""
        if not user_profile or not self.allow_edit_responses:
            return False
        if not self.is_available():
            return False
        return self.has_user_responded(user_profile)

    def can_edit_survey(self, user_profile):
        """Check if a user can edit the survey"""
        if not user_profile:
            return False
        return user_profile.admin or user_profile == self.created_by

    def can_view_survey(self, user_profile):
        """Check if a user can view the survey details"""
        if not user_profile:
            return False

        # Block banned users
        if user_profile.group == Profile.GroupChoices.BANNED:
            return False

        # Public survey
        if self.is_public:
            return True

        # Owner of the survey and admin can view
        if self.created_by == user_profile or user_profile.admin:
            return True

        # Explicit invite
        if self.invited_players.filter(pk=user_profile.pk).exists():
            return True

        # Guild-based access
        if self.guild and user_profile.guilds.filter(pk=self.guild.pk).exists():
            return True

        # If Private with Stage, only allow Stage participants
        if self.stage:
            if self.stage.participants.filter(tournament_player__profile=user_profile).exists():
                return True
            else:
                return False

        # If Private with Series, only allow Series players
        if self.series:
            if self.series.tournament_players.filter(profile=user_profile).exists():
                return True
            else:
                return False

        return False


    def can_take_survey(self, user_profile):
        """Check if a user can take the survey
        NOTE: If this logic is changed, update the annotation 
        in survey_list_view as well!
        """
        if not user_profile:
            return False

        # Block banned users
        if user_profile.group == Profile.GroupChoices.BANNED:
            return False

        # Survey must be open
        if not self.is_available():
            return False

        # Check if survey has hit its hard response limit (no waitlist)
        if self.is_full():
            return False

        # Respect multiple response rules
        if self.has_user_responded(user_profile) and not self.allow_multiple_responses:
            return False

        # Public is open to everyone
        if self.is_public:
            return True

        # If private allow invited players
        if self.invited_players.filter(pk=user_profile.pk).exists():
            return True

        # If private allow guild members
        if self.guild and user_profile.guilds.filter(pk=self.guild.pk).exists():
            return True

        # If Private with Stage, only allow Stage participants
        if self.stage:
            if self.stage.participants.filter(tournament_player__profile=user_profile).exists():
                return True
            else:
                return False

        # If Private with Series, only allow Series players
        if self.series:
            if self.series.tournament_players.filter(profile=user_profile).exists():
                return True
            else:
                return False

        return False

    def can_see_results(self, user_profile):
        if not user_profile:
            return False
        
        # Admin and creator can see results
        if user_profile.admin or user_profile == self.created_by:
            return True
        
        # If show results is true and the user has responded
        if self.has_user_responded(user_profile) and self.show_results_to_respondents:
            return True
        
        # If show results on close check that there are responses and the survey is not active
        if not self.is_available() and self.response_count() > 0 and self.show_results_on_close:
            return True

        return False

    def get_accepted_response_count(self):
        """Count responses within threshold (not on waitlist)"""
        if not self.limit_responses or self.waitlist_threshold is None:
            return self.responses.count()
        return self.responses.filter(response_position__lte=self.waitlist_threshold).count()

    def get_available_response_count(self):
        """Count remaining spots before response limit"""
        if not self.limit_responses or self.waitlist_threshold is None:
            return 0
        available_spots = self.waitlist_threshold - self.responses.filter(response_position__lte=self.waitlist_threshold).count()
        return max(available_spots, 0)

    def get_waitlisted_response_count(self):
        """Count responses on waitlist"""
        if not self.limit_responses or not self.has_waitlist or self.waitlist_threshold is None:
            return 0
        return self.responses.filter(response_position__gt=self.waitlist_threshold).count()

    def is_response_waitlisted(self, response):
        """Check if a specific response is on waitlist"""
        if not self.limit_responses or not self.has_waitlist or self.waitlist_threshold is None:
            return False
        return response.response_position > self.waitlist_threshold

    def get_next_response_position(self):
        """Calculate position for next response (1-indexed, chronological)"""
        from django.db.models import Max
        # Count all existing non-deleted responses
        max_position = self.responses.aggregate(Max('response_position'))['response_position__max']
        return (max_position or 0) + 1

    def get_score_stats(self):
        """Get aggregate score statistics for this survey"""

        stats = self.responses.filter(
            score_total__gt=0  # Only responses with scoreable questions
        ).aggregate(
            avg_score=Avg('relative_score'),
            avg_score_required=Avg('required_score'),
            avg_score_optional=Avg('optional_score'),

            avg_correct=Avg('score_correct'),
            avg_correct_required=Avg('score_correct_required'),
            avg_correct_optional=Avg('score_correct_optional'),

            avg_total=Avg('score_total'),
            avg_total_required=Avg('score_total_required'),
            avg_total_optional=Avg('score_total_optional'),

            max_score=Max('relative_score'),
            min_score=Min('relative_score'),
            max_required_score=Max('required_score'),
            min_required_score=Min('required_score'),
            max_optional_score=Max('optional_score'),
            min_optional_score=Min('optional_score'),
            response_count=Count('id')
        )

        # Return with defaults if no responses
        return {
            'avg_score': round(stats['avg_score'], 2) if stats['avg_score'] else 0,
            'avg_score_required': round(stats['avg_score_required'], 2) if stats['avg_score_required'] else 0,
            'avg_score_optional': round(stats['avg_score_optional'], 2) if stats['avg_score_optional'] else 0,            
            
            'avg_correct': round(stats['avg_correct'], 2) if stats['avg_correct'] else 0,
            'avg_correct_required': round(stats['avg_correct_required'], 2) if stats['avg_correct_required'] else 0,
            'avg_correct_optional': round(stats['avg_correct_optional'], 2) if stats['avg_correct_optional'] else 0,

            'avg_total': round(stats['avg_total'], 2) if stats['avg_total'] else 0,
            'avg_total_required': round(stats['avg_total_required'], 2) if stats['avg_total_required'] else 0,
            'avg_total_optional': round(stats['avg_total_optional'], 2) if stats['avg_total_optional'] else 0,

            'max_score': stats['max_score'] or 0,
            'min_score': stats['min_score'] or 0,
            'response_count': stats['response_count'] or 0,
        }

    def has_availability_questions(self):
        """Check if survey has TIME_AVAILABILITY or DAY_AVAILABILITY questions"""
        from .models import Question
        return self.questions.filter(
            question_type__in=[
                Question.QuestionType.TIME_AVAILABILITY,
                Question.QuestionType.DAY_AVAILABILITY
            ]
        ).exists()


class LikertScale(models.Model):
    name = models.CharField(max_length=100, help_text="Name for this scale (e.g., '5-point Agreement')")
    min_value = models.IntegerField(default=1, validators=[MinValueValidator(0), MaxValueValidator(10)], help_text="Minimum value (0-10)")
    max_value = models.IntegerField(default=5, validators=[MinValueValidator(0), MaxValueValidator(10)], help_text="Maximum value (0-10)")
    min_label = models.CharField(max_length=50, default="Strongly Disagree", blank=False, help_text="Label for minimum value")
    max_label = models.CharField(max_length=50, default="Strongly Agree", blank=False, help_text="Label for maximum value")
    labels = models.JSONField(default=dict, null=True, blank=True, help_text='Optional labels for each value (e.g., {"1": "Poor", "5": "Excellent"})')
    created_by = models.ForeignKey(Profile, null=True, blank=True, on_delete=models.CASCADE, related_name='custom_scales', help_text="User who created this scale. Null = default/system scale.")

    class Meta:
        ordering = ['name']
        verbose_name = 'Scale'
        verbose_name_plural = 'Scales'

    def __str__(self):
        return f"{self.name} ({self.min_value}-{self.max_value})"

    def clean(self):
        """Validate that required fields are filled"""
        from django.core.exceptions import ValidationError

        # Validate required labels
        if not self.min_label or not self.min_label.strip():
            raise ValidationError({'min_label': 'Minimum label is required.'})
        if not self.max_label or not self.max_label.strip():
            raise ValidationError({'max_label': 'Maximum label is required.'})

        # Validate min/max values
        if self.min_value >= self.max_value:
            raise ValidationError({'max_value': 'Maximum value must be greater than minimum value.'})

        if (self.max_value - self.min_value + 1) > 12:
            raise ValidationError({'max_value': 'Scale cannot exceed 12 points.'})

        # Validate labels JSON field if provided
        if self.labels:
            if not isinstance(self.labels, dict):
                raise ValidationError({'labels': 'Labels must be a dictionary/object.'})

            # Validate that keys are integers within the scale range
            for key, value in self.labels.items():
                try:
                    key_int = int(key)
                    if key_int < self.min_value or key_int > self.max_value:
                        raise ValidationError({
                            'labels': f'Label key {key} is outside the scale range ({self.min_value}-{self.max_value}).'
                        })
                except (ValueError, TypeError):
                    raise ValidationError({'labels': f'Label key "{key}" must be a number.'})

    def get_display_labels(self):
        if self.labels:
            return {
                value: _(label)
                for value, label in self.labels.items()
            }

        return {
            self.min_value: _(self.min_label),
            self.max_value: _(self.max_label),
        }


# Day configuration constants for TIME_AVAILABILITY questions
TA_DAY_CODES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
TA_DAY_LABELS = {
    'mon': 'Monday', 'tue': 'Tuesday', 'wed': 'Wednesday',
    'thu': 'Thursday', 'fri': 'Friday', 'sat': 'Saturday', 'sun': 'Sunday'
}

def get_default_ta_days():
    """Return all days enabled by default for TIME_AVAILABILITY questions"""
    return TA_DAY_CODES.copy()


# A Survey can optionally be divided into sections (pages)
# Questions belonging to a section are displayed together on one page
class SurveySection(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=200, blank=True, help_text="Section title (optional)")
    description = models.TextField(blank=True, help_text="Section description (optional)")
    order = models.PositiveIntegerField(default=0, help_text="Display order in survey")

    class Meta:
        ordering = ['survey', 'order', 'id']
        verbose_name = 'Survey Section'
        verbose_name_plural = 'Survey Sections'

    def __str__(self):
        return f"{self.survey.title} - Section {self.order}: {self.title or '(Untitled)'}"


# Each Survey is made up of one or more questions
# The Type determines what kind of question it is
class Question(models.Model):
    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = 'MC', 'Multiple Choice'
        MULTIPLE_SELECTION = 'MS', 'Multiple Selection'
        OPEN_ENDED = 'OE', 'Open Ended'
        RANKING = 'RK', 'Ranking'
        SCALE = 'LK', 'Scale'
        BOOLEAN = 'YN', 'Yes/No'
        DATE = 'DA', 'Date'
        TIME = 'TI', 'Time'
        DATETIME = 'DT', 'Date & Time'
        TIME_AVAILABILITY = 'TA', 'Time Availability'
        DAY_AVAILABILITY = 'DY', 'Day Availability'
        NUMERIC = 'NU', 'Numeric'

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='questions')
    section = models.ForeignKey(
        SurveySection, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='questions',
        help_text="Section this question belongs to (optional)"
    )
    text = models.TextField(help_text="The question text")
    likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL, help_text="Required for Likert Scale questions")
    question_type = models.CharField(max_length=2, choices=QuestionType.choices)
    order = models.PositiveIntegerField(default=0, help_text="Display order in survey")
    required = models.BooleanField(default=True, help_text="Is this question required?")
    help_text = models.CharField(max_length=300, blank=True, help_text="Optional help text shown to users")
    is_hidden = models.BooleanField(default=False, help_text="Hidden questions preserve data but are not shown to respondents")
    allow_other = models.BooleanField(default=False, help_text="Allow respondents to select 'Other' and type a custom response (MC/MS only)")

    # Post-based choices configuration
    class PostSelectionMode(models.TextChoices):
        ALL_OFFICIAL = 'all_official', 'All Official'
        INDIVIDUAL = 'individual', 'Select Individual'

    post_component = models.CharField(
        max_length=20,
        null=True, blank=True,
        help_text="Component type for Post-based choices (e.g., Faction, Map)"
    )
    post_selection_mode = models.CharField(
        max_length=20,
        choices=PostSelectionMode.choices,
        null=True, blank=True,
        help_text="How Posts are selected as choices"
    )

    # Day configuration for TIME_AVAILABILITY questions
    ta_enabled_days = models.JSONField(
        default=get_default_ta_days,
        blank=True,
        help_text="Days of week for TIME_AVAILABILITY questions"
    )

    # Correct answer fields for quiz functionality
    correct_choice = models.ForeignKey(
        'Choice', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='correct_for_questions',
        help_text="Correct choice for MC/YN questions"
    )
    correct_post = models.ForeignKey(
        'the_keep.Post', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='correct_for_questions',
        help_text="Correct post for Post-based MC questions"
    )
    correct_choices = models.ManyToManyField(
        'Choice', blank=True, related_name='correct_for_ms_questions',
        help_text="Correct choices for MS questions (all must match)"
    )
    correct_posts = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='correct_for_ms_questions',
        help_text="Correct posts for Post-based MS questions (all must match)"
    )
    correct_numeric = models.IntegerField(
        null=True, blank=True,
        help_text="Correct answer for NU/LK questions (exact match)"
    )
    correct_ranking = models.JSONField(
        null=True, blank=True,
        help_text="Ordered list of choice IDs for correct ranking"
    )
    correct_ranking_posts = models.JSONField(
        null=True, blank=True,
        help_text="Ordered list of post IDs for correct ranking"
    )

    class Meta:
        ordering = ['survey', 'section__order', 'order', 'id']
        verbose_name = 'Question'
        verbose_name_plural = 'Questions'

    def __str__(self):
        return f"{self.survey.title} - Q{self.order}: {self.text[:50]}"

    def create_utc_hour_choices(self):
        """Create 24 UTC hour choices for TIME_AVAILABILITY questions"""
        if self.question_type != self.QuestionType.TIME_AVAILABILITY:
            return

        # Only create if choices don't already exist
        if self.choices.exists():
            return

        # Create choices for hours 0-23 (UTC)
        for hour in range(24):
            Choice.objects.create(
                question=self,
                text=str(hour),
                order=hour
            )


    def create_day_choices(self):
        """Create 7 day choices for DAY_AVAILABILITY questions"""
        if self.question_type != self.QuestionType.DAY_AVAILABILITY:
            return

        # Only create if choices don't already exist
        if self.choices.exists():
            return

        for order, day_name in enumerate(calendar.day_name):
            Choice.objects.create(
                question=self,
                text=day_name,   # "Monday", "Tuesday", ...
                order=order
            )

    def clean(self):
        """Validate that question type matches required fields"""
        super().clean()
        if self.question_type == self.QuestionType.SCALE and not self.likert_scale:
            raise ValidationError("Scale questions require a Likert Scale to be selected.")

        # Only validate choices if the question has been saved (has a primary key)
        # Skip validation for "All Official" mode since choices are loaded dynamically
        if self.pk and not self.uses_all_official_posts():
            if self.question_type in [self.QuestionType.MULTIPLE_CHOICE, self.QuestionType.MULTIPLE_SELECTION,
                                      self.QuestionType.BOOLEAN, self.QuestionType.RANKING,
                                      self.QuestionType.TIME_AVAILABILITY, self.QuestionType.DAY_AVAILABILITY]:
                if not self.choices.exists():
                    raise ValidationError(f"{self.get_question_type_display()} questions require at least one choice.")

    def is_post_based(self):
        """Check if this question uses Posts as choices"""
        return bool(self.post_component)

    def uses_all_official_posts(self):
        """Check if dynamically loading all official Posts"""
        return self.post_component and self.post_selection_mode == self.PostSelectionMode.ALL_OFFICIAL

    def get_post_choices(self):
        """Get Posts for 'all_official' mode"""
        if not self.uses_all_official_posts():
            return None
        Post = apps.get_model('the_keep', 'Post')
        return Post.objects.filter(
            component=self.post_component,
            official=True,
            status__lte=4
        ).order_by('title')

    def has_responses(self):
        """Check if this question has any associated answers"""
        return self.answer_set.exists()

    def get_visible_choices(self):
        """Get choices that should be shown to respondents"""
        return self.choices.filter(is_hidden=False)

    def get_enabled_days_display(self):
        """Return comma-separated list of enabled day names for TIME_AVAILABILITY questions"""
        if self.question_type != self.QuestionType.TIME_AVAILABILITY:
            return ""
        days = self.ta_enabled_days if self.ta_enabled_days else TA_DAY_CODES

        # Check for all days
        all_days = set(TA_DAY_CODES)
        # Check for weekdays (Monday-Friday)
        weekdays = ['mon', 'tue', 'wed', 'thu', 'fri']
        # Check for weekends (Saturday-Sunday)
        weekends = ['sat', 'sun']

        days_set = set(days)
        weekdays_set = set(weekdays)
        weekends_set = set(weekends)

        # If all 7 days are selected
        if days_set == all_days:
            return "All Days"
        # If exactly weekdays are selected
        elif days_set == weekdays_set:
            return "Weekdays"
        # If exactly weekends are selected
        elif days_set == weekends_set:
            return "Weekends"
        # Otherwise, show individual day names with proper formatting
        else:
            day_names = [TA_DAY_LABELS.get(d, d) for d in days]
            if len(day_names) == 1:
                return day_names[0]
            elif len(day_names) == 2:
                return f"{day_names[0]} and {day_names[1]}"
            else:
                return ", ".join(day_names[:-1]) + f", and {day_names[-1]}"

    def has_correct_answer(self):
        """Check if this question has a correct answer defined"""
        qtype = self.question_type

        if qtype in [self.QuestionType.MULTIPLE_CHOICE, self.QuestionType.BOOLEAN]:
            if self.is_post_based():
                return self.correct_post is not None
            return self.correct_choice is not None

        elif qtype == self.QuestionType.MULTIPLE_SELECTION:
            if self.is_post_based():
                return self.correct_posts.exists()
            return self.correct_choices.exists()

        elif qtype in [self.QuestionType.NUMERIC, self.QuestionType.SCALE]:
            return self.correct_numeric is not None

        elif qtype == self.QuestionType.RANKING:
            if self.is_post_based():
                return bool(self.correct_ranking_posts)
            return bool(self.correct_ranking)

        return False

    def get_correct_answer_display(self):
        """Return human-readable correct answer"""
        qtype = self.question_type

        if qtype in [self.QuestionType.MULTIPLE_CHOICE, self.QuestionType.BOOLEAN]:
            if self.is_post_based() and self.correct_post:
                return self.correct_post.title
            elif self.correct_choice:
                return self.correct_choice.get_display_text()

        elif qtype == self.QuestionType.MULTIPLE_SELECTION:
            if self.is_post_based():
                posts = self.correct_posts.all()
                if posts:
                    return ", ".join([p.title for p in posts])
            else:
                choices = self.correct_choices.all()
                if choices:
                    return ", ".join([c.get_display_text() for c in choices])

        elif qtype in [self.QuestionType.NUMERIC, self.QuestionType.SCALE]:
            if self.correct_numeric is not None:
                return str(self.correct_numeric)

        elif qtype == self.QuestionType.RANKING:
            if self.is_post_based() and self.correct_ranking_posts:
                Post = apps.get_model('the_keep', 'Post')
                posts = Post.objects.filter(id__in=self.correct_ranking_posts)
                post_map = {p.id: p.title for p in posts}
                return ", ".join([f"{i+1}. {post_map.get(pid, '?')}" for i, pid in enumerate(self.correct_ranking_posts)])
            elif self.correct_ranking:
                choices = self.choices.filter(id__in=self.correct_ranking)
                choice_map = {c.id: c.get_display_text() for c in choices}
                return ", ".join([f"{i+1}. {choice_map.get(cid, '?')}" for i, cid in enumerate(self.correct_ranking)])

        return None


# For multiple choice questions they will have multiple choices
class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=200, blank=True)  # blank=True since Post can provide text
    order = models.PositiveIntegerField(default=0)
    post = models.ForeignKey(
        'the_keep.Post', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='survey_choices',
        help_text="Link to a Post - if set, displays Post title/icon"
    )
    is_hidden = models.BooleanField(default=False, help_text="Hidden choices preserve data but are not shown to respondents")

    class Meta:
        ordering = ['question', 'order', 'id']
        verbose_name = 'Choice'
        verbose_name_plural = 'Choices'

    def __str__(self):
        return f"{self.question.text[:30]} - {self.get_display_text()}"

    def get_display_text(self):
        """Return the text to display for this choice"""
        return self.post.title if self.post else self.text

    def clean(self):
        """Validate that either text or post is provided"""
        super().clean()
        if not self.text and not self.post:
            raise ValidationError("Choice must have either text or a linked Post.")

    def has_responses(self):
        """Check if this choice has been selected in any answers"""
        return (
            self.single_answers.exists() or
            self.multiple_answers.exists() or
            self.rankedanswer_set.exists()
        )


# A user's response to a survey is stored here
class SurveyResponse(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
    profile = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='survey_responses')
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    timezone_offset_hours = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="UTC offset in hours at submission time (e.g., -5.0, 5.5, -3.5)"
    )

    response_position = models.IntegerField(default=0)

    score_correct = models.IntegerField(default=0)
    score_total = models.IntegerField(default=0)
    score_correct_required = models.IntegerField(default=0)
    score_total_required = models.IntegerField(default=0)
    score_correct_optional = models.IntegerField(default=0)
    score_total_optional = models.IntegerField(default=0)
    relative_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    required_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    optional_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)



    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Survey Response'
        verbose_name_plural = 'Survey Responses'
        indexes = [
            models.Index(fields=['survey', 'profile']),
        ]

    def __str__(self):
        user_display = self.profile.name if self.profile else "Anonymous"
        return f"{user_display} → {self.survey.title}"

    def get_absolute_url(self):
        return reverse('survey-user-response', kwargs={
            'slug': self.survey.slug,
            'response_id': self.pk,
        })

    @property
    def was_edited(self):
        """Check if response was edited after initial submission"""
        # Compare with 1 second tolerance to account for save timing
        return (self.updated_at - self.submitted_at).total_seconds() > 1

    def can_view_response(self, user_profile):
        if not user_profile:
            return False
        # Admin, survey owner or respondent can view
        if user_profile == self.profile or user_profile.admin or user_profile == self.survey.created_by:
            return True
        return False

    def calculate_score(self):
        """Calculate the score for this response"""
        score_correct = 0
        score_total = 0
        score_correct_required = 0
        score_total_required = 0
        score_correct_optional = 0
        score_total_optional = 0

        if self.survey.is_quiz:
            for question in self.survey.questions.all():
                if question.has_correct_answer():
                    answer = self.answers.filter(question=question).first()

                    if question.required:
                        score_total_required += 1
                        if answer and answer.is_correct():
                            score_correct_required += 1
                    else:
                        # Only count optional if answered
                        if answer:
                            score_total_optional += 1
                            if answer.is_correct():
                                score_correct_optional += 1

        score_correct = score_correct_required + score_correct_optional
        score_total = score_total_required + score_total_optional
        relative_score = round(score_correct / score_total * 100, 2) if score_total else 0
        required_score = round(score_correct_required / score_total_required * 100, 2) if score_total_required else 0
        optional_score = round(score_correct_optional / score_total_optional * 100, 2) if score_total_optional else 0



        self.score_correct = score_correct
        self.score_total = score_total
        self.score_correct_required = score_correct_required
        self.score_total_required = score_total_required
        self.score_correct_optional = score_correct_optional
        self.score_total_optional = score_total_optional
        self.relative_score = relative_score
        self.required_score = required_score
        self.optional_score = optional_score
        self.save(update_fields=[
            'score_correct', 'score_total',
            'score_correct_required', 'score_total_required',
            'score_correct_optional', 'score_total_optional',
            'relative_score', 'required_score', 'optional_score'
        ])
        return {
            'score_correct': score_correct,
            'score_total': score_total,
            'score_correct_required': score_correct_required,
            'score_total_required': score_total_required,
            'score_correct_optional': score_correct_optional,
            'score_total_optional': score_total_optional,
            'relative_score': relative_score,
            'required_score': required_score,
            'optional_score': optional_score,
        }

    def get_combined_availability_hours(self):
        """
        Compile all TIME_AVAILABILITY answers into a single set of hour-of-week integers,
        filtered by DAY_AVAILABILITY answers if present.

        Returns:
            set: Set of hour-of-week integers (0-167) representing when user is available

        Logic:
        1. Collect all hours from all TA questions
        2. If any DY questions exist, filter to only include days selected in DY answers
        3. Return combined set

        Example:
            TA Q1: User selects Mon 14:00, Tue 14:00 → hours [14, 38]
            TA Q2: User selects Wed 10:00 → hours [58]
            DY Q1: User selects Monday, Wednesday
            Result: {14, 58} (Tue filtered out)
        """
        
        # Collect all hour-of-week values from TA questions
        all_ta_hours = set()
        ta_answers = self.answers.filter(question__question_type='TA')

        for answer in ta_answers:
            hours = answer.get_hour_of_week_list()
            all_ta_hours.update(hours)

        # If no TA answers, return empty set
        if not all_ta_hours:
            return set()

        # Check if there are any DY (Day Availability) questions
        dy_answers = self.answers.filter(question__question_type='DY')

        if not dy_answers.exists():
            # No day filtering needed
            return all_ta_hours

        # Collect selected days from all DY questions
        selected_day_indices = set()
        day_name_to_index = {
            'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
            'Friday': 4, 'Saturday': 5, 'Sunday': 6
        }

        for dy_answer in dy_answers:
            for choice in dy_answer.selected_choices.all():
                day_name = choice.text  # "Monday", "Tuesday", etc.
                day_index = day_name_to_index.get(day_name)
                if day_index is not None:
                    selected_day_indices.add(day_index)

        # If no days selected in DY, return empty (user said they're not available any day)
        if not selected_day_indices:
            return set()

        # Filter TA hours to only include selected days
        filtered_hours = set()
        for hour_of_week in all_ta_hours:
            day_index = hour_of_week // 24
            if day_index in selected_day_indices:
                filtered_hours.add(hour_of_week)

        return filtered_hours

# An answer to a question that is linked to a user's response
class Answer(models.Model):
    response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)

    # For open-ended
    text_answer = models.TextField(blank=True, null=True)

    # For multiple choice (single selection)
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True, related_name='single_answers')

    # For multiple selection
    selected_choices = models.ManyToManyField(Choice, blank=True, related_name='multiple_answers')

    # For date/time questions
    date_answer = models.DateField(blank=True, null=True, help_text="For date questions and date part of datetime questions")
    time_answer = models.TimeField(blank=True, null=True, help_text="For time questions and time part of datetime questions")

    # For rating/likert (stored as integer value)
    numeric_answer = models.IntegerField(blank=True, null=True, help_text="For rating and likert scale questions")

    # For Post-based answers - store Post directly for easier querying/reporting
    selected_post = models.ForeignKey(
        'the_keep.Post', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='single_post_answers',
        help_text="For Post-based single selection questions"
    )
    selected_posts = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='multiple_post_answers',
        help_text="For Post-based multiple selection questions"
    )

    # For "Other" free-text option on MC/MS questions
    other_text = models.TextField(blank=True, null=True, help_text="Free-text response when 'Other' is selected")

    class Meta:
        ordering = ['response', 'question__order']
        verbose_name = 'Answer'
        verbose_name_plural = 'Answers'
        unique_together = ('response', 'question')

    def clean(self):
        """Validate that answer matches question type"""
        qtype = self.question.question_type

        # MULTIPLE CHOICE
        if qtype == Question.QuestionType.MULTIPLE_CHOICE:
            if self.question.required and not self.selected_choice:
                raise ValidationError("Multiple choice question requires a selected choice.")
            if self.selected_choices.exists():
                raise ValidationError("Only one choice allowed for multiple choice questions.")

        # MULTIPLE SELECTION
        elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for multiple selection.")

        # TIME AVAILABILITY - same as multiple selection
        elif qtype == Question.QuestionType.TIME_AVAILABILITY:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for time availability.")
            
        # DAY AVAILABILITY - same as multiple selection
        elif qtype == Question.QuestionType.DAY_AVAILABILITY:
            # Check will happen after save for M2M fields
            if self.selected_choice:
                raise ValidationError("Use 'selected_choices' only for day availability.")

        # OPEN ENDED
        elif qtype == Question.QuestionType.OPEN_ENDED:
            if self.question.required and not self.text_answer:
                raise ValidationError("Open-ended question requires a text answer.")
            if self.selected_choice or self.selected_choices.exists():
                raise ValidationError("Open-ended questions should not have choices selected.")

        # BOOLEAN (YES/NO)
        elif qtype == Question.QuestionType.BOOLEAN:
            if self.question.required and not self.selected_choice:
                raise ValidationError("Yes/No question requires a choice.")
            if self.selected_choice:
                valid = self.question.choices.filter(pk=self.selected_choice.pk).exists()
                if not valid:
                    raise ValidationError("Selected choice is not valid for this question.")

        # SCALE - store in numeric_answer
        elif qtype == Question.QuestionType.SCALE:
            if self.question.required and self.numeric_answer is None:
                raise ValidationError(f"{self.question.get_question_type_display()} question requires a numeric answer.")
            if self.numeric_answer is not None and self.question.likert_scale:
                if not (self.question.likert_scale.min_value <= self.numeric_answer <= self.question.likert_scale.max_value):
                    raise ValidationError(f"Answer must be between {self.question.likert_scale.min_value} and {self.question.likert_scale.max_value}.")

        # RANKING - uses RankedAnswer model
        elif qtype == Question.QuestionType.RANKING:
            # Validation happens in RankedAnswer
            if self.selected_choice or self.selected_choices.exists() or self.text_answer:
                raise ValidationError("Use the RankedAnswer model to store ranking answers.")

        # DATE
        elif qtype == Question.QuestionType.DATE:
            if self.question.required and not self.date_answer:
                raise ValidationError("A valid date must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer or self.time_answer:
                raise ValidationError("Only a date answer is allowed.")

        # TIME
        elif qtype == Question.QuestionType.TIME:
            if self.question.required and not self.time_answer:
                raise ValidationError("A valid time must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer or self.date_answer:
                raise ValidationError("Only a time answer is allowed.")

        # DATETIME
        elif qtype == Question.QuestionType.DATETIME:
            if self.question.required and (not self.date_answer or not self.time_answer):
                raise ValidationError("Both date and time must be provided.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer:
                raise ValidationError("Only date and time answers are allowed.")

        # NUMERIC
        elif qtype == Question.QuestionType.NUMERIC:
            if self.question.required and self.numeric_answer is None:
                raise ValidationError("Numeric question requires a numeric answer.")
            if self.selected_choice or self.selected_choices.exists() or self.text_answer:
                raise ValidationError("Only a numeric answer is allowed for numeric questions.")

        # Fallback
        else:
            raise ValidationError("Unsupported question type.")

    def __str__(self):
        return f"Answer to '{self.question.text[:50]}'"

    def get_display_value(self):
        """Return a human-readable version of the answer"""
        qtype = self.question.question_type

        # Handle Post-based questions
        if self.question.is_post_based():
            if qtype == Question.QuestionType.MULTIPLE_CHOICE:
                if self.selected_post:
                    return self.selected_post.title
                elif self.selected_choice:
                    return self.selected_choice.get_display_text()
                return "No answer"
            elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
                posts = self.selected_posts.all()
                if posts:
                    return ", ".join([p.title for p in posts])
                choices = self.selected_choices.all()
                if choices:
                    return ", ".join([c.get_display_text() for c in choices])
                return "No answer"
            elif qtype == Question.QuestionType.RANKING:
                ranked_posts = self.ranked_post_items.order_by('rank')
                if ranked_posts:
                    return ", ".join([f"{r.rank}. {r.post.title}" for r in ranked_posts])
                ranked = self.ranked_items.order_by('rank')
                if ranked:
                    return ", ".join([f"{r.rank}. {r.choice.get_display_text()}" for r in ranked])
                return "No answer"

        if qtype == Question.QuestionType.MULTIPLE_CHOICE:
            return self.selected_choice.get_display_text() if self.selected_choice else "No answer"
        elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
            choices = self.selected_choices.all()
            return ", ".join([c.get_display_text() for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.TIME_AVAILABILITY:
            choices = self.selected_choices.all().order_by('text')
            return ", ".join([f"{c.text}:00 UTC" for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.DAY_AVAILABILITY:
            choices = self.selected_choices.all().order_by('text')
            return ", ".join([c.text for c in choices]) if choices else "No answer"
        elif qtype == Question.QuestionType.OPEN_ENDED:
            return self.text_answer or "No answer"
        elif qtype == Question.QuestionType.BOOLEAN:
            return self.selected_choice.get_display_text() if self.selected_choice else "No answer"
        elif qtype == Question.QuestionType.SCALE:
            return str(self.numeric_answer) if self.numeric_answer is not None else "No answer"
        elif qtype == Question.QuestionType.RANKING:
            ranked = self.ranked_items.order_by('rank')
            return ", ".join([f"{r.rank}. {r.choice.get_display_text()}" for r in ranked]) if ranked else "No answer"
        elif qtype == Question.QuestionType.DATE:
            return str(self.date_answer) if self.date_answer else "No answer"
        elif qtype == Question.QuestionType.TIME:
            return str(self.time_answer) if self.time_answer else "No answer"
        elif qtype == Question.QuestionType.DATETIME:
            if self.date_answer and self.time_answer:
                from datetime import datetime
                dt = datetime.combine(self.date_answer, self.time_answer)
                return dt.strftime("%B %d, %Y %I:%M %p")
            return "No answer"
        elif qtype == Question.QuestionType.NUMERIC:
            return str(self.numeric_answer) if self.numeric_answer is not None else "No answer"
        return "No answer"

    def is_correct(self):
        """Check if this answer is correct. Returns True/False/None (None if no correct answer defined)"""
        question = self.question
        if not question.has_correct_answer():
            return None

        qtype = question.question_type

        # MC / YN - single choice
        if qtype in [Question.QuestionType.MULTIPLE_CHOICE, Question.QuestionType.BOOLEAN]:
            if question.is_post_based():
                return self.selected_post_id == question.correct_post_id
            return self.selected_choice_id == question.correct_choice_id

        # MS - all-or-nothing (selected must exactly match correct)
        elif qtype == Question.QuestionType.MULTIPLE_SELECTION:
            if question.is_post_based():
                selected_ids = set(self.selected_posts.values_list('id', flat=True))
                correct_ids = set(question.correct_posts.values_list('id', flat=True))
                return selected_ids == correct_ids
            else:
                selected_ids = set(self.selected_choices.values_list('id', flat=True))
                correct_ids = set(question.correct_choices.values_list('id', flat=True))
                return selected_ids == correct_ids

        # NU / LK - exact match
        elif qtype in [Question.QuestionType.NUMERIC, Question.QuestionType.SCALE]:
            return self.numeric_answer == question.correct_numeric

        # RK - exact order match
        elif qtype == Question.QuestionType.RANKING:
            if question.is_post_based():
                user_order = list(self.ranked_post_items.order_by('rank').values_list('post_id', flat=True))
                return user_order == question.correct_ranking_posts
            else:
                user_order = list(self.ranked_items.order_by('rank').values_list('choice_id', flat=True))
                return user_order == question.correct_ranking

        return None

    def get_hour_of_week_list(self):
        """
        Convert TIME_AVAILABILITY answers to hour-of-week integers (0-167).
        Monday 00:00 UTC = 0, Sunday 23:00 UTC = 167

        Takes into account the user's timezone offset at time of response submission.

        Example: User in EST (UTC-5) selects "14:00" on Tuesday
        - They see 14:00 in their local time (which is actually 19:00 UTC)
        - Hour of week = Tuesday(1) * 24 + 19 = 43

        Returns:
            list: Sorted list of hour-of-week integers (0-167)
        """
        if self.question.question_type != Question.QuestionType.TIME_AVAILABILITY:
            return []

        hours_of_week = []
        user_offset = float(self.response.timezone_offset_hours) if self.response.timezone_offset_hours else 0

        # Day code to index mapping
        day_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}

        # Get the enabled days for this question
        enabled_days = self.question.ta_enabled_days if self.question.ta_enabled_days else TA_DAY_CODES

        for choice in self.selected_choices.all():
            local_hour = int(choice.text)  # Hour displayed to user (0-23)

            # Each selected hour applies to all enabled days
            for day_code in enabled_days:
                day_index = day_map.get(day_code, 0)

                # Convert local time to UTC
                utc_hour = local_hour - user_offset
                utc_day = day_index

                # Handle day wraparound
                if utc_hour < 0:
                    utc_hour += 24
                    utc_day = (utc_day - 1) % 7
                elif utc_hour >= 24:
                    utc_hour -= 24
                    utc_day = (utc_day + 1) % 7

                hour_of_week = utc_day * 24 + int(utc_hour)
                hours_of_week.append(hour_of_week)

        return sorted(set(hours_of_week))


class RankedAnswer(models.Model):
    """For ranking questions - stores the rank order of choices"""
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='ranked_items')
    choice = models.ForeignKey(Choice, on_delete=models.CASCADE)
    rank = models.PositiveIntegerField(help_text="Position in ranking (1 = first choice)")

    class Meta:
        ordering = ['answer', 'rank']
        unique_together = ('answer', 'rank')
        verbose_name = 'Ranked Answer'
        verbose_name_plural = 'Ranked Answers'

    def __str__(self):
        return f"Rank {self.rank}: {self.choice.get_display_text()}"


class RankedPostAnswer(models.Model):
    """For ranking Post-based questions - stores the rank order of Posts"""
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='ranked_post_items')
    post = models.ForeignKey('the_keep.Post', on_delete=models.SET_NULL, null=True)
    rank = models.PositiveIntegerField(help_text="Position in ranking (1 = first choice)")

    class Meta:
        ordering = ['answer', 'rank']
        unique_together = ('answer', 'rank')
        verbose_name = 'Ranked Post Answer'
        verbose_name_plural = 'Ranked Post Answers'

    def __str__(self):
        return f"Rank {self.rank}: {self.post.title}"


# Question Templates - Reusable questions
class QuestionTemplate(models.Model):
    """Template for reusable survey questions"""
    name = models.CharField(max_length=200, help_text="Template name (e.g., 'Age Question', 'Satisfaction Scale')")
    text = models.TextField(help_text="The question text")
    question_type = models.CharField(max_length=2, choices=Question.QuestionType.choices)
    likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL)
    help_text = models.CharField(max_length=300, blank=True)
    required = models.BooleanField(default=True)
    allow_other = models.BooleanField(default=False, help_text="Allow 'Other' free-text option (MC/MS only)")
    choices_data = models.JSONField(default=list, blank=True, help_text="List of choice texts for choice-based questions")
    created_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='question_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(default=False, help_text="Make available to all users")

    # Post-based choices configuration
    post_component = models.CharField(
        max_length=20,
        null=True, blank=True,
        help_text="Component type for Post-based choices"
    )
    post_selection_mode = models.CharField(
        max_length=20,
        choices=Question.PostSelectionMode.choices,
        null=True, blank=True,
        help_text="How Posts are selected as choices"
    )
    post_choices = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='question_templates',
        help_text="Pre-selected Posts for 'individual' mode templates"
    )

    # Day configuration for TIME_AVAILABILITY questions
    ta_enabled_days = models.JSONField(
        default=get_default_ta_days,
        blank=True,
        help_text="Days of week for TIME_AVAILABILITY questions"
    )

    # Correct answer fields for templates
    correct_choice_index = models.IntegerField(
        null=True, blank=True,
        help_text="Index into choices_data for correct MC/YN answer"
    )
    correct_choice_indices = models.JSONField(
        default=list, blank=True,
        help_text="List of indices into choices_data for correct MS answers"
    )
    correct_ranking_indices = models.JSONField(
        default=list, blank=True,
        help_text="Ordered list of indices into choices_data for correct RK order"
    )
    correct_post = models.ForeignKey(
        'the_keep.Post', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='correct_for_templates',
        help_text="Correct post for Post-based MC templates"
    )
    correct_posts = models.ManyToManyField(
        'the_keep.Post', blank=True, related_name='correct_for_ms_templates',
        help_text="Correct posts for Post-based MS templates"
    )
    correct_ranking_posts = models.JSONField(
        default=list, blank=True,
        help_text="Ordered list of post IDs for correct RK order"
    )
    correct_numeric = models.IntegerField(
        null=True, blank=True,
        help_text="Correct answer for NU/LK templates"
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Question Template'
        verbose_name_plural = 'Question Templates'

    def __str__(self):
        return self.name

    def to_question_data(self):
        """Convert template to question data format used in create/edit forms"""
        data = {
            'text': self.text,
            'type': self.question_type,
            'required': self.required,
            'help_text': self.help_text,
            'allow_other': self.allow_other,
        }

        if self.question_type == 'LK' and self.likert_scale:
            data['likert_scale_id'] = self.likert_scale_id

        # Handle Post-based templates
        if self.post_component:
            data['post_component'] = self.post_component
            data['post_selection_mode'] = self.post_selection_mode
            if self.post_selection_mode == Question.PostSelectionMode.INDIVIDUAL:
                data['post_choices'] = list(self.post_choices.values_list('id', flat=True))
            # Correct answers for Post-based templates
            if self.correct_post_id:
                data['correct_post_id'] = self.correct_post_id
            if self.correct_posts.exists():
                data['correct_post_ids'] = list(self.correct_posts.values_list('id', flat=True))
            if self.correct_ranking_posts:
                data['correct_ranking_posts'] = self.correct_ranking_posts
        elif self.question_type in ['MC', 'MS', 'YN', 'RK'] and self.choices_data:
            data['choices'] = self.choices_data
            # Correct answers for choice-based templates
            if self.correct_choice_index is not None:
                data['correct_choice_index'] = self.correct_choice_index
            if self.correct_choice_indices:
                data['correct_choice_indices'] = self.correct_choice_indices
            if self.correct_ranking_indices:
                data['correct_ranking_indices'] = self.correct_ranking_indices

        # Handle Time Availability templates
        if self.question_type == 'TA' and self.ta_enabled_days:
            data['ta_enabled_days'] = self.ta_enabled_days

        # Correct answer for numeric types
        if self.question_type in ['NU', 'LK'] and self.correct_numeric is not None:
            data['correct_numeric'] = self.correct_numeric

        return data



# Signal to auto-create UTC hour choices for TIME_AVAILABILITY questions
@receiver(post_save, sender=Question)
def create_time_availability_choices(sender, instance, created, **kwargs):
    """Automatically create 24 UTC hour choices for TIME_AVAILABILITY questions"""
    if instance.question_type == Question.QuestionType.TIME_AVAILABILITY:
        instance.create_utc_hour_choices()
    """Automatically create weekday choices for DAY_AVAILABILITY questions"""
    if instance.question_type == Question.QuestionType.DAY_AVAILABILITY:
        instance.create_day_choices()
