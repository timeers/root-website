from django import forms
from .models import Effort, Game
from the_keep.models import Hireling, Landmark, Deck, Map, Faction, Vagabond
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

    def __init__(self, *args, user=None, **kwargs):
        # Call the parent constructor
        super(GameCreateForm, self).__init__(*args, **kwargs)
        # Filter for only Official content if not a member of Weird Root
        if not user.profile.weird:
            self.fields['deck'].queryset = Deck.objects.filter(official=True)
            self.fields['map'].queryset = Map.objects.filter(official=True)
            self.fields['undrafted_faction'].queryset = Faction.objects.filter(official=True)
            self.fields['undrafted_vagabond'].queryset = Vagabond.objects.filter(official=True)
            self.fields['landmarks'].queryset = Landmark.objects.filter(official=True)
            self.fields['hirelings'].queryset = Hireling.objects.filter(official=True)
            

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
    # required_css_class = 'required-field'
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