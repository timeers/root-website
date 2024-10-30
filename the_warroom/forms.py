from django import forms
from .models import Effort, Game
from django.core.exceptions import ValidationError

class GameCreateForm(forms.ModelForm):  
    class Meta:
        model = Game
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'league', 'undrafted', 'date_posted', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }

class EffortCreateForm(forms.ModelForm):  
    class Meta:
        model = Effort
        fields = ['seat', 'player', 'faction', 'vagabond', 'score', 'win', 'dominance', 'coalition_with', 'game']
    def clean_faction(self):
        faction = self.cleaned_data.get('faction')
        if not faction:
            raise ValidationError("Please select a faction for each player")
        return faction