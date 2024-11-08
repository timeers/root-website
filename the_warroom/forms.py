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
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'league', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }

class EffortCreateForm(forms.ModelForm):
    required_css_class = 'required-field'
    class Meta:
        model = Effort
        fields = ['seat', 'player', 'faction', 'vagabond', 'score', 'win', 'dominance', 'coalition_with']

    def clean(self):
        cleaned_data = super().clean()
        faction = cleaned_data.get('faction')

        if faction is None or faction == "":
            raise ValidationError("Please select a faction for each player.")

        return cleaned_data
    
class EffortImportForm(forms.ModelForm):
    required_css_class = 'required-field'
    class Meta:
        model = Effort
        fields = ['seat', 'player', 'faction', 'vagabond', 'score', 'win', 'dominance', 'coalition_with', 'game', 'date_posted']

    def clean(self):
        cleaned_data = super().clean()
        faction = cleaned_data.get('faction')

        if faction is None or faction == "":
            raise ValidationError("Please select a faction for each player.")

        return cleaned_data
    
class GameImportForm(forms.ModelForm):  

    class Meta:
        model = Game
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'league', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }