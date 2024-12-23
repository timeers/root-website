from django import forms
from django.utils import timezone
from .models import Effort, Game, TurnScore, ScoreCard, Round, Tournament
from the_keep.models import Hireling, Landmark, Deck, Map, Faction, Vagabond
from the_gatehouse.models import Profile
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
        fields = ['solo', 'coop', 'test_match', 'round', 'platform', 'type', 'deck', 'map', 'random_clearing', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link']
        widgets = {
            'type': forms.RadioSelect,
        }
        labels = {
            'round': "Tournament",
            }

    def __init__(self, *args, user=None, effort_formset=None, **kwargs):
        # Call the parent constructor
        super(GameCreateForm, self).__init__(*args, **kwargs)

        self.effort_formset = effort_formset 

        # Filter for only Official content if not a member of Weird Root
        if not user.profile.weird:
            self.fields['deck'].queryset = Deck.objects.filter(official=True)
            self.fields['map'].queryset = Map.objects.filter(official=True)
            self.fields['undrafted_faction'].queryset = Faction.objects.filter(official=True)
            self.fields['undrafted_vagabond'].queryset = Vagabond.objects.filter(official=True)
            self.fields['landmarks'].queryset = Landmark.objects.filter(official=True)
            self.fields['hirelings'].queryset = Hireling.objects.filter(official=True)

        if user:
            if user.profile.admin:
                # Select all active tournament roundds (Admin can record games for any tournament)
                active_rounds = Round.objects.filter(
                    Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True), start_date__lt=timezone.now())
            else:
                # Select rounds in ongoing tournaments where the user is a player
                active_rounds = Round.objects.filter(
                    Q(end_date__gt=timezone.now()) | Q(end_date__isnull=True), start_date__lt=timezone.now(),
                    tournament__players=user.profile
                    )
                active_rounds = active_rounds.filter(
                    Q(players__isnull=False) & Q(players__in=[user.profile]) | Q(players__isnull=True)
)
            
            self.fields['round'].queryset = active_rounds


        # This needs to be adapted to work. But if admin can record any tournament game it might not be a problem.
        # Except for concluded tournaments....
        # If a specific round is provided, add it to the queryset
        # if round:
        #     # Ensure round is a single object, otherwise handle accordingly
        #     if isinstance(round, Round):
        #         self.fields['round'].queryset |= Round.objects.filter(id=round.id)
                # if round.designer != user.profile:
                #     self.fields['round'].disabled = True  # Disable the field


    def clean(self):
        validation_errors_to_display = []  # List to store error messages
        cleaned_data = super().clean()

        round = cleaned_data.get('round')
        platform = cleaned_data.get('platform')
        link = cleaned_data.get('link')
        map = cleaned_data.get('map')
        deck = cleaned_data.get('deck')
        landmarks = cleaned_data.get('landmarks')
        hirelings = cleaned_data.get('hirelings')
        if round:
            # Check that the deck, landmarks, hirelings and map are registered for the tournament
            tournament_maps = round.tournament.maps.all()
            tournament_decks = round.tournament.decks.all()
            if landmarks:
                tournament_landmarks = round.tournament.landmarks.all()
                for landmark in landmarks:
                    if landmark not in tournament_landmarks:
                        validation_errors_to_display.append(f'{landmark} Landmark is not playable in {round.tournament}')
            if hirelings:
                tournament_hirelings = round.tournament.hirelings.all()
                for hireling in hirelings:
                    if hireling not in tournament_hirelings:
                        validation_errors_to_display.append(f'{hireling} Hireling is not playable in {round.tournament}')
            if map not in tournament_maps:
                validation_errors_to_display.append(f'{map} Map is not playable in {round.tournament}')
            if deck not in tournament_decks:
                validation_errors_to_display.append(f'{deck} Deck is not playable in {round.tournament}')
   
        if self.effort_formset.is_valid():
            faction_roster = set()
            vagabond_roster = set()
            player_roster = set()
            test_match = False
            vagabond_count = 1
            win_count = 0
            clockwork_count = 0
            human_count = 0
            coalition_count = 0

            for effort_form in self.effort_formset.forms:
                faction = effort_form.cleaned_data.get('faction')
                vagabond = effort_form.cleaned_data.get('vagabond')
                win = effort_form.cleaned_data.get('win')
                coalition = effort_form.cleaned_data.get('coalition_with')
                player = effort_form.cleaned_data.get('player')
                
                if coalition:
                    coalition_count += 1

                if win and not coalition:
                    win_count += 1

                if player:
                    if player in player_roster:
                        test_match = True
                    else:
                        player_roster.add(player)

                if faction:
                    if faction.type == "C": # Count the Clockwork Factions
                        clockwork_count += 1
                    else:
                        human_count += 1 #Count the regular Factions
                    if faction in faction_roster:
                        # Error for duplicate faction
                        if faction.title == 'Vagabond':
                            vagabond_count += 1
                            if vagabond_count > 2:
                                validation_errors_to_display.append(f"Extra {faction} selected") 
                        else:
                            validation_errors_to_display.append(f'{faction} selected twice') 
                    else:
                        faction_roster.add(faction)
                if vagabond:
                    if vagabond in vagabond_roster:
                        # Error for duplicate vagabond
                        validation_errors_to_display.append(f'{vagabond} {faction} selected twice') 
                    else:
                        vagabond_roster.add(vagabond)

            # Winner Required
            if win_count == 0:
                validation_errors_to_display.append(f'Select at winner')

            # Multiple Winners and Multiple Humans means Coop Match
            elif win_count > 1 and human_count > 1:
                cleaned_data['coop'] = True
            else:
                cleaned_data['coop'] = False

            # One human and at least one clockwork means Solo
            if human_count == 1 and clockwork_count > 0:
                cleaned_data['solo'] = True
            else:
                cleaned_data['solo'] = False
            
            # One player playing multiple hands means Playtest
            if test_match:
                cleaned_data['test_match'] = True
            else:
                cleaned_data['test_match'] = False

            if len(faction_roster) + max(0, vagabond_count-1) < 2:
                validation_errors_to_display.append(f'Select at least two factions') 

            # Validate Tournament Game Settings
            if round:
                if win_count > 1 and not round.tournament.teams:
                    validation_errors_to_display.append(f'Only one winner is allowed')
                if coalition_count > round.tournament.coalitions:
                    validation_errors_to_display.append(f'This type of coalition is not allowed')
                # Error if platform does not match tournament platform
                if round.tournament.platform and platform != round.tournament.platform:
                    # raise ValidationError(f"Please select {round.tournament.platform} for this {round.tournament} Game.")
                    validation_errors_to_display.append(f"Select {round.tournament.platform} for this {round.tournament} Game")  # Store the message
                # Error if link not supplied for 
                if round.tournament.link_required and not link:
                    validation_errors_to_display.append("Provide a unique link to this game's thread")  # Store the message
                
            
                player_roster = set()  # Set to track unique players
                current_players = round.current_player_queryset()  # Assuming round is available and has a tournament
                # eliminated_players = round.tournament.eliminated_players()
                tournament_factions = round.tournament.factions.all()
                tournament_vagabonds = round.tournament.vagabonds.all()

                # Loop through each form in the formset
                for effort_form in self.effort_formset.forms:
                    # Get the player field from the cleaned data of the form
                    player = effort_form.cleaned_data.get('player')

                    # Check if the player exists in the player_roster
                    if player:
                        if player in player_roster:
                            # Error for duplicate player
                            validation_errors_to_display.append(f'{player} selected twice') 
                        else:
                            # Add the player to the game roster (no duplicates due to set)
                            player_roster.add(player)
                    else:
                        faction = effort_form.cleaned_data.get('faction')
                        if faction:
                            validation_errors_to_display.append(f'Select a player for each faction') 
                # Check tournament's max and min player counts
                if len(player_roster) > round.tournament.max_players:
                    validation_errors_to_display.append(f'Over {round.tournament} maximum player count') 
                if len(player_roster) < round.tournament.min_players:
                    validation_errors_to_display.append(f'Under {round.tournament} minimum player count') 

                # Check each player in the player_roster
                for player in player_roster:
                    if player not in current_players:
                        # if player in eliminated_players:
                        #     validation_errors_to_display.append(f'{player} was previously eliminated from {round.tournament}')
                        # else:
                        validation_errors_to_display.append(f'{player} is not registered for {round.tournament}')
                for faction in faction_roster:
                    if faction not in tournament_factions:
                        validation_errors_to_display.append(f'The Faction {faction} is not playable in {round.tournament}')
                for vagabond in vagabond_roster:
                    if vagabond not in tournament_vagabonds:
                        validation_errors_to_display.append(f'The Vagabond {vagabond} is not playable in {round.tournament}')


        if validation_errors_to_display:
            raise ValidationError(validation_errors_to_display)
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
    def __init__(self, *args, **kwargs):
        # Check if 'game' is passed in kwargs and set it
        self.game = kwargs.pop('game', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        validation_errors_to_display = []
        cleaned_data = super().clean()
        faction = cleaned_data.get('faction')
        player = cleaned_data.get('player')
        dominance = cleaned_data.get('dominance')
        score = cleaned_data.get('score')
        vagabond = cleaned_data.get('vagabond')
        coalition = cleaned_data.get('coalition_with')

        if faction is None or faction == "":
            # raise ValidationError(f"Faction required")
            validation_errors_to_display.append("Faction required")
        
        elif faction.title == 'Vagabond' and not vagabond:
            # raise ValidationError('Select a Vagabond.')
            validation_errors_to_display.append('Select a Vagabond')
                # If captains are assigned ensure no more than 3 captains are assigned
        elif faction.title == "Knaves of the Deepwood":
            captains = cleaned_data.get('captains')
            if captains.count() != 3 and captains.count() != 0:
                # raise ValidationError({'captains': 'Please assign 3 Vagabonds as captains.'})
                validation_errors_to_display.append('Please assign 3 Vagabonds as captains')
        elif faction.type == "C" and player:
            validation_errors_to_display.append('This is a Clockwork faction and cannot have a "player"')

        if not dominance and not score and not coalition:
            # raise ValidationError(f"Score or Dominance required")
            validation_errors_to_display.append('Score or Dominance required')

        


            
        if validation_errors_to_display:
            raise ValidationError(validation_errors_to_display)
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
        fields = ['deck', 'map', 'random_clearing', 'type', 'platform', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'link', 'date_posted']



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


class TournamentCreateForm(forms.ModelForm):
    PLATFORM_CHOICES = [
        (None, 'Any platform'),  # Represents the null choice
        ('Tabletop Simulator', 'Tabletop Simulator'),
        ('Root Digital', 'Root Digital'),
        ('In Person', 'In Person'),
    ]
    platform = forms.ChoiceField(
        choices=PLATFORM_CHOICES,
        initial=None,  # Set the default choice to None
        required=False,
        label='Required Platform'
    )
    class Meta:
        model = Tournament
        fields = ['name', 'start_date', 'end_date', 'max_players', 'min_players', 'game_threshold', 'platform', 'include_fan_content', 'include_clockwork', 'link_required', 'coalitions', 'teams']
        labels = {
            'name': 'Tournament Name',
            'start_date': 'Start Date',
            'end_date': 'End Date (Optional)',
            'game_threshold': 'Leaderboard Threshold',
            'link_required': 'Require Link with Game Submission',
            'coalitions': 'Coalitions Allowed',
            'teams': 'Allow for multiple non-Coalition Wins (Teams)',
        }
    def __init__(self, *args, **kwargs):
        super(TournamentCreateForm, self).__init__(*args, **kwargs)
        # Set the initial value for 'start_date' to the current time
        if not self.instance.pk:  # Only set this if the instance is new
            self.fields['start_date'].initial = timezone.now()
            
    def save(self, commit=True):
        """
        Save the round, associating it with the tournament if it's a new round.
        If updating an existing round, retain the current tournament association.
        """
        instance = super().save(commit=False)

        # Check if this is a new tournament to set the default assets
        set_default_assets = False
        if not instance.pk:
            set_default_assets = True
            
        if commit:
            instance.save()

        if set_default_assets:
            if instance.platform == "Root Digital":
                instance.factions.set(Faction.objects.filter(in_root_digital=True).exclude(type="C"))
                instance.maps.set(Map.objects.filter(in_root_digital=True))
                instance.decks.set(Deck.objects.filter(in_root_digital=True))
                instance.vagabonds.set(Vagabond.objects.filter(in_root_digital=True))
            else:
                instance.factions.set(Faction.objects.filter(official=True, stable=True).exclude(type="C"))
                instance.maps.set(Map.objects.filter(official=True, stable=True))
                instance.decks.set(Deck.objects.filter(official=True, stable=True))
                instance.vagabonds.set(Vagabond.objects.filter(official=True, stable=True))


        return instance



class RoundCreateForm(forms.ModelForm):
    class Meta:
        model = Round
        fields = ['name', 'round_number', 'start_date', 'end_date', 'game_threshold']
        labels = {
            'name': 'Tournament Round Name',
            'round_number': 'Round #',
            'game_threshold': 'Leaderboard Threshold',
        }

    def __init__(self, *args, tournament=None, current_round=None, **kwargs):
        """
        Initialize the form, optionally accepting a tournament argument,
        and set the tournament field when saving.
        """
        # Store the tournament instance
        self.tournament = tournament
        super().__init__(*args, **kwargs)

        self.fields['game_threshold'].initial = tournament.game_threshold

        # Set the initial value for 'start_date' to the current time
        if not self.instance.pk:  # Only set this if the instance is new
            self.fields['start_date'].initial = timezone.now()

        # Ensure current_round is passed and not None
        if current_round is not None:
            # # Set the minimum value of round_number to current_round
            # self.fields['round_number'].min_value = current_round
            # Set an initial value for round_number to current_round
            self.fields['round_number'].initial = current_round

    def save(self, commit=True):
        """
        Save the round, associating it with the tournament if it's a new round.
        If updating an existing round, retain the current tournament association.
        """
        instance = super().save(commit=False)

        # Set the tournament only if the round is being created (i.e., the tournament is passed)
        if self.tournament and not instance.pk:
            instance.tournament = self.tournament

        if commit:
            instance.save()

        return instance
    def clean(self):
        cleaned_data = super().clean()

        tournament = self.tournament
        # tournament = cleaned_data.get('tournament')
        name = cleaned_data.get('name')

        # Exclude the current round from validation if it exists
        if self.instance.id:
            # Exclude the current round's ID from the query
            qs = Round.objects.filter(tournament=tournament, name=name).exclude(id=self.instance.id)
        else:
            # If no current round is passed, just check for a round with the same name
            qs = Round.objects.filter(tournament=tournament, name=name)

        if qs.exists():
            raise ValidationError(f"A round with this name already exists for {tournament.name}")
        return cleaned_data

class TournamentManageAssetsForm(forms.Form):
    # Multiple select field for Factions not yet in the tournament
    available_factions = forms.ModelMultipleChoiceField(
        queryset=Faction.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Factions'
    )

    # Multiple select field for Factions already in the tournament
    tournament_factions = forms.ModelMultipleChoiceField(
        queryset=Faction.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Factions'
    )

    # Multiple select field for Decks not yet in the tournament
    available_decks = forms.ModelMultipleChoiceField(
        queryset=Deck.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Decks'
    )

    # Multiple select field for Decks already in the tournament
    tournament_decks = forms.ModelMultipleChoiceField(
        queryset=Deck.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Decks'
    )

    # Multiple select field for Maps not yet in the tournament
    available_maps = forms.ModelMultipleChoiceField(
        queryset=Map.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Maps'
    )

    # Multiple select field for Maps already in the tournament
    tournament_maps = forms.ModelMultipleChoiceField(
        queryset=Map.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Maps'
    )

    # Multiple select field for Landmarks not yet in the tournament
    available_landmarks = forms.ModelMultipleChoiceField(
        queryset=Landmark.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Landmarks'
    )

    # Multiple select field for Landmarks already in the tournament
    tournament_landmarks = forms.ModelMultipleChoiceField(
        queryset=Landmark.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Landmarks'
    )

    # Multiple select field for Hirelings not yet in the tournament
    available_hirelings = forms.ModelMultipleChoiceField(
        queryset=Hireling.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Hirelings'
    )

    # Multiple select field for Hirelings already in the tournament
    tournament_hirelings = forms.ModelMultipleChoiceField(
        queryset=Hireling.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Hirelings'
    )

    # Multiple select field for Vagabonds not yet in the tournament
    available_vagabonds = forms.ModelMultipleChoiceField(
        queryset=Vagabond.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Vagabonds'
    )

    # Multiple select field for Vagabonds already in the tournament
    tournament_vagabonds = forms.ModelMultipleChoiceField(
        queryset=Vagabond.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Vagabonds'
    )


    def __init__(self, *args, tournament=None, **kwargs):
        self.tournament = tournament 
        # Get the tournament and querysets passed from the view
        available_factions_query = kwargs.pop('available_factions_query', None)
        tournament_factions_query = kwargs.pop('tournament_factions_query', None)
        available_decks_query = kwargs.pop('available_decks_query', None)
        tournament_decks_query = kwargs.pop('tournament_decks_query', None)
        available_maps_query = kwargs.pop('available_maps_query', None)
        tournament_maps_query = kwargs.pop('tournament_maps_query', None)
        available_landmarks_query = kwargs.pop('available_landmarks_query', None)
        tournament_landmarks_query = kwargs.pop('tournament_landmarks_query', None)
        available_hirelings_query = kwargs.pop('available_hirelings_query', None)
        tournament_hirelings_query = kwargs.pop('tournament_hirelings_query', None)
        available_vagabonds_query = kwargs.pop('available_vagabonds_query', None)
        tournament_vagabonds_query = kwargs.pop('tournament_vagabonds_query', None)

        super().__init__(*args, **kwargs)

        # Set the querysets for the fields
        self.fields['available_factions'].queryset = available_factions_query
        self.fields['tournament_factions'].queryset = tournament_factions_query
        self.fields['available_decks'].queryset = available_decks_query
        self.fields['tournament_decks'].queryset = tournament_decks_query
        self.fields['available_maps'].queryset = available_maps_query
        self.fields['tournament_maps'].queryset = tournament_maps_query
        self.fields['available_landmarks'].queryset = available_landmarks_query
        self.fields['tournament_landmarks'].queryset = tournament_landmarks_query
        self.fields['available_hirelings'].queryset = available_hirelings_query
        self.fields['tournament_hirelings'].queryset = tournament_hirelings_query
        self.fields['available_vagabonds'].queryset = available_vagabonds_query
        self.fields['tournament_vagabonds'].queryset = tournament_vagabonds_query
        
    def clean(self):
        cleaned_data = super().clean()
        print(cleaned_data)
        return cleaned_data
    
    def save(self):
        if self.cleaned_data:
            # Add selected factions, decks, maps, landmarks, hirelings, and vagabonds to the tournament
            for faction in self.cleaned_data['available_factions']:
                self.tournament.factions.add(faction)

            for faction in self.cleaned_data['tournament_factions']:
                self.tournament.factions.remove(faction)

            for deck in self.cleaned_data['available_decks']:
                self.tournament.decks.add(deck)

            for deck in self.cleaned_data['tournament_decks']:
                self.tournament.decks.remove(deck)

            for map in self.cleaned_data['available_maps']:
                self.tournament.maps.add(map)

            for map in self.cleaned_data['tournament_maps']:
                self.tournament.maps.remove(map)

            for landmark in self.cleaned_data['available_landmarks']:
                self.tournament.landmarks.add(landmark)

            for landmark in self.cleaned_data['tournament_landmarks']:
                self.tournament.landmarks.remove(landmark)

            for hireling in self.cleaned_data['available_hirelings']:
                self.tournament.hirelings.add(hireling)

            for hireling in self.cleaned_data['tournament_hirelings']:
                self.tournament.hirelings.remove(hireling)

            for vagabond in self.cleaned_data['available_vagabonds']:
                self.tournament.vagabonds.add(vagabond)

            for vagabond in self.cleaned_data['tournament_vagabonds']:
                self.tournament.vagabonds.remove(vagabond)



class RoundManagePlayersForm(forms.Form):
    # Multiple select field for players not yet in the tournament
    available_players = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Players'
    )

    # Multiple select field for players already in the tournament
    current_players = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Players'
    )

    # Checkbox to select all players in the available players list
    add_all_players = forms.BooleanField(
        required=False,
        label='Add All Players'
    )
    # Checkbox to select all players in the available players list
    remove_all_players = forms.BooleanField(
        required=False,
        label='Remove All Players'
    )

    def clean(self):
        cleaned_data = super().clean()

        # Check if the "Add All Players" checkbox is checked
        add_all = cleaned_data.get('add_all_players')

        if add_all:
            # If checked, add all available players to the available_players field
            cleaned_data['available_players'] = list(self.fields['available_players'].queryset)

        # Check if the "Remove All Players" checkbox is checked
        remove_all = cleaned_data.get('remove_all_players')

        if remove_all:
            # If checked, add all current players to the current_players field
            cleaned_data['current_players'] = list(self.fields['current_players'].queryset)

        return cleaned_data

    def __init__(self, *args, round=None, **kwargs):
        self.round = round 
        # Get the tournament and querysets passed from the view
        available_players_query = kwargs.pop('available_players_query', None)
        current_players_query = kwargs.pop('current_players_query', None)

        super().__init__(*args, **kwargs)

        # Set the querysets for the fields
        self.fields['available_players'].queryset = available_players_query
        self.fields['current_players'].queryset = current_players_query

    
    def save(self):
        if self.cleaned_data:
            # Add selected players to the round
            for player in self.cleaned_data['available_players']:
                self.round.players.add(player)

            for player in self.cleaned_data['current_players']:
                self.round.players.remove(player)



class TournamentManagePlayersForm(forms.Form):
    # Multiple select field for players not yet in the tournament
    available_players = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Players'
    )

    # Multiple select field for players already in the tournament
    current_players = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Players'
    )

    # Multiple select field for players already in the tournament
    eliminated_players = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Eliminate/Ban Players'
    )


    # Don't want to be able to add all players to a tournament.
    # # Checkbox to select all players in the available players list
    # add_all_players = forms.BooleanField(
    #     required=False,
    #     label='Add All Players'
    # )

    # Checkbox to select all players in the available players list
    remove_all_players = forms.BooleanField(
        required=False,
        label='Remove All Players'
    )

    def clean(self):
        cleaned_data = super().clean()

        # # Check if the "Add All Players" checkbox is checked
        # add_all = cleaned_data.get('add_all_players')
        # if add_all:
        #     # If checked, add all available players to the available_players field
        #     cleaned_data['available_players'] = list(self.fields['available_players'].queryset)

        # Check if the "Remove All Players" checkbox is checked

        remove_all = cleaned_data.get('remove_all_players')

        if remove_all:
            # If checked, add all current players to the current_players field
            cleaned_data['current_players'] = list(self.fields['current_players'].queryset)

        return cleaned_data


    def __init__(self, *args, tournament=None, **kwargs):
        self.tournament = tournament 
        # Get the tournament and querysets passed from the view
        available_players_query = kwargs.pop('available_players_query', None)
        current_players_query = kwargs.pop('current_players_query', None)
        

        super().__init__(*args, **kwargs)

        # Set the querysets for the fields
        self.fields['available_players'].queryset = available_players_query
        self.fields['current_players'].queryset = current_players_query
        self.fields['eliminated_players'].queryset = current_players_query
    

    def save(self):
        if self.cleaned_data:
            # Add selected players to the tournament
            self.tournament.players.add(*self.cleaned_data['available_players'])
            self.tournament.eliminated_players.remove(*self.cleaned_data['available_players'])

            # Add eliminated players to the tournament and remove them from players
            self.tournament.eliminated_players.add(*self.cleaned_data['eliminated_players'])
            self.tournament.players.remove(*self.cleaned_data['eliminated_players'])

            # Remove selected current players from the tournament players
            self.tournament.players.remove(*self.cleaned_data['current_players'])



            # If players were removed from the tournament, remove them from all rounds
            if self.cleaned_data['current_players']:
                # Get all rounds in the tournament where players need to be removed
                rounds = self.tournament.rounds.all()

                # Create a set of players to remove
                players_to_remove = set(self.cleaned_data['current_players']) | set(self.cleaned_data['eliminated_players'])

                # For each round, remove the players in bulk (minimizes queries)
                for round in rounds:
                    round.players.remove(*players_to_remove)
