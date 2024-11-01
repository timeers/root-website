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
        fields = ['display_name','image', 'dwd', 'league']
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