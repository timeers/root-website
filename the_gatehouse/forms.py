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
            'league' : 'Register for Root Digital League',
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
        if self.instance and self.instance.dwd:
            # Disable the Direwolf Digital field if DWD has a value (don't want users to change their names)
            self.fields.pop('dwd')  # This removes the dwd field
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
        label="User Status"
    )
    # Adding a checkbox to the form
    nominate_admin = forms.BooleanField(
        required=False,
        label="Nominate User as Moderator",  # Label for the checkbox
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    class Meta:
        model = Profile
        fields = ['group', 'nominate_admin']

    def __init__(self, *args, **kwargs):
        user_to_edit = kwargs.pop('user_to_edit', None)
        super().__init__(*args, **kwargs)  # Ensure the form is initialized with the correct args and kwargs

        if user_to_edit:
            if user_to_edit.admin or user_to_edit.group == "O":
                self.fields.pop('nominate_admin', None)
                self.fields.pop('group', None)

        

class PlayerCreateForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['discord']
        widgets = {
            'discord': forms.TextInput(attrs={'maxlength': '32'})  # Max length handled here
        }
        labels = {
            'discord': 'Discord Username'
        }

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
