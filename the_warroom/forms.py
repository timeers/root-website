from django import forms
from .models import Effort, Game
from blog.models import Hireling, Landmark
from django.core.exceptions import ValidationError

class GameCreateForm(forms.ModelForm):  
    required_css_class = 'required-field'
    link = forms.CharField(help_text='Post link to Discord Thread (optional)', required=False)
    hirelings = forms.ModelMultipleChoiceField(required=False, 
                queryset=Hireling.objects.all(),
                widget=forms.SelectMultiple
                )
    landmarks = forms.ModelMultipleChoiceField(required=False, 
                queryset=Landmark.objects.all(),
                widget=forms.SelectMultiple
                )
    class Meta:
        model = Game
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'league', 'undrafted_faction', 'landmarks', 'hirelings', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }

class EffortCreateForm(forms.ModelForm):
    required_css_class = 'required-field'
    class Meta:
        model = Effort
        fields = ['seat', 'player', 'faction', 'vagabond', 'score', 'win', 'dominance', 'coalition_with']


    # Why aren't either of these working? It should throw a validation error.... right?
    def clean_faction(self):
        faction = self.cleaned_data.get('faction')
        if not faction:
            raise ValidationError("Please select a faction for each player")
        return faction
    