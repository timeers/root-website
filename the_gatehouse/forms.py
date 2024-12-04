import re
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
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


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = Profile
        # fields = ['display_name','image', 'dwd', 'league']       
        fields = ['image', 'dwd', 'league']
        labels = {
            'dwd': 'Direwolf Digital Username',  # Custom label for dwd_username
            'league' : 'Registered for Digital League',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Check if the instance has a value for dwd
        if self.instance and self.instance.dwd:
            # Disable the Direwolf Digital field if DWD has a value (don't want users to change their names)
            self.fields.pop('dwd')  # This removes the dwd field
        else:
            self.fields['dwd'].label = "Direwolf Digital Username (cannot be changed once saved)"
        if self.instance and self.instance.league == True:
            self.fields.pop('league')






class PlayerCreateForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['discord']
        widgets = {
            'discord': forms.TextInput(attrs={'maxlength': '32'})  # Set the max length in the widget
        }
        labels = {
            'discord': 'Discord Username'
        }

    discord = forms.CharField(max_length=32, widget=forms.TextInput())

    def clean_discord(self):
        discord = self.cleaned_data['discord']
        
        # Convert the input to lowercase
        discord = discord.lower()
        
        # Regular expression for allowed characters: letters, numbers, underscores, and periods
        if not re.match(r'^[a-z0-9_.]+$', discord):
            raise forms.ValidationError('Please only use numbers, letters, underscores _ , or periods.')
        
        return discord
