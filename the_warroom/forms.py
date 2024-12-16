from django import forms
from .models import Effort, Game, TurnScore, ScoreCard, Round
from the_keep.models import Hireling, Landmark, Deck, Map, Faction, Vagabond
from django.core.exceptions import ValidationError
from django.db.models import Q


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
    PLATFORM_CHOICES = [
        ('Tabletop Simulator', 'Tabletop Simulator'),
        ('Root Digital', 'Root Digital'),
        ('In Person', 'In Person'),
    ]
    platform = forms.ChoiceField(
        choices=PLATFORM_CHOICES, initial="Tabletop Simulator",
        required=True
    )
    class Meta:
        model = Game
        fields = ['platform', 'round', 'type', 'league', 'deck', 'map', 'random_clearing', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }
        labels = {
            'round': "Tournament",
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

        if user:
            # Select rounds in ongoing tournaments where the user is a player
            active_rounds = Round.objects.filter(tournament__status="ongoing",
                            tournament__players=user.profile)
            # Filter rounds to only include the current round based on end_date
            current_rounds = [round for round in active_rounds if round.is_current_round()]
            user_in_rounds = []

            for round in current_rounds:
                # Call the 'get_active_player_queryset()' method and check if the user is in the returned queryset
                if user.profile in round.tournament.get_active_player_queryset():  # Corrected method call
                    user_in_rounds.append(round)

            # Set the queryset to the filtered list of current rounds
            self.fields['round'].queryset = Round.objects.filter(id__in=[round.id for round in user_in_rounds])

        # If a specific round is provided, add it to the queryset
        # if round:
        #     # Ensure round is a single object, otherwise handle accordingly
        #     if isinstance(round, Round):
        #         self.fields['round'].queryset |= Round.objects.filter(id=round.id)
                # if round.designer != user.profile:
                #     self.fields['round'].disabled = True  # Disable the field
            

        # If no rounds exist in the queryset, hide the field
        if not self.fields['round'].queryset.exists():
            del self.fields['round']
    def clean(self):
        cleaned_data = super().clean()
        print(cleaned_data)
        round = cleaned_data.get('round')
        platform = cleaned_data.get('platform')
        link = cleaned_data.get('link')
        # print(f'Selected: {platform} for {round.tournament.platform} Tournament')
       
        if round:
            # Error if platform does not match tournament platform
            if round.tournament.platform and platform != round.tournament.platform:
                raise ValidationError(f"Please select {round.tournament.platform} for this {round} Game.")
            # Error if link not supplied for 
            if round.tournament.link_required and not link:
                raise ValidationError("Please provide a link to this game's thread.")
        return cleaned_data

class EffortCreateForm(forms.ModelForm):
    required_css_class = 'required-field'

    class Meta:
        model = Effort
        fields = ['player', 'faction', 'vagabond', 'captains', 'score', 'win', 'dominance', 'coalition_with']
        captains = forms.ModelMultipleChoiceField(
            queryset=Vagabond.objects.all(),
            widget=forms.SelectMultiple(attrs={'class': 'select2'}),
        )
    def clean(self):
        cleaned_data = super().clean()
        faction = cleaned_data.get('faction')
        player = cleaned_data.get('player')
        if faction is None or faction == "":
            raise ValidationError(f"Please select a faction for {player}.")
        # If captains are assigned ensure no more than 3 captains are assigned
        if faction.title == "Knaves of the Deepwood":
            captains = cleaned_data.get('captains')
            if captains.count() != 3 and captains.count() != 0:
                raise ValidationError({'captains': 'Please assign 3 Vagabonds as captains.'})
        return cleaned_data

class TurnScoreCreateForm(forms.ModelForm):
    class Meta:
        model = TurnScore
        fields = ['id', 'turn_number', 'faction_points', 'crafting_points', 'battle_points', 'other_points', 'dominance']
    faction_points = forms.IntegerField(required=False, initial=0)
    crafting_points = forms.IntegerField(required=False, initial=0)
    battle_points = forms.IntegerField(required=False, initial=0)
    other_points = forms.IntegerField(required=False, initial=0)
    # Custom validation for turn_number field
    def clean_turn_number(self):
        turn_number = self.cleaned_data.get('turn_number')
        if turn_number < 1:
            raise forms.ValidationError("Turn number must be greater than or equal to 1.")
        return turn_number

    # Custom validation for the total_points (can be a derived field if needed)
    def clean_total_points(self):
        faction_points = self.cleaned_data.get('faction_points', 0)
        crafting_points = self.cleaned_data.get('crafting_points', 0)
        battle_points = self.cleaned_data.get('battle_points', 0)
        other_points = self.cleaned_data.get('other_points', 0)

        # Ensure the total points are consistent
        total_points = faction_points + crafting_points + battle_points + other_points
        if total_points != self.cleaned_data.get('total_points', 0):
            raise forms.ValidationError("The total points do not match the sum of faction, crafting, battle, and other points.")

        return total_points
    def clean(self):
        cleaned_data = super().clean()

        # Set any missing points to 0
        for field in ['faction_points', 'crafting_points', 'battle_points', 'other_points']:
            if field in cleaned_data and not cleaned_data.get(field):
                cleaned_data[field] = 0

        return cleaned_data
    
class ScoreCardCreateForm(forms.ModelForm):
    class Meta:
        model = ScoreCard
        fields = ['faction', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
        }

    def __init__(self, *args, user=None, faction=None, **kwargs):
        # Call the parent constructor
        super(ScoreCardCreateForm, self).__init__(*args, **kwargs)
        # Check if faction is passed to the form
        if faction:
            self.fields['faction'].queryset = Faction.objects.filter(id=faction)
            self.fields['faction'].initial = faction  # Set the initial value of the faction
            self.fields['faction'].empty_label = None
            # self.fields['faction'].disabled = True
        self.user = user  # Save the user parameter for later use

    def clean(self):
        cleaned_data = super().clean()
        faction = cleaned_data.get('faction')
        if faction is None:
            raise forms.ValidationError("Faction cannot be empty.")
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
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'league', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link', 'date_posted']



class AssignScorecardForm(forms.ModelForm):  
    scorecard = forms.ModelChoiceField(queryset=ScoreCard.objects.all(), required=True)

    class Meta:
        model = Effort
        fields = ['scorecard']
        widgets = {
            'scorecard': forms.ModelChoiceField(queryset=ScoreCard.objects.all(), required=True),
        }

    def __init__(self, *args, user=None, total_points=None, faction=None, selected_scorecard=None, dominance=False, **kwargs):
        # Call the parent constructor
        super(AssignScorecardForm, self).__init__(*args, **kwargs)
        print(f'Selected Scorecard: {selected_scorecard}')
        queryset = ScoreCard.objects.filter(
            Q(recorder=user.profile) & 
            Q(effort=None) & 
            Q(faction=faction) &
            Q(total_points=total_points)
        )

        # If dominance is True, add the dominance condition
        if dominance:
            dominance_queryset = ScoreCard.objects.filter(
                Q(recorder=user.profile) &
                Q(effort=None) &
                Q(faction=faction) &
                Q(turns__dominance=True)
            )
            # Combine the regular and dominance querysets, ensuring distinct results
            queryset = queryset | dominance_queryset

        # If selected_scorecard is provided, filter by that as well
        if selected_scorecard:
            queryset = queryset.filter(Q(id=selected_scorecard))
        # Set the queryset for the scorecard field and ensure it has a non-empty label
        self.fields['scorecard'].queryset = queryset.distinct()
        self.fields['scorecard'].empty_label = None
