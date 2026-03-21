from django import forms
from django.utils import timezone
from .models import Effort, Game, TurnScore, ScoreCard, Round, Stage, Tournament, Match, MatchSeat, AssetModeChoices, PlatformChoices
from the_keep.models import Hireling, Landmark, Deck, Map, Faction, Vagabond, Tweak
from the_gatehouse.models import Profile
from django.core.exceptions import ValidationError
from django.db.models import Max, Q
from django.utils.translation import gettext as _




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
    tweaks = forms.ModelMultipleChoiceField(required=False, 
                queryset=Tweak.objects.all(),
                widget=forms.SelectMultiple,
                label="House Rules",
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
        fields = ['solo', 'coop', 'official', 'test_match', 'round', 
                  'platform', 'type', 'deck', 'map', 'random_clearing', 
                  'undrafted_faction', 'undrafted_vagabond', 'landmarks', 
                  'hirelings', 'link', 'tweaks', 'final', 'notes', 'nickname', 'reach_value', 'date_posted']
        widgets = {
            'type': forms.RadioSelect,
        }
        labels = {
            'round': "Series",
            }

    def __init__(self, *args, user=None, effort_formset=None, round=None, **kwargs):
        # Call the parent constructor
        super(GameCreateForm, self).__init__(*args, **kwargs)

        # Get today's date in the required format (YYYY-MM-DD)
        today = timezone.now().date()

        # self.fields['date_posted'].widget.attrs.update({'class': 'datepicker'}) 
        self.fields['date_posted'].widget = forms.DateInput(
            format='%Y-%m-%d', 
            attrs={
                'class': 'datepicker form-control',
                'max': today  # Set the max attribute to today's date
            }
        )
        self.fields['notes'].widget.attrs.update({
            'rows': '2',
            'placeholder': 'Game Notes...',
            'class': 'form-control full-width', 
        })
        self.fields['link'].widget.attrs.update({
            'placeholder': 'Link to Game Thread',
            'class': 'form-control full-width', 
        })
        self.fields['nickname'].widget.attrs.update({
            'placeholder': _('Game Nickname (optional)'),
            'class': 'form-control full-width', 
        })

        self.effort_formset = effort_formset 

        # Filter for only Official content if not a member of Weird Root
        if not user.profile.weird:
            self.fields['deck'].queryset = Deck.objects.filter(official=True)
            self.fields['map'].queryset = Map.objects.filter(official=True)
            self.fields['undrafted_faction'].queryset = Faction.objects.filter(official=True)
            self.fields['undrafted_vagabond'].queryset = Vagabond.objects.filter(official=True)
            self.fields['landmarks'].queryset = Landmark.objects.filter(official=True)
            self.fields['tweaks'].queryset = Tweak.objects.filter(official=True)
            self.fields['hirelings'].queryset = Hireling.objects.filter(official=True)

        if user:
            user_guilds = user.profile.guilds.all()
            if user.profile.admin:
                # Select all active tournament rounds (Admin can record games for any tournament)
                # Filter for available rounds (is_active=True + within date range)
                now = timezone.now().date()
                active_rounds = Round.objects.filter(
                    is_active=True,
                    stage__is_active=True,
                    stage__tournament__is_active=True
                ).filter(
                    Q(start_date__isnull=True) | Q(start_date__lte=now)
                ).exclude(
                    end_date__lt=now
                ).distinct()
            else:

                now = timezone.now().date()
                active_rounds = Round.objects.filter(
                    Q(
                        Q(stage__participants__tournament_player__profile=user.profile) |  # player is a participant of the stage
                        Q(stage__tournament__designer=user.profile) |  # or the creator of the tournament
                        Q(stage__tournament__guild__in=user_guilds)  # or in the tournament's guild
                    ),
                    is_active=True,
                    stage__is_active=True,
                    stage__tournament__is_active=True
                ).filter(
                    Q(start_date__isnull=True) | Q(start_date__lte=now)
                ).exclude(
                    end_date__lt=now
                ).exclude(
                    # Hide bracket rounds from regular players (moderators/designers can still see them)
                    Q(series__isnull=False),
                    # Removed for now. Should add match creation in the Tournament instead of here
                    # ~Q(stage__tournament__designer=user.profile),
                    # ~Q(stage__tournament__moderators=user.profile),
                ).distinct()


            self.fields['round'].queryset = active_rounds
            if round and round in active_rounds:
                self.fields['round'].queryset = Round.objects.filter(pk=round.pk)
                self.fields['round'].empty_label = None
                # self.fields['round'].initial = round


    def clean(self):
        validation_errors_to_display = []  # List to store error messages
        progress_errors_to_display = []  # List to store error messages when only saving progress
        cleaned_data = super().clean()


        map = cleaned_data.get('map')
        deck = cleaned_data.get('deck')
        final = cleaned_data.get('final')

         
        round = cleaned_data.get('round')
        platform = cleaned_data.get('platform')
        link = cleaned_data.get('link')
        landmarks = cleaned_data.get('landmarks')
        tweaks = cleaned_data.get('tweaks')
        hirelings = cleaned_data.get('hirelings')
        if round:
            tournament = round.get_tournament()
            # Check that the deck, landmarks, hirelings and map are registered for the tournament
            tournament_maps = tournament.maps.all()
            tournament_decks = tournament.decks.all()
            if not tournament.asset_mode == AssetModeChoices.OPEN:
                if landmarks:
                    tournament_landmarks = tournament.landmarks.all()
                    for landmark in landmarks:
                        if landmark not in tournament_landmarks:
                            validation_errors_to_display.append(f'{landmark} Landmark is not playable in {tournament}')
                if tweaks:
                    tournament_tweaks = tournament.tweaks.all()
                    for tweak in tweaks:
                        if tweak not in tournament_tweaks:
                            validation_errors_to_display.append(f'{tweak} Tweak is not playable in {tournament}')
                if hirelings:
                    tournament_hirelings = tournament.hirelings.all()
                    for hireling in hirelings:
                        if hireling not in tournament_hirelings:
                            validation_errors_to_display.append(f'{hireling} Hireling is not playable in {tournament}')
                if map and map not in tournament_maps:
                    validation_errors_to_display.append(f'{map} Map is not playable in {tournament}')
                if deck and deck not in tournament_decks:
                    validation_errors_to_display.append(f'{deck} Deck is not playable in {tournament}')
   
        # Check that map and deck are selected
        official_only = True
        if map and deck:
            if not map.official or not deck.official:
                official_only = False
            elif tweaks:
                for tweak in tweaks:
                    if not tweak.official:
                        official_only = False
                        break
            elif landmarks:
                for landmark in landmarks:
                    if not landmark.official:
                        official_only = False
                        break
            elif hirelings:
                for hireling in hirelings:
                    if not hireling.official:
                        official_only = False
                        break
        else:
            if map:
                validation_errors_to_display.append('Select a Deck')
            elif deck:
                validation_errors_to_display.append('Select a Map')
            else:
                validation_errors_to_display.append('Select a Map and a Deck')

        if self.effort_formset.is_valid():

            faction_roster = set()
            vagabond_roster = set()
            player_roster = set()
            coalition_roster = set()
            coalition_receiver = set()
            test_match = False
            vagabond_count = 1
            win_count = 0
            clockwork_count = 0
            human_count = 0
            coalition_count = 0
            reach_value = 0

            for effort_form in self.effort_formset.forms:
                faction = effort_form.cleaned_data.get('faction')
                vagabond = effort_form.cleaned_data.get('vagabond')
                win = effort_form.cleaned_data.get('win')
                coalition = effort_form.cleaned_data.get('coalition_with')
                player = effort_form.cleaned_data.get('player')
                
                if coalition:
                    coalition_roster.add(faction)
                    coalition_receiver.add(coalition)
                    coalition_count += 1

                if win and not coalition:
                    win_count += 1

                if player:
                    if player in player_roster:
                        test_match = True
                    else:
                        player_roster.add(player)

                if faction:
                    reach_value = reach_value + faction.reach
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
                        if not faction.official:
                            official_only = False
                        faction_roster.add(faction)
                if vagabond:
                    if vagabond in vagabond_roster:
                        # Error for duplicate vagabond
                        validation_errors_to_display.append(f'{vagabond} {faction} selected twice') 
                    else:
                        if not vagabond.official:
                            official_only = False
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
            if test_match or human_count == 0:
                cleaned_data['test_match'] = True
            else:
                cleaned_data['test_match'] = False

            if official_only:
                cleaned_data['official'] = True
            else:
                cleaned_data['official'] = False

            cleaned_data['reach_value'] = reach_value
            
            if len(faction_roster) + max(0, vagabond_count-1) < 2:
                validation_errors_to_display.append(f'Select at least two factions') 
                progress_errors_to_display.append(f'Select at least two factions') 

            if len(coalition_receiver) != coalition_count:
                        validation_errors_to_display.append('One faction cannot have two Coalitions')

            # Validate Tournament Game Settings
            if round:
                if win_count > 1 and not tournament.teams:
                    validation_errors_to_display.append(f'Only one winner is allowed')
                if coalition_count:
                    if tournament.coalition_type == 'None':
                        validation_errors_to_display.append(f'Coalitions are not allowed in {tournament}')
                    elif tournament.coalition_type == 'One' and coalition_count > 1:
                        if coalition_roster & coalition_receiver:
                            validation_errors_to_display.append(f'Double Coalitions are not allowed in {tournament}')

                # Error if platform does not match tournament platform
                if tournament.platform and platform != tournament.platform:
                    validation_errors_to_display.append(f"Select {tournament.platform} for this {tournament} Game")  # Store the message
                # Error if link not supplied for
                if tournament.link_required and not link:
                    validation_errors_to_display.append("Provide a unique link to this game's thread")  # Store the message


                player_roster = set()  # Set to track unique players
                current_players = round.current_player_queryset()
                tournament_factions = tournament.factions.all()
                tournament_vagabonds = tournament.vagabonds.all()

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
                if len(player_roster) > tournament.max_players:
                    validation_errors_to_display.append(f'Over {tournament} maximum player count')
                if len(player_roster) < tournament.min_players:
                    validation_errors_to_display.append(f'Under {tournament} minimum player count')

                # Check each player in the player_roster
                if not tournament.open_roster:
                    for player in player_roster:
                        if player not in current_players:
                            validation_errors_to_display.append(f'{player} is not registered for {tournament}')
                if not tournament.asset_mode == AssetModeChoices.OPEN:
                    for faction in faction_roster:
                        if faction not in tournament_factions:
                            validation_errors_to_display.append(f'The Faction {faction} is not playable in {tournament}')
                    for vagabond in vagabond_roster:
                        if vagabond not in tournament_vagabonds:
                            validation_errors_to_display.append(f'The Vagabond {vagabond} is not playable in {tournament}')

        if not final:
            if not deck:
                progress_errors_to_display.append(f"Select a deck") 
            if not map:
                progress_errors_to_display.append(f"Select a map") 
            if progress_errors_to_display:
                raise ValidationError(progress_errors_to_display)
            return cleaned_data


        if validation_errors_to_display:
            raise ValidationError(validation_errors_to_display)
        return cleaned_data


class GameCreateFormV2(GameCreateForm):
    """Extended game form supporting match mode.
    In match mode: round is locked, platform may be locked, match_id is tracked."""
    match_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, match=None, **kwargs):
        self.match = match

        if match:
            # Force the round from the match
            kwargs['round'] = match.round

        super().__init__(*args, **kwargs)

        if match:
            self.fields['match_id'].initial = match.id
            # Lock the round field
            self.fields['round'].disabled = True
            self.fields['round'].queryset = Round.objects.filter(pk=match.round.pk)
            self.fields['round'].empty_label = None

            tournament = match.round.get_tournament()
            # Lock platform if tournament specifies one
            if tournament.platform:
                self.fields['platform'].choices = [(tournament.platform, tournament.platform)]
                self.fields['platform'].initial = tournament.platform
                self.fields['platform'].widget.attrs['disabled'] = True

    def clean(self):
        cleaned_data = super().clean()
        # For match mode, ensure round is set even though field is disabled
        if self.match and 'round' not in cleaned_data:
            cleaned_data['round'] = self.match.round

        # Validate all match seat players have an effort
        if self.match and self.effort_formset.is_valid():
            seat_profiles = set(
                MatchSeat.objects.filter(series=self.match.series)
                .values_list('stage_participant__tournament_player__profile', flat=True)
            )
            submitted_players = set()
            for effort_form in self.effort_formset.forms:
                player = effort_form.cleaned_data.get('player')
                if player and not effort_form.cleaned_data.get('delete', False):
                    submitted_players.add(player.pk)
            missing = seat_profiles - submitted_players
            if missing:
                missing_names = list(Profile.objects.filter(pk__in=missing).values_list('discord', flat=True))
                raise ValidationError(
                    [f'{name} is required in this match' for name in missing_names]
                )

        return cleaned_data


class EffortCreateForm(forms.ModelForm):
    required_css_class = 'required-field'
    score = forms.IntegerField(
        widget=forms.NumberInput(attrs={'type': 'number', 'inputmode': 'numeric', 'min': 0, 'step': 1}),
        required=False,
        initial=0 
    )
    delete = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'delete-form-checkbox'}))
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
        score = cleaned_data.get('score', None)
        vagabond = cleaned_data.get('vagabond')
        captains = cleaned_data.get('captains')
        coalition = cleaned_data.get('coalition_with')
        if score is None or score == "":
            cleaned_data['score'] = 0 
        
        # If the delete checkbox is checked, clear the cleaned data to skip further validation
        if cleaned_data.get('delete', False):
            # Clear out cleaned_data, essentially skipping validation for this form
            cleaned_data = {}
            cleaned_data['delete'] = True
            # print(cleaned_data)
            return cleaned_data
        
        if not faction and not player and not score:
            cleaned_data = {}
            cleaned_data['delete'] = True
            # print(cleaned_data)
            return cleaned_data

        # print(cleaned_data.get('score'))
        if faction is None or faction == "":
            # raise ValidationError(f"Faction required")
            validation_errors_to_display.append("Faction required")
        elif faction.type == "C" and player:
            validation_errors_to_display.append('This is a Clockwork faction and cannot have a "player"')
        # elif faction.type != "C" and not player:
        #     validation_errors_to_display.append('Please select a player. If the player is missing add them using the "Register Player" form in the top right.')
        
        elif faction.title == 'Vagabond' and not vagabond:
            # raise ValidationError('Select a Vagabond.')
            validation_errors_to_display.append('Select a Vagabond')
        
        # Clean VB specific options
        if faction and faction.title != 'Vagabond':
            cleaned_data['vagabond'] = None
            if faction.title != 'Chameleander':
                cleaned_data['coalition_with'] = None

        # If captains are assigned ensure no more than 3 captains are assigned
        # if faction.title == "Knaves of the Deepwood" and not captains:
        #     validation_errors_to_display.append('Please select a Captain')
        # Captains cannot be recorded
        cleaned_data['captains'] = None

        # print(score)
        if not dominance and score is None and not coalition:
            # raise ValidationError(f"Score or Dominance required")
            validation_errors_to_display.append('Score or Dominance required')

        # print(cleaned_data)
            
        if validation_errors_to_display:
            raise ValidationError(validation_errors_to_display)
        return cleaned_data

class TurnScoreCreateForm(forms.ModelForm):
    delete = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'delete-form-checkbox'}))

    class Meta:
        model = TurnScore
        fields = ['id', 'turn_number', 
                  'faction_points', 'crafting_points', 'battle_points', 'other_points', 'dominance', 
                  'total_points', 'generic_points']
    faction_points = forms.IntegerField(
        required=False, 
        widget=forms.NumberInput(attrs={
            'type': 'number',
            'step': 1,
            'style': 'text-align: right; max-width: 50px;',
            'placeholder': '0' 
        })
        )
    crafting_points = forms.IntegerField(
        required=False, 
        widget=forms.NumberInput(attrs={
            'type': 'number',
            'step': 1,
            'style': 'text-align: right; max-width: 50px;',
            'placeholder': '0' 
        })
        )
    battle_points = forms.IntegerField(
        required=False, 
        widget=forms.NumberInput(attrs={
            'type': 'number',
            'step': 1,
            'style': 'text-align: right; max-width: 50px;',
            'placeholder': '0' 
        })
        )
    other_points = forms.IntegerField(
        required=False, 
        widget=forms.NumberInput(attrs={
            'type': 'number',
            'step': 1,
            'style': 'text-align: right; max-width: 50px;',
            'placeholder': '0' 
        })
    )
    generic_points = forms.IntegerField(
        required=False, 
        widget=forms.NumberInput(attrs={
            'type': 'number',
            'step': 1,
            'style': 'text-align: right; max-width: 50px;',
            'placeholder': '0' 
        })
    )


    # Custom validation for turn_number field
    def clean_turn_number(self):
        turn_number = self.cleaned_data.get('turn_number')
        if turn_number < 1:
            raise forms.ValidationError("Turn number must be greater than or equal to 1.")
        return turn_number

    # Custom validation for the total_points (can be a derived field if needed)
    # def clean_total_points(self):
    #     faction_points = self.cleaned_data.get('faction_points', 0)
    #     crafting_points = self.cleaned_data.get('crafting_points', 0)
    #     battle_points = self.cleaned_data.get('battle_points', 0)
    #     other_points = self.cleaned_data.get('other_points', 0)
    #     generic_points = self.cleaned_data.get('generic_points', 0)

    #     # Ensure the total points are consistent
    #     total_points = faction_points + crafting_points + battle_points + other_points + generic_points
    #     if total_points != self.cleaned_data.get('total_points', 0):
    #         raise forms.ValidationError("The total points do not match the sum of faction, crafting, battle, and other points.")

    #     return total_points
    
    def clean(self):
        cleaned_data = super().clean()

        # Set any missing points to 0
        for field in ['faction_points', 'crafting_points', 'battle_points', 'other_points', 'generic_points']:
            if field in cleaned_data and not cleaned_data.get(field):
                cleaned_data[field] = 0

        return cleaned_data
    
class ScoreCardCreateForm(forms.ModelForm):
    class Meta:
        model = ScoreCard
        fields = ['faction', 'description', 'game_group', 'dominance',
                  'total_points', 'total_other_points', 'total_generic_points',
                  'total_faction_points', 'total_crafting_points', 'total_battle_points']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'cols': 20}),
            'game_group': forms.TextInput(attrs={'placeholder': 'To distinguish games'}),
        }
    def __init__(self, *args, user=None, faction=None, **kwargs):
        # Call the parent constructor
        super(ScoreCardCreateForm, self).__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'rows': '2',
            'placeholder': 'Faction Notes...',
            'class': 'form-control full-width', 
        })
        # Check if faction is passed to the form
        view_status = user.profile.view_status
        if faction:
            self.fields['faction'].queryset = Faction.objects.filter(id=faction)
            self.fields['faction'].initial = faction  # Set the initial value of the faction
            self.fields['faction'].empty_label = None
            # self.fields['faction'].disabled = True
        elif not user.profile.weird:
            self.fields['faction'].queryset = Faction.objects.filter(official=True, status__lte=view_status)
        else:
            self.fields['faction'].queryset = Faction.objects.filter(status__lte=view_status)

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
        fields = ['deck', 'round', 'official', 'map', 'random_clearing', 'type', 'platform', 'undrafted_faction', 'undrafted_vagabond', 'landmarks', 'hirelings', 'tweaks','link', 'date_posted', 'final', 'recorder', 'notes']



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
        # print(f'Selected Scorecard: {selected_scorecard}')
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

class AssignEffortForm(forms.ModelForm):  
    effort = forms.ModelChoiceField(queryset=Effort.objects.all(), required=True)

    class Meta:
        model = ScoreCard
        fields = ['effort']
        widgets = {
            'effort': forms.ModelChoiceField(queryset=ScoreCard.objects.all(), required=True),
        }

    def __init__(self, *args, selected_efforts, user=None, **kwargs):
        # Call the parent constructor
        super(AssignEffortForm, self).__init__(*args, **kwargs)
        # print(f'Selected Scorecard: {selected_scorecard}')
        self.fields['effort'].queryset = selected_efforts       


# Dynamic forms for new tournament create/update views with permission-based field access
class TournamentDynamicCreateForm(forms.ModelForm):
    PLATFORM_CHOICES = [
        (None, 'Any platform'),
        ('Tabletop Simulator', 'Tabletop Simulator'),
        ('Root Digital', 'Root Digital'),
        ('In Person', 'In Person'),
    ]
    COALITION_CHOICES = [
        ('None', 'No Coalitions'),
        ('One', 'Single Coalitions Only'),
        ('All', 'Double Coalitions Allowed'),
    ]
    platform = forms.ChoiceField(
        choices=PLATFORM_CHOICES,
        initial=None,
        required=False,
        label='Required Platform'
    )
    coalition_type = forms.ChoiceField(
        choices=COALITION_CHOICES,
        initial='One',
        required=False,
        label='Allowed Coalitions'
    )
    picture = forms.ImageField(required=False)
    use_stages = forms.BooleanField(
        required=False,
        initial=False,
        label='Use Stages',
    )
    stage_name = forms.CharField(
        max_length=100,
        initial='Stage 1',
        required=False,
        label='First Stage Name',
    )
    use_rounds = forms.BooleanField(
        required=False,
        initial=False,
        label='Use Rounds',
    )
    round_name = forms.CharField(
        max_length=100,
        initial='Round 1',
        required=False,
        label='First Round Name',
    )

    class Meta:
        model = Tournament
        fields = [
            'name', 'classification', 'designer', 'guild', 'description', 'rules',
            'start_date', 'end_date', 'publicly_visible', 'is_active',
            'max_players', 'min_players', 'enforce_player_count', 'open_roster',
            'platform', 'default_format', 'link_required',
            'asset_mode', 'include_clockwork',
            'leaderboard_positions', 'game_threshold', 'coalition_type', 'teams',
            'picture'
        ]
        labels = {
            'name': 'Series Name',
            'classification': 'Classification',
            'designer': 'Owner',
            'guild': 'Discord Guild',
            'start_date': 'Start Date',
            'end_date': 'End Date',
            'publicly_visible': 'Display on Series home page',
            'is_active': 'Active Status',
            'leaderboard_positions': 'Leaderboard Positions',
            'game_threshold': 'Leaderboard Game Threshold',
            'link_required': 'Require Link with Game Submission',
            'teams': 'Allow for multiple non-Coalition Wins (Teams)',
            'description': 'Description (Optional)',
            'rules': 'Rules (Optional)',
            'open_roster': 'Allow Unregistered Players to join games hosted by a Registered Player',
            'asset_mode': 'Asset Mode',
            'include_clockwork': 'Include Clockwork Factions',
            'default_format': 'Default Round Format',
            'picture': 'Series Image',
        }

    def __init__(self, *args, user=None, **kwargs):
        super(TournamentDynamicCreateForm, self).__init__(*args, **kwargs)

        # Set initial values for new tournaments
        if not self.instance.pk:
            if user:
                self.fields['designer'].initial = user.profile.id
                self.fields['classification'].initial = Tournament.ClassificationTypes.GROUP

        # Add CSS classes for date fields
        self.fields['start_date'].widget.attrs.update({'class': 'datepicker'})
        self.fields['end_date'].widget.attrs.update({'class': 'datepicker'})

        # Add placeholder for description
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief description of the series.',
            'rows': '2'
        })

        # Add placeholder for rules
        self.fields['rules'].widget.attrs.update({
            'placeholder': 'Define the rules for your series. Players will need to agree to these rules when registering via a registration survey.',
            'rows': '6'
        })

        # Filter guild choices to user's guilds
        if user:
            import json
            from the_gatehouse.models import DiscordGuild
            with open('/etc/config.json') as config_file:
                config = json.load(config_file)
            if user.profile.admin:
                self.fields['guild'].queryset = DiscordGuild.objects.all().exclude(guild_id=config['WW_GUILD_ID'])
            else:
                self.fields['guild'].queryset = user.profile.guilds.all().exclude(guild_id=config['WW_GUILD_ID'])

        # Remove admin-only fields for non-admins
        if user and not user.profile.admin:
            self.fields.pop('designer', None)

    def clean(self):
        cleaned_data = super().clean()
        use_stages = cleaned_data.get('use_stages')
        use_rounds = cleaned_data.get('use_rounds')
        stage_name = cleaned_data.get('stage_name')
        round_name = cleaned_data.get('round_name')
        if use_stages and not stage_name:
            self.add_error('stage_name', 'Stage name is required when Use Stages is enabled.')
        if use_stages and use_rounds and not round_name:
            self.add_error('round_name', 'Round name is required when Use Rounds is enabled.')
        return cleaned_data

    def save(self, commit=True):
        """Save tournament and set default assets based on platform"""
        instance = super().save(commit=False)

        if commit:
            # Check if this is a new tournament to set the default assets
            set_default_assets = False
            if not instance.pk:
                set_default_assets = True
            else:
                # Existing tournament - check if any related assets are missing
                if (
                    not instance.factions.exists() or
                    not instance.maps.exists() or
                    not instance.decks.exists() or
                    not instance.vagabonds.exists()
                ):
                    set_default_assets = True

            instance.save()

            # Set default assets based on platform
            if set_default_assets:
                if instance.platform == "Root Digital":
                    instance.factions.set(Faction.objects.filter(in_root_digital=True).exclude(type="C"))
                    instance.maps.set(Map.objects.filter(in_root_digital=True))
                    instance.decks.set(Deck.objects.filter(in_root_digital=True))
                    instance.vagabonds.set(Vagabond.objects.filter(in_root_digital=True))
                else:
                    instance.factions.set(Faction.objects.filter(official=True, status='1').exclude(type="C"))
                    instance.maps.set(Map.objects.filter(official=True, status='1'))
                    instance.decks.set(Deck.objects.filter(official=True, status='1'))
                    instance.vagabonds.set(Vagabond.objects.filter(official=True, status='1'))

        return instance


class TournamentDynamicUpdateForm(forms.ModelForm):
    PLATFORM_CHOICES = [
        (None, 'Any platform'),
        ('Tabletop Simulator', 'Tabletop Simulator'),
        ('Root Digital', 'Root Digital'),
        ('In Person', 'In Person'),
    ]
    COALITION_CHOICES = [
        ('None', 'No Coalitions'),
        ('One', 'Single Coalitions Only'),
        ('All', 'Double Coalitions Allowed'),
    ]
    platform = forms.ChoiceField(
        choices=PLATFORM_CHOICES,
        initial=None,
        required=False,
        label='Required Platform'
    )
    coalition_type = forms.ChoiceField(
        choices=COALITION_CHOICES,
        initial='One',
        required=False,
        label='Allowed Coalitions'
    )
    picture = forms.ImageField(required=False)

    class Meta:
        model = Tournament
        fields = [
            'name', 'classification', 'designer', 'guild', 'description', 'rules',
            'start_date', 'end_date', 'publicly_visible', 'is_active',
            'max_players', 'min_players', 'enforce_player_count', 'open_roster',
            'platform', 'link_required',
            'asset_mode', 'include_clockwork',
            'leaderboard_positions', 'game_threshold', 'coalition_type', 'teams',
            'default_format',
            'use_stages', 'use_rounds',
            'picture'
        ]
        labels = {
            'name': 'Series Name',
            'classification': 'Classification',
            'designer': 'Owner',
            'guild': 'Discord Guild',
            'description': 'Description (Optional)',
            'rules': 'Rules (Optional)',
            'start_date': 'Start Date',
            'end_date': 'End Date',
            'publicly_visible': 'Display on Series home page',
            'is_active': 'Active Status',
            'leaderboard_positions': 'Leaderboard Positions',
            'game_threshold': 'Leaderboard Game Threshold',
            'coalition_type': 'Allowed Coalitions',
            'platform': 'Required Platform',
            'link_required': 'Require Link with Game Submission',
            'teams': 'Allow for multiple non-Coalition Wins (Teams)',
            'open_roster': 'Allow Unregistered Players to join games',
            'asset_mode': 'Asset Mode',
            'include_clockwork': 'Include Clockwork Factions',
            'default_format': 'Default Round Format',
            'use_stages': 'Use Stages',
            'use_rounds': 'Use Rounds',
            'picture': 'Series Image',
        }
        help_texts = {
            'use_stages': 'Enable if there are multiple stages (e.g. Swiss then Top 8 or 2026 then 2027).',
            'use_rounds': 'Enable if stages in this tournament have multiple rounds.',
        }

    def __init__(self, *args, user=None, **kwargs):
        super(TournamentDynamicUpdateForm, self).__init__(*args, **kwargs)

        # Add CSS classes for date fields
        self.fields['start_date'].widget.attrs.update({'class': 'datepicker'})
        self.fields['end_date'].widget.attrs.update({'class': 'datepicker'})

        # Add placeholder for description
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Brief description of the series.',
            'rows': '2',
        })

        # Add placeholder for rules
        self.fields['rules'].widget.attrs.update({
            'placeholder': 'Define the rules for your series. Players will need to agree to these rules when registering via a registration survey.',
            'rows': '6'
        })

        # Filter guild choices to user's guilds
        if user:
            import json
            from the_gatehouse.models import DiscordGuild
            with open('/etc/config.json') as config_file:
                config = json.load(config_file)
            if user.profile.admin:
                self.fields['guild'].queryset = DiscordGuild.objects.all().exclude(guild_id=config['WW_GUILD_ID'])
            else:
                self.fields['guild'].queryset = user.profile.guilds.all().exclude(guild_id=config['WW_GUILD_ID'])

        # Remove admin-only fields for non-admins
        if user and not user.profile.admin:
            self.fields.pop('classification', None)
            self.fields.pop('designer', None)


class RoundCreateForm(forms.ModelForm):
    class Meta:
        model = Round
        fields = ['name', 'description', 'round_number', 'start_date', 'end_date', 'is_active', 'game_threshold', 'leaderboard_positions', 'round_specific_format', 'min_players', 'max_players']
        labels = {
            'name': 'Round Name',
            'round_number': 'Round #',
            'end_date': 'End Date',
            'is_active': 'Active Status',
            'game_threshold': 'Leaderboard Threshold',
            'leaderboard_positions': 'Leaderboard Positions',
            'description': 'Description (Optional)',
            'round_specific_format': 'Format',
            'min_players': 'Min Players per Group',
            'max_players': 'Max Players per Group',
        }

    def __init__(self, *args, stage=None, current_round=None, **kwargs):
        """
        Initialize the form, optionally accepting a tournament argument,
        and set the tournament field when saving.
        """
        # Store the tournament and stage instances
        self.stage = stage
        super().__init__(*args, **kwargs)
        self.fields['description'].widget.attrs.update({
            'placeholder': 'Give a brief description of the round.',
            'rows': '2'
            })
        self.fields['start_date'].widget.attrs.update({'class': 'datepicker'}) 
        self.fields['end_date'].widget.attrs.update({'class': 'datepicker'}) 

        # Ensure current_round is passed and not None
        if current_round is not None:
            # Set an initial value for round_number to current_round
            self.fields['round_number'].initial = current_round

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance
    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')

        if name and self.stage:
            qs = Round.objects.filter(stage=self.stage, name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"A round with this name already exists in this stage")
        return cleaned_data


class StageCreateForm(forms.ModelForm):
    use_rounds = forms.BooleanField(
        required=False,
        label='Use Rounds',
        help_text='Enable if stages in this tournament have multiple rounds.',
    )
    round_name = forms.CharField(
        max_length=255,
        initial='Round 1',
        required=False,
        label='First Round Name',
    )

    class Meta:
        model = Stage
        fields = [
            'name', 'order', 'stage_format', 'advancement_type',
            'start_date', 'end_date', 'is_active',
            'min_players', 'max_players',
            'game_threshold', 'leaderboard_positions',
        ]
        labels = {
            'stage_format': 'Format',
            'advancement_type': 'Advancement Type',
            'end_date': 'End Date',
            'is_active': 'Active Status',
            'game_threshold': 'Leaderboard Threshold',
            'leaderboard_positions': 'Leaderboard Positions',
        }

    def __init__(self, *args, tournament=None, **kwargs):
        self.tournament = tournament
        super().__init__(*args, **kwargs)
        if tournament and not self.instance.pk:
            self.instance.tournament = tournament
        self.fields['start_date'].widget.attrs.update({'class': 'datepicker'})
        self.fields['end_date'].widget.attrs.update({'class': 'datepicker'})
        if not self.instance.pk:
            # Auto-populate order to next available number
            if tournament:
                max_order = tournament.stages.aggregate(Max('order'))['order__max'] or 0
                self.fields['order'].initial = max_order + 1
        # Pre-populate use_rounds from the tournament's current setting
        if tournament:
            self.fields['use_rounds'].initial = tournament.use_rounds
            # Hide format/advancement fields for non-Tournament classifications
            if tournament.classification != Tournament.ClassificationTypes.TOURNAMENT:
                del self.fields['stage_format']
                del self.fields['advancement_type']
        # Only show round_name on create, not update
        if self.instance.pk:
            del self.fields['round_name']

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        if name and self.tournament:
            qs = Stage.objects.filter(tournament=self.tournament, name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"A stage with this name already exists in {self.tournament.name}")
        # Validate round_name on create when use_rounds is enabled
        if not self.instance.pk:
            use_rounds = cleaned_data.get('use_rounds')
            round_name = cleaned_data.get('round_name')
            if use_rounds and not round_name:
                self.add_error('round_name', 'Round name is required when Use Rounds is enabled.')
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

    # Multiple select field for Tweaks not yet in the tournament
    available_tweaks = forms.ModelMultipleChoiceField(
        queryset=Tweak.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Add Tweaks'
    )

    # Multiple select field for Tweaks already in the tournament
    tournament_tweaks = forms.ModelMultipleChoiceField(
        queryset=Tweak.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': '10'}),
        required=False,
        label='Remove Tweaks'
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
        available_tweaks_query = kwargs.pop('available_tweaks_query', None)
        tournament_tweaks_query = kwargs.pop('tournament_tweaks_query', None)
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
        self.fields['available_tweaks'].queryset = available_tweaks_query
        self.fields['tournament_tweaks'].queryset = tournament_tweaks_query
        self.fields['available_hirelings'].queryset = available_hirelings_query
        self.fields['tournament_hirelings'].queryset = tournament_hirelings_query
        self.fields['available_vagabonds'].queryset = available_vagabonds_query
        self.fields['tournament_vagabonds'].queryset = tournament_vagabonds_query
        
    def clean(self):
        cleaned_data = super().clean()
        # print(cleaned_data)
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

            for tweak in self.cleaned_data['available_tweaks']:
                self.tournament.tweaks.add(tweak)

            for tweak in self.cleaned_data['tournament_tweaks']:
                self.tournament.tweaks.remove(tweak)

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
            from the_warroom.models import TournamentPlayer

            tournament = self.round.get_tournament()

            # Add selected players to the tournament roster
            for player in self.cleaned_data['available_players']:
                TournamentPlayer.objects.get_or_create(
                    tournament=tournament,
                    profile=player,
                    defaults={'status': TournamentPlayer.StatusChoices.REGISTERED}
                )

            # Remove selected players from the tournament roster
            for player in self.cleaned_data['current_players']:
                TournamentPlayer.objects.filter(tournament=tournament, profile=player).delete()



class TournamentPlayerSettingsForm(forms.ModelForm):
    """Form for player-related tournament settings only."""
    class Meta:
        model = Tournament
        fields = ['open_roster', 'enforce_player_count', 'min_players', 'max_players']
        labels = {
            'enforce_player_count': 'Restrict Player Count',
        }


class TournamentAssetSettingsForm(forms.ModelForm):
    """Form for asset-related tournament settings only."""
    class Meta:
        model = Tournament
        fields = ['asset_mode', 'include_clockwork']
        labels = {
            'asset_mode': 'Asset Mode',
            'include_clockwork': 'Include Clockwork Factions',
        }
