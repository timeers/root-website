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
    def __init__(self, *args, survey=None, existing_response=None, **kwargs):
        super().__init__(*args, **kwargs)

        if not survey:
            return

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
                self.fields[field_name] = forms.ChoiceField(
                    label=question.text,
                    choices=choices,
                    required=question.required,
                    help_text=question.help_text,
                    widget=forms.RadioSelect
                )

            # Multiple Selection
            elif question.question_type == 'MS':
                # Handle Post-based questions
                if question.uses_all_official_posts():
                    posts = question.get_post_choices()
                    choices = [(f'post_{post.id}', post.title) for post in posts]
                else:
                    choices = [(choice.id, choice.get_display_text()) for choice in question.get_visible_choices()]
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=question.text,
                    choices=choices,
                    required=False,  # We'll validate this manually in the view if needed
                    help_text=question.help_text,
                    widget=forms.CheckboxSelectMultiple
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
                            self.initial[field_name] = list(answer.selected_choices.values_list('id', flat=True))

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
