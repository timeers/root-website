import requests
import time
from celery import shared_task
from dateutil import parser, relativedelta
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from the_warroom.models import Game, Effort, PlatformChoices, Tournament, Round
from the_keep.models import Faction, Vagabond, Deck, Map, Profile, StatusChoices
from the_gatehouse.utils import format_bulleted_list
from the_gatehouse.discordservice import send_rich_discord_message

# Import League Games from Pliskin.dev REST API

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

# Imports all games from the last 8 days
@shared_task
def import_league_games(limit=100, tournament_name="M", days_back=1, date_from=None, date_to=None):
    """
    Import games from the Root League API.
    
    Args:
        limit: Number of games to fetch per request
        tournament_name: String included in the tournament's name
        days_back: Number of days back to import (default 1, ignored if date_from is provided)
        date_from: Optional - specific start date (datetime or ISO string)
        date_to: Optional - specific end date (datetime or ISO string)
    """
    base_url = "https://rootleague.pliskin.dev/api/match/"
    
    imported_count = 0
    skipped_count = 0
    error_count = 0

    imported_list = []
    skipped_list = []
    error_list = []
    
    offset = 0
    has_more = True

    # Determine date range
    if date_from:
        # If date_from is a string, parse it
        if isinstance(date_from, str):
            start_date = parser.parse(date_from)
        else:
            start_date = date_from
    else:
        # Use days_back as default
        start_date = timezone.now() - timedelta(days=days_back)
    
    # Parse date_to if provided
    end_date = None
    if date_to:
        if isinstance(date_to, str):
            end_date = parser.parse(date_to)
        else:
            end_date = date_to
    
    while has_more:
        # Fetch data from API
        params = {
            'tournament_name': tournament_name,
            'limit': limit,
            'offset': offset,
            'date_closed__gte': start_date.isoformat()
        }
        
        # Only add date_closed__lte if end_date is provided
        if end_date:
            params['date_closed__lte'] = end_date.isoformat()
        
        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching API data: {e}")
            error_count += 1
            break
        
        results = data.get('results', [])
        
        if not results:
            has_more = False
            break
        
        for match_data in results:
            try:
                # Check if game already exists by league_id
                league_id = str(match_data['id'])
                if Game.objects.filter(league_id=league_id).exists():
                    print(f"Game {league_id} already exists, skipping")
                    skipped_count += 1
                    skipped_list.append(league_id)
                    continue

                # Atomic transaction - if anything fails, nothing gets saved to prevent incomplete game submission
                with transaction.atomic():
                    # Create the Game
                    game = create_game_from_api(match_data)
                    
                    # Create the Efforts (participants)
                    create_efforts_from_api(game, match_data['participants'])
                
                imported_count += 1
                imported_list.append(league_id)
                print(f"Successfully imported game {league_id}")
                
            except Exception as e:
                print(f"Error importing game {str(match_data.get('id'))}: {e}")
                error_count += 1
                error_list.append(league_id)
                continue
        
        # Check if there are more results
        has_more = data.get('next') is not None
        # Add delay before next API call
        if has_more:
            time.sleep(0.5)  # 500ms delay between API requests
        offset += limit
    
    summary = f"Import complete: {imported_count} imported, {skipped_count} skipped, {error_count} errors"
    message = f"Import complete: {imported_count} imported and {error_count} errors"

    # Create import message
    fields = []
    if imported_list:
        fields.append({
            'name': 'Imported',
            'value': format_bulleted_list(imported_list),
        })
    # if skipped_list:
    #     fields.append({
    #         'name': 'Skipped',
    #         'value': format_bulleted_list(skipped_list),
    #     })
    if error_list:
        fields.append({
            'name': 'Error',
            'value': format_bulleted_list(error_list),
        })
    
    if imported_count > 0 or error_count > 0:
        # Send import summary message to Discord
        send_rich_discord_message(
            message,
            author_name='RDB Admin',
            category='report',
            title='RDL Import',
            fields=fields
        )


    return summary


# Checks all the games modified in the last 2 days and updates them
@shared_task
def update_league_games(limit=100, days_back=2, days_cutoff=1, date_from=None, date_to=None):
    """
    Check for games that were modified after initial submission and update them.
    
    Args:
        limit: Number of games to fetch per request
        days_back: Number of days back to update (default 2, ignored if date_from is provided)
        days_cutoff: How many days from now to stop checking (default 1 = yesterday)
                     Example: days_back=2, days_cutoff=1 checks games from 2-1 days ago
        date_from: Optional - specific start date (datetime or ISO string)
        date_to: Optional - specific end date (datetime or ISO string)
    """
    
    base_url = "https://rootleague.pliskin.dev/api/match/"
    updated_count = 0
    error_count = 0
    skipped_count = 0
    updated_list = []
    error_list = []
    
    offset = 0
    has_more = True
    
    # Determine date range
    if date_from:
        # If date_from is a string, parse it
        if isinstance(date_from, str):
            start_date = parser.parse(date_from)
        else:
            start_date = date_from
    else:
        # Use days_back as default
        start_date = timezone.now() - timedelta(days=days_back)
    
    # Parse date_to if provided
    end_date = None
    if date_to:
        if isinstance(date_to, str):
            end_date = parser.parse(date_to)
        else:
            end_date = date_to
    else:
        end_date = timezone.now() - timedelta(days=days_cutoff)

    while has_more:
        # Fetch data from API
        params = {
            'limit': limit,
            'offset': offset,
            'date_modified__gte': start_date.isoformat()
        }
        
        # Only add date_modified__lte if end_date is provided
        if end_date:
            params['date_modified__lte'] = end_date.isoformat()
        
        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching API data: {e}")
            error_count += 1
            break
        
        results = data.get('results', [])
        
        if not results:
            has_more = False
            break
        
        for match_data in results:
            try:
                
                # Parse dates
                date_closed = parser.parse(match_data.get('date_closed'))
                date_modified = parser.parse(match_data.get('date_modified'))
                
                # Check if modified at least 5 seconds after closing (to account for API timing)
                time_diff = (date_modified - date_closed).total_seconds()
                
                # Skip if not actually modified (less than 60 seconds difference)
                if time_diff < 60:
                    skipped_count += 1
                    continue
                
                league_id = str(match_data['id'])
                
                # Check if game exists in Root Database
                try:
                    game = Game.objects.get(league_id=league_id)
                except Game.DoesNotExist:
                    # Game doesn't exist, skip
                    continue
                
                print(f"Game {league_id} was modified {time_diff:.0f} seconds after closing. Updating...")
                
                # Use atomic transaction to update
                with transaction.atomic():
                    # Delete existing efforts
                    game.efforts.all().delete()
                    
                    # Update game data
                    update_game_from_api(game, match_data)
                    
                    # Create efforts with new data
                    create_efforts_from_api(game, match_data['participants'])
                
                updated_count += 1
                updated_list.append(league_id)
                print(f"Successfully updated game {league_id}")
                
            except Exception as e:
                print(f"Error updating game {str(match_data.get('id'))}: {e}")
                import traceback
                traceback.print_exc()
                error_count += 1
                error_list.append(str(match_data.get('id')))
                continue
        
        # Check if there are more results
        has_more = data.get('next') is not None
        # Add delay before next API call
        if has_more:
            time.sleep(0.5)  # 500ms delay between API requests
        offset += limit
    
    summary = f"Update check complete: {updated_count} updated, {skipped_count} skipped, {error_count} errors"
    
    # Create update message
    fields = []
    if updated_list:
        fields.append({
            'name': 'Updated',
            'value': format_bulleted_list(updated_list),
        })
    if error_list:
        fields.append({
            'name': 'Error',
            'value': format_bulleted_list(error_list),
        })
    
    # Send update summary message to Discord
    if updated_count > 0 or error_count > 0:
        send_rich_discord_message(
            summary,
            author_name='RDB Admin',
            category='report',
            title='RDL Update Check',
            fields=fields
        )
    
    print(summary)
    return summary


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

        # Start day of League
        first_of_month = date_registered.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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

    raw_nickname = match_data.get('title', '')
    nickname = raw_nickname.strip()
    # Normalize to lowercase to catch all cases
    lower_nickname = nickname.lower()
    # If it contains a URL or Discord link, set to None
    if 'https://' in lower_nickname or 'discord.com' in lower_nickname or 'import game 20' in lower_nickname:
        nickname = None

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
    
    return game


def update_game_from_api(game, match_data):
    """Update an existing Game object with new data from API."""
    
    date_registered = parser.parse(match_data.get('date_registered', match_data.get('date_closed')))
    
    # Get or create Tournament
    from the_warroom.models import Tournament
    tournament, created = Tournament.objects.get_or_create(
        name='Root Digital League'
    )
    
    # Get or create Round
    round_name = match_data.get('tournament', '')
    game_round = None
    if round_name:

        # Start day of League
        first_of_month = date_registered.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
    

    raw_nickname = match_data.get('title', '')
    nickname = raw_nickname.strip()
    # Normalize to lowercase to catch all cases
    lower_nickname = nickname.lower()
    # If it contains a URL or Discord link, set to None
    if 'https://' in lower_nickname or 'discord.com' in lower_nickname or 'import game 20' in lower_nickname:
        nickname = None

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
        player_without_number = full_player_string.split('+')[0]  # e.g., "MrMirz"
        
        # 1. Exact match on dwd field
        player = Profile.objects.filter(dwd__iexact=full_player_string).first()
        
        # 2. Match dwd without the +number and update
        if not player:
            player = Profile.objects.filter(dwd__iexact=player_without_number).first()
            if player:
                player.dwd = full_player_string
                player.save()
    
        # 3. Match discord without the +number and update
        if not player:
            player = Profile.objects.filter(discord__iexact=player_without_number).first()
            if player:
                player.dwd = full_player_string
                player.save()
        
        # 4. Create new profile if no match found
        if not player:
            player = Profile.objects.create(
                dwd=full_player_string,
                discord=player_without_number
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


@shared_task
def test_import_league_games_single_page(limit=100, tournament_name="M04", days_back=2):
    """
    Test import - only processes the FIRST page of results.
    
    Args:
        limit: Number of games to fetch
        tournament_name: String included in the tournament's name
        days_back: Filter by date modified
    """
    base_url = "https://rootleague.pliskin.dev/api/match/"
    imported_count = 0
    skipped_count = 0
    error_count = 0
    imported_list = []
    skipped_list = []
    error_list = []
    
    # Calculate cutoff date
    cutoff_date = timezone.now() - timedelta(days=days_back)
    
    # Fetch ONLY the first page
    params = {
        'tournament_name': tournament_name,
        'limit': limit,
        'offset': 0,  # Always start at 0
        'date_modified__gte': cutoff_date.isoformat()
    }
    
    try:
        response = requests.get(base_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return f"Error fetching API data: {e}"
    
    results = data.get('results', [])
    
    for match_data in results:
        try:
            # Check if game already exists by league_id
            league_id = str(match_data['id'])
            if Game.objects.filter(league_id=league_id).exists():
                print(f"Game {league_id} already exists, skipping")
                skipped_count += 1
                skipped_list.append(league_id)
                continue
            
            # Atomic transaction - if anything fails, nothing gets saved
            with transaction.atomic():
                # Create the Game
                game = create_game_from_api(match_data)
                
                # Create the Efforts (participants)
                create_efforts_from_api(game, match_data['participants'])
            
            imported_count += 1
            imported_list.append(league_id)
            print(f"Successfully imported game {league_id}")
            
        except Exception as e:
            print(f"Error importing game {str(match_data.get('id'))}: {e}")
            import traceback
            traceback.print_exc()
            error_count += 1
            error_list.append(league_id)
            continue
    
    summary = f"Test import complete (first page only): {imported_count} imported, {skipped_count} skipped, {error_count} errors"
    message = f"Test import complete: {imported_count} imported and {error_count} errors"
    
    # Create import message
    fields = []
    if imported_list:
        fields.append({
            'name': 'Imported',
            'value': format_bulleted_list(imported_list),
        })
    if error_list:
        fields.append({
            'name': 'Error',
            'value': format_bulleted_list(error_list),
        })
    
    if imported_count > 0 or error_count > 0:
        # Call import summary message to Discord
        send_rich_discord_message(
            message,
            author_name='RDB Admin',
            category='report',
            title='RDL Test Import',
            fields=fields
        )
    
    print(summary)
    return summary