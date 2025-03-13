import re
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import Profile

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

# Removed League checkbox
class ProfileUpdateForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('1', 'Stable Only'),
        ('2', 'Testing and Above'),
        ('3', 'Development and Above'),
        ('4', 'All (include Inactive)'),
    ]
    view_status = forms.ChoiceField(
        choices=STATUS_CHOICES, initial="4",
        required=True,
        help_text='Choose what is visible to you.',
        label="Status Visiblity"
    )
    class Meta:
        model = Profile     
        fields = ['image', 'dwd', 'weird', 'view_status'] # 'league' to add yourself to RDL tournament
        labels = {
            'dwd': 'Direwolf Digital Username',  # Custom label for dwd_username
            'league' : 'Register for Root TTS League',
            'weird' : 'Show Fan Content',
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

        # Save to repurpose #########################
        # if league and dwd == None and not self.instance.dwd:
        #     raise ValidationError(f"Must have a DWD username to be registered for Leauge.")

        return cleaned_data

class UserManageForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('B', 'Banned'),
        ('P', 'User'),
        ('D', 'Designer')
    ]
    group = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        label="User Status",
        help_text="Users can record games, Designers can post new fan content, users should only be banned after being warned and repeat offenses. User status is only visible to Moderators."
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
        widget=forms.TextInput(attrs={'maxlength': '32', 'placeholder': 'Discord Username'}),  # Max length handled here
        label='Discord Username'
    )
    display_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'maxlength': '50', 'placeholder': 'Display Name (optional)'}), 
        label='Display Name (optional)'
    )
    class Meta:
        model = Profile
        fields = ['discord', 'display_name']

    def clean_discord(self):
        discord = self.cleaned_data['discord']
        
        # Convert the input to lowercase (Discord usernames are case insensitive)
        discord = discord.lower()
        
        if len(discord) > 32:
            raise forms.ValidationError('Player names cannot be longer than 32 characters.')

        # Regular expression for allowed characters: letters, numbers, underscores, and periods
        if not re.match(r'^[a-z0-9_.]+$', discord):
            raise forms.ValidationError('Please only use numbers, letters, underscores _ , or periods.')
        
        # Optional: Check if the username already exists (if necessary)
        if Profile.objects.filter(discord=discord).exists():
            raise forms.ValidationError('This Discord username is already registered.')
        
        return discord


class MessageForm(forms.Form):
    TITLE_CHOICES = [
                ('general', 'General Feedback'),
                ('usability', 'Usability Feedback'),
                ('bug', 'Bug Report'),
                ('feature', 'Feature Request'),
                ('faction', 'Faction'),
                ('map', 'Map'),
                ('deck', 'Deck'),
                ('outdated', 'Outdated Information'),
                ('incorrect', 'Incorrect Information'),
                ('offensive', 'Offensive Image/Language'),
                ('spam', 'Spam'),
                ('other', 'Other'),
                ('weird-root', 'Weird Root')
    ]
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, label="Select Category")
    message = forms.CharField(widget=forms.Textarea, label="Details")
    # Initially make the author field hidden
    author = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),  # Default to hidden
        label='Contact Info (Optional)'
    )
    subject = forms.CharField(
        widget=forms.HiddenInput(),  # Makes the field hidden
        required=False
    )
    def __init__(self, *args, author=None, message_category='feedback', **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['message'].widget.attrs.update({
            'rows': '5'
            })

        # If author is provided, set it as the value for the author field
        if author:
            self.fields['author'].initial = author
        else:
            if message_category in ['report', 'request']:
                self.fields['author'].required = True  # Make the author field required
                self.fields['author'].label = 'Contact Info'
            # If no author is passed in, change the widget to a visible TextInput
            self.fields['author'].widget = forms.TextInput(attrs={'maxlength': '50', 'placeholder': 'Discord Username or Email'})

        # Limit choices for title based on message_category
        if message_category == 'feedback':
            self.fields['title'].choices = [
                ('general', 'General Feedback'),
                ('usability', 'Usability Feedback'),
                ('bug', 'Bug Report'),
                ('feature', 'Feature Request'),
                ('other', 'Other')
            ]
        elif message_category == 'request':
            self.fields['title'].choices = [
                ('faction', 'Faction'),
                ('map', 'Map'),
                ('deck', 'Deck'),
                ('other', 'Other')
            ]
        elif message_category == 'weird-root':
            self.fields['title'].choices = [
                ('weird-root', 'Weird Root')
            ]
            self.fields['message'].widget.attrs.update({
            'placeholder': 'Weird Root is a private server for posting and discussing fan made content for Root. To reduce bot attacks, invites to the server cannot be posted publicly. The fastest way to join is to message someone you know who is already a member. This site is not managed by Weird Root, but if you submit a request here we will do our best to contact you soon to confirm you are not a robot. Include a short message here to confirm.'
                })
        elif message_category == 'report':
            self.fields['title'].choices = [
                ('outdated', 'Outdated Information'),
                ('incorrect', 'Incorrect Information'),
                ('offensive', 'Offensive Image/Language'),
                ('spam', 'Spam'),
                ('other', 'Other')
            ]
            self.fields['message'].widget.attrs.update({
            'placeholder': 'Please provide any relevant information. If information is incorrect or out of date please provide a link to the updated information.'
                })