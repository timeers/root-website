from django import forms
from .models import Effort, Game
from django.core.exceptions import ValidationError

class GameCreateForm(forms.ModelForm):  
    class Meta:
        model = Game
        fields = ['deck', 'map', 'type', 'platform']
        widgets = {
            'type': forms.RadioSelect,
        }

class EffortCreateForm(forms.ModelForm):  
    class Meta:
        model = Effort
        fields = ['player', 'faction', 'vagabond', 'score', 'win', 'dominance', 'coalition_with', 'notes']
    def clean_faction(self):
        faction = self.cleaned_data.get('faction')
        if not faction:
            raise ValidationError("Please select a faction for each player")
        return faction