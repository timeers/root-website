from dateutil import parser, relativedelta
from datetime import timedelta, datetime

from django.utils import timezone

from the_keep.models import Faction, Vagabond, Deck, Map, Landmark, Hireling, Profile, StatusChoices

from the_warroom.models import Game, Effort, PlatformChoices, Tournament, Round
from the_warroom.utils import clean_nickname

from the_gatehouse.services.discordservice import send_discord_message



# Mapping from API values to Faction Names
FACTION_MAP = {
    # Base
    'cats': 'Marquise de Cat',
    'birds': 'Eyrie Dynasties',
    'alliance': 'Woodland Alliance',
    # Riverfolk
    'lizards': 'Lizard Cult',
    'otters': 'Riverfolk Company',
    # Underground
    'crows': 'Corvid Conspiracy',
    'moles': 'Underground Duchy',
    # Marauders
    'badgers': 'Keepers in Iron',
    'rats': 'Lord of the Hundreds',
    # Homelands
    'frogs': 'Lilypad Diaspora',
    'bats': 'Twilight Council',
    'skunks': 'Knaves of the Deepwood',

    # Vagabonds
    'vb_ranger': 'Vagabond',
    'vb_thief': 'Vagabond',
    'vb_tinker': 'Vagabond',
    'vb_vagrant': 'Vagabond',
    'vb_arbiter': 'Vagabond',
    'vb_scoundrel': 'Vagabond',
    'vb_adventurer': 'Vagabond',
    'vb_ronin': 'Vagabond',
    'vb_harrier': 'Vagabond',
    'vb_jailor': 'Vagabond',
    'vb_cheat': 'Vagabond',
    'vb_gladiator': 'Vagabond',
}

VAGABOND_MAP = {
    'vb_ranger': 'Ranger',
    'vb_thief': 'Thief',
    'vb_tinker': 'Tinker',
    'vb_vagrant': 'Vagrant',
    'vb_arbiter': 'Arbiter',
    'vb_scoundrel': 'Scoundrel',
    'vb_adventurer': 'Adventurer',
    'vb_ronin': 'Ronin',
    'vb_harrier': 'Harrier',
    'vb_jailor': 'Jailor',
    'vb_cheat': 'Cheat',
    'vb_gladiator': 'Gladiator',
}

DECK_MAP = {
    'e&p': 'Exiles & Partisans',
    'standard': "Base",
    's&d': 'Squires & Disciples',
}

MAP_MAP = {
    'autumn': 'Autumn',
    'winter': 'Winter',
    'mountain': 'Mountain',
    'lake': 'Lake',

    'gorge': 'Gorge',
    'marsh': 'Marsh',
}

LANDMARK_MAP = {
    'lm_lc': 'Lost City',
    'lm_bm': 'Black Market',
    'lm_et': 'Elder Treetop',
    'lm_ferry': 'The Ferry',
    'lm_tower': 'The Tower',
    'lm_lf': 'Legendary Forge',
}

HIRELING_MAP = {
    # Promoted
    # Base
    'h_cats_p': 'Forest Patrol',
    'h_birds_p': 'Last Dynasty',
    'h_alliance_p': 'Spring Uprising',
    'h_vb_p': 'The Exile',
    # Riverfolk
    'h_lizards_p': 'Warm Sun Prophets',
    'h_otters_p': 'Riverfolk Flotilla',
    # Underground
    'h_crows_p': 'Corvid Spies',
    'h_moles_p': 'Sunward Expedition',
    # Marauders
    'h_badgers_p': 'Vault Keepers',
    'h_rats_p': 'Flame Bearers',
    # Homelands
    'h_frogs_p': 'River Roamers',
    'h_bats_p': 'Sunny Advocates',

    # Demoted
    # Base
    'h_cats_d': 'Feline Physicians',
    'h_birds_d': 'Bluebird Nobles',
    'h_alliance_d': 'Rabbit Scouts',
    'h_vb_d': 'The Brigand',
    # Riverfolk
    'h_lizards_d': 'Lizard Envoys',
    'h_otters_d': 'Otter Divers',
    # Underground
    'h_crows_d': 'Raven Sentries',
    'h_moles_d': 'Mole Artisans',
    # Marauders
    'h_badgers_d': 'Badger Bodyguards',
    'h_rats_d': 'Rat Smugglers',
    # Homelands
    'h_frogs_d': 'Frog Tinkers',
    'h_bats_d': 'Bat Messengers',

    # Misc Hirelings
    'h_band_p': 'Popular Band',
    'h_band_d': 'Street Band',
    'h_bandits_p': 'Highway Bandits',
    'h_bandits_d': 'Bandit Gangs',
    'h_protector_p': 'Furious Protector',
    'h_protector_d': 'Stoic Protector',
    'h_farmers_p': 'Prosperous Farmers',
    'h_farmers_d': 'Struggling Farmers',
    'h_farmer_p': 'Prosperous Farmers',
    'h_farmer_d': 'Struggling Farmers',
}

def create_game_from_api(match_data):
    """Create a Game object from API match data."""

    date_registered = parser.parse(match_data.get('date_registered', match_data.get('date_closed')))

    # Get or create Tournament
    tournament, created = Tournament.objects.get_or_create(
        name='Root Digital League'
    )
    
    # Get or create Round based on API tournament name (e.g., "M04", "A08")
    round_name = match_data.get('tournament', '')
    game_round = None
    if round_name:
        game_round = get_game_round(date_registered=date_registered, round_name=round_name, tournament=tournament)

    # Get deck
    deck_name = DECK_MAP.get(match_data.get('deck'))
    deck = None
    if deck_name:
        deck = Deck.objects.filter(title=deck_name).first()
    
    # Get map
    map_name = MAP_MAP.get(match_data.get('board_map'))
    game_map = None
    if map_name:
        game_map = Map.objects.filter(title=map_name).first()
    
    # Get undrafted faction
    undrafted_faction = None
    undrafted_vagabond = None
    undrafted_faction_key = match_data.get('undrafted_faction')
    if undrafted_faction_key:
        faction_name = FACTION_MAP.get(undrafted_faction_key)
        if faction_name:
            undrafted_faction = Faction.objects.filter(title=faction_name).first()
    if undrafted_faction_key in VAGABOND_MAP:
        undrafted_vagabond_name = VAGABOND_MAP[undrafted_faction_key]
        undrafted_vagabond = Vagabond.objects.filter(title=undrafted_vagabond_name).first()

    # Determine game type
    game_type = Game.TypeChoices.ASYNC if match_data.get('turn_timing') == 'async' else Game.TypeChoices.LIVE
    
    # Get platform
    platform = PlatformChoices.DWD

    status = StatusChoices.STABLE
    
    random_clearing = bool(match_data.get('random_suits'))

    nickname = clean_nickname(match_data.get('title', ''))

    notes = f"Imported from rootleague.pliskin.dev on {timezone.now().strftime('%m/%d/%y')}"

    # Create the game
    game = Game.objects.create(
        type=game_type,
        platform=platform,
        deck=deck,
        map=game_map,
        round=game_round,
        undrafted_faction=undrafted_faction,
        undrafted_vagabond=undrafted_vagabond,
        link=match_data.get('table_talk_url', ''),
        nickname=nickname,
        random_clearing=random_clearing,
        league_id=str(match_data['id']),
        date_posted=date_registered,
        official=True,
        final=True,
        status=status,
        notes=notes,
    )
    
    hireling_keys = match_data.get('hirelings', []) or []
    landmark_keys = match_data.get('landmarks', []) or []

    # Add hirelings
    hirelings_to_add = []
    for key in hireling_keys:
        title = HIRELING_MAP.get(key) if key else None
        if title:
            hireling = Hireling.objects.filter(title__iexact=title).first()
            if hireling:
                hirelings_to_add.append(hireling)

    if hirelings_to_add:
        game.hirelings.add(*hirelings_to_add)

    # Add landmarks
    landmarks_to_add = []
    for key in landmark_keys:
        if key:
            title = LANDMARK_MAP.get(key)
            if title:
                landmark = Landmark.objects.filter(title__iexact=title).first()
                if landmark:
                    landmarks_to_add.append(landmark)
                    
    if landmarks_to_add:
        game.landmarks.add(*landmarks_to_add)

    return game

def get_game_round(date_registered: datetime, round_name: str, tournament: Tournament):
     # Add 1 day to avoid edge-case month rollover issues if the first game is showing on the previous month
    adjusted_date = date_registered + timedelta(days=1)
    # Start day of League
    first_of_month = adjusted_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Add 3 months
    three_months_later = first_of_month + relativedelta.relativedelta(months=3)
    # Subtract one day to get the last day of the second month
    end_of_target_month = three_months_later - timedelta(days=1)

    game_round, created = Round.objects.get_or_create(
        name=round_name,
        tournament=tournament,
        defaults={
            'round_number': tournament.rounds.count() + 1,
            'start_date': first_of_month,
            'end_date': end_of_target_month,
            'game_threshold': 25,
        }
    )
    return game_round

def update_game_from_api(game, match_data):
    """Update an existing Game object with new data from API."""
    
    date_registered = parser.parse(match_data.get('date_registered', match_data.get('date_closed')))
    
    # Get or create Tournament
    tournament, created = Tournament.objects.get_or_create(
        name='Root Digital League'
    )
    
    # Get or create Round
    round_name = match_data.get('tournament', '')
    game_round = None
    if round_name:
        game_round = get_game_round(date_registered=date_registered, round_name=round_name, tournament=tournament)
    
    # Get deck
    deck_name = DECK_MAP.get(match_data.get('deck'))
    deck = None
    if deck_name:
        deck = Deck.objects.filter(title=deck_name).first()
    
    # Get map
    map_name = MAP_MAP.get(match_data.get('board_map'))
    game_map = None
    if map_name:
        game_map = Map.objects.filter(title=map_name).first()
    
    undrafted_faction_key = match_data.get('undrafted_faction')
    undrafted_faction = None
    undrafted_vagabond = None
    if undrafted_faction_key in VAGABOND_MAP:
        # Map Vagabond and set Vagabond as faction
        undrafted_vagabond_name = VAGABOND_MAP[undrafted_faction_key]
        undrafted_vagabond = Vagabond.objects.filter(title=undrafted_vagabond_name).first()
    # Get undrafted faction
    if undrafted_faction_key:
        faction_name = FACTION_MAP.get(undrafted_faction_key)
        if faction_name:
            undrafted_faction = Faction.objects.filter(title=faction_name).first()
    
    # Determine game type
    game_type = Game.TypeChoices.ASYNC if match_data.get('turn_timing') == 'async' else Game.TypeChoices.LIVE
    

    nickname = clean_nickname(match_data.get('title', ''))

    random_clearing = bool(match_data.get('random_suits'))

    # Update all fields
    game.type = game_type
    game.platform = PlatformChoices.DWD
    game.deck = deck
    game.map = game_map
    game.round = game_round
    game.undrafted_faction = undrafted_faction
    game.undrafted_vagabond = undrafted_vagabond
    game.link = match_data.get('table_talk_url', '')
    game.nickname = nickname
    game.random_clearing = random_clearing
    game.date_posted = date_registered
    
    # Clear old hirelings
    game.hirelings.clear()
    game.landmarks.clear()

    # Add new hirelings
    hireling_fields = ['hirelings_a', 'hirelings_b', 'hirelings_c']
    for field in hireling_fields:
        key = match_data.get(field)
        if key:
            title = HIRELING_MAP.get(key)
            if title:
                hireling = Hireling.objects.filter(title__iexact=title).first()
                if hireling:
                    game.hirelings.add(hireling)

    # Add landmarks
    landmark_fields = ['landmark_a', 'landmark_b']
    for field in landmark_fields:
        key = match_data.get(field)
        if key:
            title = LANDMARK_MAP.get(key)
            if title:
                landmark = Landmark.objects.filter(title__iexact=title).first()
                if landmark:
                    game.landmarks.add(landmark)



    game.save()
    
    return game


def create_efforts_from_api(game, participants):
    """Create Effort objects from API participants data."""
    
    # First pass: create a lookup dict of player -> faction for coalition matching
    player_faction_lookup = {}
    for participant in participants:
        faction_key = participant['faction']
        if faction_key in VAGABOND_MAP:
            # For vagabonds, store the "Vagabond" faction
            faction = Faction.objects.filter(title='Vagabond').first()
        elif faction_key in FACTION_MAP:
            faction_name = FACTION_MAP[faction_key]
            faction = Faction.objects.filter(title=faction_name).first() if faction_name else None
        else:
            faction = None
        
        player_faction_lookup[participant['player']] = faction

    # Second pass: Create efforts
    for participant in participants:
        # Get player identifier from API
        full_player_string = participant['player']  # e.g., "MrMirz+1445"


        parts = full_player_string.split('+', 1)

        player_without_number = parts[0] # e.g., "MrMirz"
        player_number = parts[1] if len(parts) > 1 else "0000"
        standard_player_number = str(player_number).zfill(4)
        standard_player_string = f'{full_player_string}+{standard_player_number}'
        
        player = Profile.objects.filter(dwd__iexact=standard_player_string).first()

        # 1. Non-standardized match on dwd field
        if not player:
            player = Profile.objects.filter(dwd__iexact=full_player_string).first()
            if player:
                player.dwd = standard_player_string
                player.save()

        # 2. Match dwd without the +number and update
        if not player:
            player = Profile.objects.filter(dwd__iexact=player_without_number).first()
            if player:
                player.dwd = standard_player_string
                player.save()
    
        # 3. Match discord without the +number and update
        if not player:
            player = Profile.objects.filter(discord__iexact=player_without_number, dwd__isnull=True).first()
            if player:
                player.dwd = standard_player_string
                player.save()
        
        # 4. Create new profile if no match found
        if not player:
            if Profile.objects.filter(discord__iexact=player_without_number).exists():
                player, created = Profile.objects.get_or_create(
                    discord=full_player_string.lower(),
                    defaults={
                        'dwd': standard_player_string
                    }
                ) 
                send_discord_message(f'Duplicate user {player} added.', 'user_updates')
            else:
                player, created = Profile.objects.get_or_create(
                    discord=player_without_number.lower(),
                    defaults={
                        'dwd': standard_player_string
                    }
                )
        # Determine if this is a faction or vagabond
        faction_key = participant['faction']
        faction = None
        vagabond = None
        
        if faction_key in VAGABOND_MAP:
            # Map Vagabond and set Vagabond as faction
            vagabond_name = VAGABOND_MAP[faction_key]
            vagabond = Vagabond.objects.filter(title=vagabond_name).first()
            # Also set faction to "Vagabond"
            faction = Faction.objects.filter(title='Vagabond').first()
        elif faction_key in FACTION_MAP:
            # Map Faction
            faction_name = FACTION_MAP[faction_key]
            if faction_name:
                faction = Faction.objects.filter(title=faction_name).first()
        
        # Handle coalition
        coalition_with = None
        if participant.get('coalition'):
            coalition_player = participant['coalition']
            coalition_with = player_faction_lookup.get(coalition_player)
        

        # Determine if winner
        is_winner = float(participant.get('tournament_score', 0)) > 0
        
        # Get score, handle null values
        score = participant.get('game_score')
        if score is not None:
            score = int(score)
        else:
            score = None

        # Get dominance if applicable
        dominance = None
        if participant.get('dominance'):
            dominance_map = {
                'fox': Effort.DominanceChoices.FOX,
                'rabbit': Effort.DominanceChoices.RABBIT,
                'mouse': Effort.DominanceChoices.MOUSE,
                'bird': Effort.DominanceChoices.BIRD,
            }
            dominance = dominance_map.get(participant['dominance'].lower())
        
        status = StatusChoices.STABLE

        # Create the effort
        Effort.objects.create(
            seat=participant.get('turn_order'),
            player=player,
            faction=faction,
            vagabond=vagabond,
            coalition_with=coalition_with,
            dominance=dominance,
            win=is_winner,
            score=score,
            game=game,
            faction_status=status,
        )
