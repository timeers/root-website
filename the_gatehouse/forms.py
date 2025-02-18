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
        
        # # Only show the 'weird' field if 'in_weird_root' is True
        # if self.instance and not self.instance.in_weird_root:
        #     self.fields['weird'].label = "Join the Weird Root Discord to view Fan Content!"
        #     self.fields['weird'].widget.attrs['disabled'] = 'disabled'
        #     # self.fields.disable('weird')  # Remove the weird field from the form if the profile's in_weird_root is False

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
        help_text="Users can record games, Designers can post new fan content, users should only be banned after being warned and repeatedly posting false data. User status is only visible to Moderators."
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


class FeedbackForm(forms.Form):
    TITLE_CHOICES = [
                ('general', 'General Feedback'),
                ('bug', 'Bug Report'),
                ('feature', 'Feature Request'),
                ('usability', 'Usability Feedback'),
                ('other', 'Other')
    ]
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, label="Select Category")
    message = forms.CharField(widget=forms.Textarea, label="Please Provide Details")
    author = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'maxlength': '50', 'placeholder': 'Discord Username or Email'}),  # Max length handled here
        label='Contact Info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class ReportForm(forms.Form):
    TITLE_CHOICES = [
                ('incorrect', 'Incorrect Information'),
                ('offensive', 'Offensive Image/Language'),
                ('other', 'Other')
    ]
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, label="Select Category")
    message = forms.CharField(widget=forms.Textarea, label="Please Provide Details")
    author = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'maxlength': '50', 
            'placeholder': 'Discord Username or Email'}),  # Max length handled here
        label='Contact Info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class RequestForm(forms.Form):
    TITLE_CHOICES = [
                ('faction', 'Faction'),
                ('map', 'Map'),
                ('deck', 'Deck'),
                ('other', 'Other')
    ]
    
    title = forms.ChoiceField(choices=TITLE_CHOICES, label="Select Category")
    message = forms.CharField(widget=forms.Textarea, label="Please Provide Details")
    author = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'maxlength': '50', 
            'placeholder': 'Discord Username or Email'}),  # Max length handled here
        label='Contact Info'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        