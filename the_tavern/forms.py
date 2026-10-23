from django import forms
from .models import GameComment, PostComment

class GameCommentCreateForm(forms.ModelForm):
    class Meta:
        model = GameComment
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

class PostCommentCreateForm(forms.ModelForm):
    class Meta:
        model = PostComment
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

class CommentCreateForm(forms.ModelForm):
    class Meta:
        model = None  # Set this dynamically later
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

    def set_model(self, model):
        """Method to set the model for the form dynamically."""
        self.Meta.model = model



# Survey Forms
class SurveyResponseForm(forms.Form):
    """
    Dynamic form for survey responses.
    Form fields are created based on the survey's questions.
    """
    def __init__(self, *args, survey=None, existing_response=None, profile=None, **kwargs):
        super().__init__(*args, **kwargs)

        if not survey:
            return

        # The respondent, for questions that pre-fill from their profile (TZ)
        # or their General Availability (WA).
        if profile is None and existing_response is not None:
            profile = existing_response.profile
        # Only the FIRST General Availability grid is pre-filled: the save is a
        # union of every WA grid, so pre-filling them all would make removing
        # an hour mean clearing it in each one.
        wa_prefilled = False

        # Create a field for each question in the survey (only non-hidden)
        for question in survey.questions.filter(is_hidden=False):
            field_name = f'question_{question.id}'

            # Multiple Choice
            if question.question_type == 'MC':
                # Handle Post-based questions
                if question.uses_all_official_posts():
                    posts = question.get_post_choices()
                    choices = [(f'post_{post.id}', post.title) for post in posts]
                else:
                    choices = [(choice.id, choice.get_display_text()) for choice in question.get_visible_choices()]
                    if question.allow_other:
                        choices.append(('other', 'Other'))
                if question.display_as_dropdown:
                    # Dropdown for long choice lists (e.g. timezones). A blank
                    # placeholder lets "required" actually force a selection.
                    dropdown_choices = [('', '---------')] + list(choices)
                    mc_widget = forms.Select(attrs={'class': 'form-select'})
                else:
                    dropdown_choices = choices
                    mc_widget = forms.RadioSelect
                self.fields[field_name] = forms.ChoiceField(
                    label=question.text,
                    choices=dropdown_choices,
                    required=question.required,
                    help_text=question.help_text,
                    widget=mc_widget
                )
                if question.allow_other and not question.post_component:
                    self.fields[f'{field_name}_other_text'] = forms.CharField(
                        required=False,
                        max_length=500,
                        widget=forms.TextInput(attrs={
                            'class': 'form-control',
                            'placeholder': 'Please specify...',
                        })
                    )

            # Multiple Selection
            elif question.question_type == 'MS':
                # Handle Post-based questions
                if question.uses_all_official_posts():
                    posts = question.get_post_choices()
                    choices = [(f'post_{post.id}', post.title) for post in posts]
                else:
                    choices = [(choice.id, choice.get_display_text()) for choice in question.get_visible_choices()]
                    if question.allow_other:
                        choices.append(('other', 'Other'))
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=question.text,
                    choices=choices,
                    required=False,  # We'll validate this manually in the view if needed
                    help_text=question.help_text,
                    widget=forms.CheckboxSelectMultiple
                )
                if question.allow_other and not question.post_component:
                    self.fields[f'{field_name}_other_text'] = forms.CharField(
                        required=False,
                        max_length=500,
                        widget=forms.TextInput(attrs={
                            'class': 'form-control',
                            'placeholder': 'Please specify...',
                        })
                    )

            # Time Availability (handled in template with JavaScript)
            elif question.question_type == 'TA':
                # Create hidden field - actual UI is rendered in template
                choices = [(choice.id, choice.text) for choice in question.get_visible_choices()]
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=question.text,
                    choices=choices,
                    required=False,  # We'll validate this manually in the view if needed
                    help_text=question.help_text,
                    widget=forms.MultipleHiddenInput
                )

            # Day Availability
            elif question.question_type == 'DY':
                # Create hidden field - actual UI is rendered in template
                choices = [(choice.id, choice.text) for choice in question.get_visible_choices()]
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=question.text,
                    choices=choices,
                    required=False,  # We'll validate this manually in the view if needed
                    help_text=question.help_text,
                    widget=forms.MultipleHiddenInput
                )

            # Weekly Availability (7x24 grid rendered in the template)
            elif question.question_type == 'WA':
                # A plain CharField, not a MultipleChoiceField: WA stores a JSON
                # list of hour-of-week ints and has no Choice rows to validate
                # against. The grid JS serializes the painted cells into this.
                self.fields[field_name] = forms.CharField(
                    label=question.text,
                    required=False,  # validated manually in the view, like TA/DY
                    help_text=question.help_text,
                    widget=forms.HiddenInput
                )
                # The zone the grid was DRAWN in, which is what those local hours
                # mean. Without it the view would have to guess, and a mismatch
                # silently shifts a whole week's availability.
                self.fields[f'{field_name}_timezone'] = forms.CharField(
                    required=False, widget=forms.HiddenInput
                )
                if not wa_prefilled:
                    wa_prefilled = True
                    self._prefill_general_availability(
                        field_name, question, profile, existing_response)

            # Timezone - confirms or changes the respondent's profile timezone.
            # Same curated list as /availability; the page's script adds the
            # browser-detected zone on top, which TimezoneChoiceField accepts.
            elif question.question_type == 'TZ':
                from the_databot.services.time_parsing import timezone_choice_groups
                from the_gatehouse.forms import TimezoneChoiceField
                initial_tz = self._initial_timezone(question, profile, existing_response)
                current = self.data.get(field_name) if self.is_bound else initial_tz
                self.fields[field_name] = TimezoneChoiceField(
                    label=question.text,
                    choices=[('', '---------')] + timezone_choice_groups(current=current),
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.Select(attrs={'class': 'form-select', 'data-tz-question': '1'})
                )
                if initial_tz:
                    self.initial[field_name] = initial_tz

            # Open Ended
            elif question.question_type == 'OE':
                self.fields[field_name] = forms.CharField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.Textarea(attrs={
                        'rows': 5,
                        'class': 'form-control modern-textarea',
                        'placeholder': 'Type your answer here...',
                        'style': 'resize: vertical;'
                    })
                )

            # Boolean (Yes/No)
            elif question.question_type == 'YN':
                choices = [(choice.id, choice.get_display_text()) for choice in question.get_visible_choices()]
                self.fields[field_name] = forms.ChoiceField(
                    label=question.text,
                    choices=choices,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.RadioSelect
                )

            # Scale
            elif question.question_type == 'LK':
                if question.likert_scale:
                    scale = question.likert_scale
                    choices = [(i, str(i)) for i in range(scale.min_value, scale.max_value + 1)]
                    self.fields[field_name] = forms.ChoiceField(
                        label=question.text,
                        choices=choices,
                        required=question.required,
                        help_text=question.help_text or f"{scale.min_label} ({scale.min_value}) to {scale.max_label} ({scale.max_value})",
                        widget=forms.RadioSelect
                    )

            # Ranking
            elif question.question_type == 'RK':
                # For ranking, we'll use a text field with instructions
                # Frontend JavaScript would typically handle drag-and-drop
                if question.uses_all_official_posts():
                    posts = question.get_post_choices()
                    choices_text = ", ".join([post.title for post in posts])
                else:
                    choices_text = ", ".join([choice.get_display_text() for choice in question.get_visible_choices()])
                self.fields[field_name] = forms.CharField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text or f"Rank these items (comma-separated IDs): {choices_text}",
                    widget=forms.TextInput(attrs={'placeholder': 'e.g., 1,3,2'})
                )

            # Date only
            elif question.question_type == 'DA':
                self.fields[field_name] = forms.DateField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.DateInput(attrs={'type': 'date'})
                )

            # Time only
            elif question.question_type == 'TI':
                self.fields[field_name] = forms.TimeField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.TimeInput(attrs={'type': 'time'})
                )

            # Date & Time
            elif question.question_type == 'DT':
                self.fields[field_name] = forms.DateTimeField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.DateTimeInput(attrs={'type': 'datetime-local'})
                )

            # Numeric
            elif question.question_type == 'NU':
                self.fields[field_name] = forms.IntegerField(
                    label=question.text,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.NumberInput(attrs={
                        'class': 'form-control',
                        'placeholder': 'Enter a number...'
                    })
                )

        # Prepopulate with existing response if editing
        if existing_response and not kwargs.get('data'):
            from .models import Answer, RankedAnswer, RankedPostAnswer
            for question in survey.questions.filter(is_hidden=False):
                field_name = f'question_{question.id}'
                try:
                    answer = Answer.objects.get(response=existing_response, question=question)

                    if question.question_type == 'MC':
                        # Single choice - handle Post-based questions
                        if question.uses_all_official_posts() and answer.selected_post:
                            self.initial[field_name] = f'post_{answer.selected_post.id}'
                        elif answer.other_text and question.allow_other:
                            self.initial[field_name] = 'other'
                            self.initial[f'{field_name}_other_text'] = answer.other_text
                        elif answer.selected_choice:
                            self.initial[field_name] = answer.selected_choice.id

                    elif question.question_type == 'YN':
                        # Boolean - single choice
                        if answer.selected_choice:
                            self.initial[field_name] = answer.selected_choice.id

                    elif question.question_type == 'MS':
                        # Multiple choices - handle Post-based questions
                        if question.uses_all_official_posts():
                            post_ids = answer.selected_posts.values_list('id', flat=True)
                            self.initial[field_name] = [f'post_{pid}' for pid in post_ids]
                        else:
                            initial_choices = list(answer.selected_choices.values_list('id', flat=True))
                            if answer.other_text and question.allow_other:
                                initial_choices.append('other')
                                self.initial[f'{field_name}_other_text'] = answer.other_text
                            self.initial[field_name] = initial_choices

                    elif question.question_type == 'TA' or question.question_type == 'DY':
                        # Time/Day availability - multiple choices
                        self.initial[field_name] = list(answer.selected_choices.values_list('id', flat=True))

                    elif question.question_type == 'OE':
                        # Open ended
                        self.initial[field_name] = answer.text_answer

                    elif question.question_type == 'LK':
                        # Scale
                        self.initial[field_name] = answer.numeric_answer

                    elif question.question_type == 'RK':
                        # Ranking - handle Post-based questions
                        if question.uses_all_official_posts():
                            ranked_posts = RankedPostAnswer.objects.filter(answer=answer).order_by('rank')
                            post_ids = [f'post_{rp.post.id}' for rp in ranked_posts]
                            self.initial[field_name] = ','.join(post_ids)
                        else:
                            ranked_answers = RankedAnswer.objects.filter(answer=answer).order_by('rank')
                            choice_ids = [str(ra.choice.id) for ra in ranked_answers]
                            self.initial[field_name] = ','.join(choice_ids)

                    elif question.question_type == 'DA':
                        # Date only
                        self.initial[field_name] = answer.date_answer

                    elif question.question_type == 'TI':
                        # Time only
                        self.initial[field_name] = answer.time_answer

                    elif question.question_type == 'DT':
                        # Date & Time
                        from datetime import datetime
                        if answer.date_answer and answer.time_answer:
                            dt = datetime.combine(answer.date_answer, answer.time_answer)
                            self.initial[field_name] = dt

                    elif question.question_type == 'NU':
                        # Numeric
                        self.initial[field_name] = answer.numeric_answer

                except Answer.DoesNotExist:
                    pass

    def _prefill_general_availability(self, field_name, question, profile,
                                      existing_response):
        """Start a General Availability (WA) grid on the respondent's CURRENT
        General Availability -- the same schedule /availability edits and that
        saving this survey writes back.

        Sources, first NON-EMPTY wins:
          1. the General row (week_start=None);
          2. on edit, the hours saved on this response;
          3. nothing.
        An empty General row counts as unset: /availability creates one on a
        bare visit, so it isn't availability the player actually chose -- the
        saved answer, if any, is a better starting point.

        Stored UTC, drawn locally. Converted in the profile's zone, the same
        one the grid is labelled and posted in (_survey_grid_timezone). With no
        profile zone the page would draw in the BROWSER's zone, which the
        server can't know -- so pre-filled cells are drawn in UTC instead and
        the posted zone pinned to UTC to match.
        """
        if profile is None:
            return
        from the_gatehouse.models import PlayerSchedule
        from the_gatehouse.services.availability import utc_to_local_hours
        from the_databot.services.time_parsing import valid_timezone

        hours = (PlayerSchedule.objects
                 .filter(profile=profile, week_start=None)
                 .values_list('available_hours', flat=True).first())
        if not hours and existing_response is not None:
            from .models import Answer
            hours = (Answer.objects
                     .filter(response=existing_response, question=question)
                     .values_list('availability_hours', flat=True).first())
        if not hours:
            return

        tz_name = profile.timezone if valid_timezone(profile.timezone) else None
        local = utc_to_local_hours(hours, tz_name or 'UTC')
        self.initial[field_name] = ','.join(str(h) for h in local)
        if tz_name is None:
            self.initial[f'{field_name}_timezone'] = 'UTC'

    @staticmethod
    def _initial_timezone(question, profile, existing_response):
        """What a TZ question starts on: the CURRENT profile zone, then the zone
        saved on this response.

        Profile first, even when editing: every save copies the answer to the
        profile, so pre-filling a stale saved answer would quietly roll back a
        zone the player changed since (on /availability or in the bot) the
        moment they edit some unrelated answer.
        """
        from the_databot.services.time_parsing import valid_timezone
        profile_tz = getattr(profile, 'timezone', None)
        if valid_timezone(profile_tz):
            return profile_tz
        if existing_response is not None:
            from .models import Answer
            saved = (Answer.objects.filter(response=existing_response, question=question)
                     .values_list('text_answer', flat=True).first())
            if valid_timezone(saved):
                return saved
        return None
