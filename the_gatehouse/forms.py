import re
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import Profile, Website, MessageChoices, Theme, BackgroundImage, ForegroundImage, Holiday
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox
from django.utils.translation import gettext_lazy as _


class UserRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

class UserUpdateForm(forms.ModelForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['email']


class ProfileUpdateForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('1', _('Stable Only')),
        ('2', _('Testing and Above')),
        ('3', _('Development and Above')),
        ('4', _('All (include Inactive)')),
    ]
    view_status = forms.ChoiceField(
        choices=STATUS_CHOICES, initial="4",
        required=True,
        help_text=_("Pick what you want to see. If you hide something, it won’t show up in your searches or game setup."),
        label=_("Status Visiblity")
    )
    class Meta:
        model = Profile     
        fields = ['image', 'dwd', 'weird', 'view_status', 'language'] # 'league' to add yourself to a TTS League
        labels = {
            'dwd': 'Direwolf Digital Username',  # Custom label for dwd_username
            'league' : 'Register for Root TTS League',
            'weird' : _('Show Fan Content'),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['language'].empty_label = _("Set Automatically")
        self.fields['language'].help_text = _("Select your preferred language. When available, component content will be shown in this language by default.")
        # Check if the instance has a value for dwd
        if self.instance and self.instance.dwd and not self.instance.admin:
            # Disable the Direwolf Digital field if DWD has a value (don't want users to change their names)
            self.fields.pop('dwd')  # This removes the dwd field
        else:
            if self.instance.admin:
                self.fields['dwd'].label = "Direwolf Digital Username"
            else:
                self.fields['dwd'].label = "Direwolf Digital Username (cannot be changed once saved)"
        # Save to repurpose #########################
        # if self.instance and self.instance.league == True:
        #     self.fields.pop('league')

    def clean(self):
        cleaned_data = super().clean()
        dwd = cleaned_data.get('dwd')
        # Save to repurpose #########################
        # league = cleaned_data.get('league')

        # Check if dwd matches the pattern of any alphanumeric characters followed by a '+' and exactly 4 digits
        if dwd and not re.match(r'^[a-zA-Z0-9]+(\+\d{4})$', dwd):
            cleaned_data['dwd'] = None
            raise ValidationError(f"DWD usernames must be in the format 'username+1234'.")


        return cleaned_data

class UserManageForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('B', 'Banned'),
        ('P', 'User'),
        ('E', 'Editor'),
        ('D', 'Designer')
    ]
    group = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        label="User Status",
        help_text="Users can record games, Editors can edit their existing posts, Designers can post new fan content and delete their existing posts, users should only be banned after being warned and repeat offenses. User status is only visible to Moderators."
    )
    # Adding a checkbox to the form
    nominate_admin = forms.BooleanField(
        required=False,
        label="Recommend User as Moderator",  # Label for the checkbox
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Check this box if you want to vote to promote the user to the moderator role. (Other moderators will be able to see your recommendation)"
    )
    dismiss_admin = forms.BooleanField(
        required=False,
        label="Vote to dismiss Moderator",  # Label for the checkbox
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Check this box if you want to vote to dismiss the user from the moderator role. (Other moderators will be able to see your vote)"
    )

    class Meta:
        model = Profile
        fields = ['group', 'nominate_admin', 'dwd']
        labels = {
            'dwd': 'Direwolf Digital Username',  # Custom label for dwd_username
        }

    def __init__(self, *args, **kwargs):
        user_to_edit = kwargs.pop('user_to_edit', None)
        current_user = kwargs.pop('current_user', None)
        super().__init__(*args, **kwargs)  # Ensure the form is initialized with the correct args and kwargs

        if user_to_edit:
            self.instance = user_to_edit
            # Set the initial value for the dwd field
            self.fields['dwd'].initial = user_to_edit.dwd

            if not user_to_edit.admin:
                self.fields.pop('dismiss_admin', None)
            elif user_to_edit.admin_dismiss:
                if user_to_edit.admin_dismiss != current_user:
                    self.fields['dismiss_admin'].label = f"Dismiss Moderator (Voted by {user_to_edit.admin_dismiss.name})"
                    self.fields['dismiss_admin'].help_text = ""
                else:
                    self.fields['dismiss_admin'].label = f"Voted to dismiss Moderator"
                    self.fields['dismiss_admin'].help_text = ""
                    self.fields['dismiss_admin'].widget.attrs['disabled'] = 'disabled'

            if user_to_edit.admin or user_to_edit.group == "O":
                if user_to_edit == current_user:
                    self.fields.pop('dismiss_admin', None)
                self.fields.pop('nominate_admin', None)
                self.fields.pop('group', None)

            elif user_to_edit.admin_nominated:
                if user_to_edit.admin_nominated != current_user:
                    self.fields['nominate_admin'].label = f"Promote User to Moderator (Recommended by {user_to_edit.admin_nominated.name})"
                    self.fields['nominate_admin'].help_text = ""
                else:
                    self.fields['nominate_admin'].label = f"Recommended as Moderator"
                    self.fields['nominate_admin'].help_text = ""
                    self.fields['nominate_admin'].widget.attrs['disabled'] = 'disabled'


    def clean_dwd(self):
        """
        Custom validation for the dwd field. This ensures that the uniqueness constraint is only enforced 
        if the dwd value is being changed.
        """
        dwd = self.cleaned_data.get('dwd')
        user_to_edit = self.instance
        # Removing format requirement from Admin form
        # if dwd and not re.match(r'^[a-zA-Z0-9]+(\+\d{4})$', dwd):
        #     raise ValidationError(f"DWD usernames must be in the format 'username+1234'.")
        
        if dwd is not None and user_to_edit.dwd != dwd:
            # Check for uniqueness only if dwd is changing
            if Profile.objects.filter(dwd=dwd).exists():
                raise ValidationError(f"'{dwd}' is already associated with another user.")
        
        # If dwd is not changing or is unique, return it
        return dwd

        

class PlayerCreateForm(forms.ModelForm):
    discord = forms.CharField(
        required=True,  # Make it required
        widget=forms.TextInput(attrs={'maxlength': '32', 'placeholder': _('Discord Username')}),  # Max length handled here
        label=_('Discord Username')
    )
    display_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'maxlength': '50', 'placeholder': _('Display Name (optional)')}), 
        label=_('Display Name (optional)')
    )
    class Meta:
        model = Profile
        fields = ['discord', 'display_name']

    def clean_discord(self):
        discord = self.cleaned_data['discord']
        
        # Convert the input to lowercase (Discord usernames are case insensitive)
        discord = discord.lower()
        
        if len(discord) < 2:
            raise forms.ValidationError(_('Player names cannot be shorter than 2 characters.'))

        if len(discord) > 32:
            raise forms.ValidationError(_('Player names cannot be longer than 32 characters.'))

        # Regular expression for allowed characters: letters, numbers, underscores, and periods
        if not re.match(r'^[a-z0-9_.]+$', discord):
            raise forms.ValidationError(_('Please only use numbers, letters, underscores _ , or periods.'))
        
        # Optional: Check if the username already exists (if necessary)
        if Profile.objects.filter(discord=discord).exists():
            raise forms.ValidationError(_('This Discord username is already registered.'))
        
        return discord

    def clean(self):
        cleaned_data = super().clean()

        discord = cleaned_data.get('discord')
        display_name = cleaned_data.get('display_name')

        if discord and not display_name:
            # Use the raw input before .lower()
            raw_discord = self.data.get('discord', '').strip()
            cleaned_data['display_name'] = raw_discord

        return cleaned_data


class ThemeForm(forms.ModelForm):
    class Meta:
        model = Theme
        fields = ['name', 'theme_color', 'background_color', 'holiday', 'theme_artists', 'public', 'active', 'backup_theme']
        widgets = {
            'theme_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),
            'background_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['backup_theme'].queryset = Theme.objects.exclude(pk=self.instance.pk)
        self.fields['public'].help_text = 'Not yet in use. In the future, users will be able to select public themes for their profile.'
        self.fields['active'].help_text = 'Inactive themes are never used or selectable, regardless of other settings. Use this for themes that are not yet complete.'


class BackgroundImageForm(forms.ModelForm):
    class Meta:
        model = BackgroundImage
        fields = ['name', 'artist', 'image', 'pattern', 'page', 'background_color']
        widgets = {
            'background_color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['background_color'].required = False


class ForegroundImageForm(forms.ModelForm):
    class Meta:
        model = ForegroundImage
        fields = ['name', 'artist', 'image', 'page', 'location', 'depth', 'start_position', 'slide', 'speed']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['start_position'].help_text = 'CSS vw value for horizontal start, e.g. 30vw'
        self.fields['slide'].help_text = 'CSS translate offset for animation, e.g. -60vw'
        self.fields['speed'].help_text = 'CSS vh for animation range end point, e.g. 50vh'
        self.fields['depth'].help_text = 'Must be negative (e.g. -1). Lower values go further behind.'

    def clean_depth(self):
        depth = self.cleaned_data.get('depth')
        if depth is not None and depth >= 0:
            raise forms.ValidationError('Depth must be negative (foreground images must stay behind content).')
        return depth


class HolidayForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        help_text="Year is ignored — only month and day matter.",
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        help_text="Year is ignored — only month and day matter.",
    )

    class Meta:
        model = Holiday
        fields = ['name', 'start_date', 'end_date']

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.start_date = instance.start_date.replace(year=2000)
        instance.end_date = instance.end_date.replace(year=2000)
        if commit:
            instance.save()
        return instance


class MessageForm(forms.Form):
    TITLE_CHOICES = [
        ('general', _('General Feedback')),
        ('usability', _('Usability Feedback')),
        ('bug', _('Bug Report')),
        ('feature', _('Feature Request')),
        ('faction', _('Faction')),
        ('map', _('Map')),
        ('vagabond', _('Vagabond')),
        ('deck', _('Deck')),
        ('landmark', _('Landmark')),
        ('hireling', _('Hireling')),
        ('playtest', _('Playtest Group')),
        ('outdated', _('Outdated Information')),
        ('incorrect', _('Incorrect Information')),
        ('offensive', _('Offensive Image/Language')),
        ('spam', _('Spam')),
        ('other', _('Other')),
        ('weird-root', _('Weird Root')),
        ('french-root', _('French Root')),
        ('translation', _('Existing Translation Missing'))
    ]
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, label=_("Select Category"))
    message = forms.CharField(widget=forms.Textarea, label=_("Details"))
    # Initially make the author field hidden
    author = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),  # Default to hidden
        label=_('Contact Info (Optional)')
    )
    subject = forms.CharField(
        widget=forms.HiddenInput(),  # Makes the field hidden
        required=False
    )
    captcha = ReCaptchaField(
        label = _("Do you work for the Mechanical Marquise?"),
        widget=ReCaptchaV2Checkbox(
            attrs={

                'data-theme': 'light',        # Set a theme (light or dark)
                'data-size': 'normal',       # Set the size to compact (normal is default)
            }
        )
    )
    def __init__(self, *args, author=None, message_category='feedback', **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['message'].widget.attrs.update({
            'rows': '5'
            })

        # If author is provided, set it as the value for the author field
        if author:
            self.fields['author'].initial = author
            del self.fields['captcha']
        else:
            if message_category in ['report', 'request']:
                self.fields['author'].required = True  # Make the author field required
                self.fields['author'].label = _('Contact Info')
            # If no author is passed in, change the widget to a visible TextInput
            self.fields['author'].widget = forms.TextInput(attrs={'maxlength': '50', 'placeholder': _('Discord Username or Email')})

        # Limit choices for title based on message_category
        if message_category == 'feedback':
            self.fields['title'].choices = [
                ('general', _('General Feedback')),
                ('usability', _('Usability Feedback')),
                ('bug', _('Bug Report')),
                ('feature', _('Feature Request')),
                ('other', _('Other'))
            ]
        elif message_category == 'bug':
            self.fields['title'].choices = [('bug', _('Bug Report'))]
            self.fields['title'].initial = 'bug'
            self.fields['title'].widget = forms.HiddenInput()
            self.fields['message'].help_text= _("Please include as many details as you can that lead up to the bug you encountered (what page you were trying to view, what you encountered when it occured, whether you were logged in at the time). Any details provided can help in debugging.")

        elif message_category == 'request':
            self.fields['title'].choices = [
                ('faction', _('Faction')),
                ('map', _('Map')),
                ('deck', _('Deck')),
                ('vagabond', _('Vagabond')),
                ('landmark', _('Landmark')),
                ('hireling', _('Hireling')),
                ('other', _('Other'))
            ]
            self.fields['message'].help_text= _("Please include details for the Fan Content you would like to be added. Each post must include at least one link to a Discord Thread or BGG Post, PNP Files or TTS Mod is a plus. You can make a request for something you did not create, but include the designer's discord username.")

        elif message_category == 'weird-root':
            self.fields['title'].choices = [
                ('weird-root', _('Weird Root'))
            ]
            self.fields['message'].help_text= _("Weird Root is a private server for developing and discussing fan-made Root content. To keep bots out, invites aren't shared publicly. The easiest way to join is by messaging someone already in the server. This site isn’t managed by Weird Root, but if you include a brief message, we’ll do our best to get back to you soon.")
        
        elif message_category == 'french-root':
            self.fields['title'].choices = [
                ('french-root', _('French Root'))
            ]
            self.fields['message'].help_text= _("Root & co - Communauté FR is a French Discord server")
    
        elif message_category == 'report':
            self.fields['title'].choices = [
                ('outdated', _('Outdated Information')),
                ('incorrect', _('Incorrect Information')),
                ('translation', _('Existing Translation Missing')),
                ('offensive', _('Offensive Image/Language')),
                ('spam', _('Spam')),
                ('other', _('Other'))
            ]
            self.fields['message'].widget.attrs.update({
            'placeholder': _('Please provide any relevant information. If information is incorrect or out of date please provide a link to the updated information.')
                })

class GuildJoinRequestForm(forms.Form):
    request_message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Why would you like to join?",
    )
    agreement_message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Acknowledgement",
    )
    acknowledgement = forms.BooleanField(
        required=True,
        label="I agree to be respectful of others and follow the server's rules",
    )


class GlobalMessageForm(forms.ModelForm):
    class Meta:
        model = Website
        fields = ['global_message', 'message_type']
        widgets = {
            'global_message': forms.Textarea(attrs={'rows': 3, 'maxlength': 400, 'class': 'form-control'}),
            'message_type': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'global_message': 'Message',
            'message_type': 'Alert Type',
        }


class SendNotificationForm(forms.Form):
    message = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        label='Message',
    )
    message_type = forms.ChoiceField(
        choices=MessageChoices.choices,
        initial=MessageChoices.INFO,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Alert Type',
    )
    related_url = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional link URL'}),
        label='Link URL (optional)',
    )
    recipients = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        required=True,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '8'}),
        label='Recipients',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recipients'].queryset = Profile.objects.filter(user__isnull=False).order_by('discord')
