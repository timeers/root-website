import calendar

from django.core.validators import MaxValueValidator, MinValueValidator
from django.urls import reverse
from django.db.models import Q
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
        return self.filter(
            Q(is_public=False) & (
                Q(invited_players=profile) |
                Q(series__players=profile, series_round__isnull=True) |
                Q(series_round__players=profile) |
                Q(guild__members=profile)
            )
        ).exclude(
            created_by=profile
        ).distinct()
    
    def can_see_results(self, profile):
        now = timezone.now()
        return self.filter(
            Q(created_by=profile) |
            Q(show_results_to_respondents=True, responses__user=profile) |
            Q(show_results_on_close=True, end_date__lt=now, responses__isnull=False)
        ).distinct()


class Survey(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    slug = models.SlugField(unique=True, max_length=250, null=True, blank=True)
    
    # Survey Owner Objects
    post = models.ForeignKey('the_keep.Post', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Post that this survey is about.")
    series = models.ForeignKey('the_warroom.Tournament', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament or Series that this survey is about.")
    series_round = models.ForeignKey('the_warroom.Round', on_delete=models.SET_NULL, null=True, blank=True, related_name='surveys', help_text="The Tournament Round that this survey is about.")

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
    show_results_on_close = models.BooleanField(default=False, help_text="Make results public when closed")
    created_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_surveys')

    objects = SurveyQuerySet.as_manager()

    class Meta:
        ordering = ['-is_pinned', '-created_at']
        verbose_name = 'Survey'
        verbose_name_plural = 'Surveys'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('survey-detail', kwargs={'slug': self.slug})

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
        return self.responses.filter(user=user_profile).exists()

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

        # If Private with Series Round, only allow Round players
        if self.series_round:
            if self.series_round.players.filter(pk=user_profile.pk).exists():
                return True
            else:
                return False

        # If Private with Series, only allow Series players
        if self.series:
            if self.series.players.filter(pk=user_profile.pk).exists():
                return True
            else:
                return False

        return False


    def can_take_survey(self, user_profile):
        """Check if a user can take the survey"""
        if not user_profile:
            return False

        # Block banned users
        if user_profile.group == Profile.GroupChoices.BANNED:
            return False

        # Survey must be open
        if not self.is_available():
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

        # If Private with Series Round, only allow Round players
        if self.series_round:
            if self.series_round.players.filter(pk=user_profile.pk).exists():
                return True
            else:
                return False

        # If Private with Series, only allow Series players
        if self.series:
            if self.series.players.filter(pk=user_profile.pk).exists():
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

class LikertScale(models.Model):
    name = models.CharField(max_length=100, help_text="Name for this scale (e.g., '5-point Agreement')")
    min_value = models.IntegerField(default=1, validators=[MinValueValidator(0)], help_text="Minimum value (up to -10)")
    max_value = models.IntegerField(default=5, validators=[MaxValueValidator(10)], help_text="Maximum value (up to 10)")
    min_label = models.CharField(max_length=50, default="Strongly Disagree", blank=False, help_text="Label for minimum value")
    max_label = models.CharField(max_length=50, default="Strongly Agree", blank=False, help_text="Label for maximum value")
    labels = models.JSONField(default=dict, null=True, blank=True, help_text='Optional labels for each value (e.g., {"1": "Poor", "5": "Excellent"})')

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
    text = models.TextField(help_text="The question text")
    likert_scale = models.ForeignKey(LikertScale, null=True, blank=True, on_delete=models.SET_NULL, help_text="Required for Likert Scale questions")
    question_type = models.CharField(max_length=2, choices=QuestionType.choices)
    order = models.PositiveIntegerField(default=0, help_text="Display order in survey")
    required = models.BooleanField(default=True, help_text="Is this question required?")
    help_text = models.CharField(max_length=300, blank=True, help_text="Optional help text shown to users")
    is_hidden = models.BooleanField(default=False, help_text="Hidden questions preserve data but are not shown to respondents")

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

    class Meta:
        ordering = ['survey', 'order', 'id']
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
    user = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='survey_responses')
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Survey Response'
        verbose_name_plural = 'Survey Responses'
        indexes = [
            models.Index(fields=['survey', 'user']),
        ]

    def __str__(self):
        user_display = self.user.name if self.user else "Anonymous"
        return f"{user_display} → {self.survey.title}"

    def can_view_response(self, user_profile):
        if not user_profile:
            return False
        # Admin, survey owner or respondent can view
        if user_profile == self.user or user_profile.admin or user_profile == self.survey.created_by:
            return True
        return False

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
        }

        if self.question_type == 'LK' and self.likert_scale:
            data['likert_scale_id'] = self.likert_scale_id

        # Handle Post-based templates
        if self.post_component:
            data['post_component'] = self.post_component
            data['post_selection_mode'] = self.post_selection_mode
            if self.post_selection_mode == Question.PostSelectionMode.INDIVIDUAL:
                data['post_choices'] = list(self.post_choices.values_list('id', flat=True))
        elif self.question_type in ['MC', 'MS', 'YN', 'RK'] and self.choices_data:
            data['choices'] = self.choices_data

        # Handle Time Availability templates
        if self.question_type == 'TA' and self.ta_enabled_days:
            data['ta_enabled_days'] = self.ta_enabled_days

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
