import requests
import time

from celery import shared_task
from dateutil import parser, relativedelta
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from the_gatehouse.utils import format_bulleted_list
from the_gatehouse.services.discordservice import send_rich_discord_message

from .models import Game, Tournament, Round
from .services.root_league_api import create_game_from_api, create_efforts_from_api, update_game_from_api


# Import League Games from Pliskin.dev REST API

BASE_URL = "https://rootleague.pliskin.dev/api/match/"

# Imports all games from the last 1 day
@shared_task
def import_league_games(limit=25, tournament_name="", days_back=1, date_from=None, date_to=None):
    """
    Import games from the Root League API.
    
    Args:
        limit: Number of games to fetch per request
        tournament_name: String included in the tournament's name
        days_back: Number of days back to import (default 1, ignored if date_from is provided)
        date_from: Optional - specific start date (datetime or ISO string)
        date_to: Optional - specific end date (datetime or ISO string)
    """
    
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
            'tournament__name': tournament_name,
            'limit': limit,
            'offset': offset,
            'date_closed__gte': start_date.isoformat()
        }
        
        # Only add date_closed__lte if end_date is provided
        if end_date:
            params['date_closed__lte'] = end_date.isoformat()
        
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
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
        if error_list:
            error_field = {
                'name': 'Errors',
                'value': format_bulleted_list(error_list),
            }
            fields.append(error_field)

            # Send error message
            send_rich_discord_message(
                message,
                author_name='RDB Admin',
                category='report',
                title='Import Errors',
                fields=[error_field]
            )

    if imported_count > 0 or error_count > 0:
        # Send import summary message to Discord
        send_rich_discord_message(
            message,
            author_name='RDB Admin',
            category='rdl-import',
            title='RDL Import',
            fields=fields
        )

    return summary


# Checks all the games modified in the last 2 days and updates them
@shared_task
def update_league_games(limit=50, days_back=2, days_cutoff=1, date_from=None, date_to=None):
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
            response = requests.get(BASE_URL, params=params, timeout=30)
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

        # Build fields for summary message
        fields = []

        if updated_list:
            fields.append({
                'name': 'Updated',
                'value': format_bulleted_list(updated_list),
            })

        if error_list:
            error_field = {
                'name': 'Errors',
                'value': format_bulleted_list(error_list),
            }
            fields.append(error_field)

            # Send error message
            send_rich_discord_message(
                summary,
                author_name='RDB Admin',
                category='report',
                title='Update Errors',
                fields=[error_field]
            )

        # Send main update summary (only if anything happened)
        if updated_count > 0 or error_count > 0:
            send_rich_discord_message(
                summary,
                author_name='RDB Admin',
                category='rdl-update',
                title='RDL Update Check',
                fields=fields
            )

    print(summary)
    return summary

@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=True, max_retries=3)
def check_all_league_rounds(delete=False, list_games=False):
    tournament, _ = Tournament.objects.get_or_create(name='Root Digital League')
    results = {}
    total_missing_count = 0
    for round_obj in Round.objects.filter(tournament=tournament):
        site_count, api_count, missing_count = compare_league_game_count(round_obj)

        if missing_count > 0:
            results[round_obj.name] = {
                'site_count': site_count,
                'api_count': api_count,
                'missing_count': missing_count,
                }
            total_missing_count += missing_count
            print(f"Round '{round_obj.name}' is out of sync! Missing {missing_count} games.")

            # Only list games if explicitly requested
            if list_games:
                deleted_ids = find_deleted_games(round_obj)
                if deleted_ids:
                    results[round_obj.name]["deleted_game_ids"] = list(deleted_ids)


                    # Only delete if explicitly requested
                    if delete:
                        count, _ = Game.objects.filter(league_id__in=deleted_ids, round=round_obj).delete()
                        print(f"Deleted {count} games from '{round_obj.name}'")
    if not results:
        print("All Root Digital League rounds are up to date.")
    else:
        fields = []
        for key, value in results.items():
            if value.get('deleted_game_ids'):
                fields.append({
                    'name': key,
                    'value': format_bulleted_list(value['deleted_game_ids'])
                })
            elif value['missing_count']:
                fields.append({
                    'name': key,
                    'value': f"{value['missing_count']} games missing"
                })

        send_rich_discord_message(
            f'{total_missing_count} games missing from RDL',
            author_name='RDB Admin',
            category='rdl-delete',
            title='Deleted Games',
            fields=fields
        )

    return results



def find_deleted_games(league_round, limit=200):
    """
    Compare local games in a round with API data and return a list of missing (deleted) game IDs.

    Args:
        league_round: The Round object to check
        limit: Number of games per page to fetch from the API
    Returns:
        set of league_ids that exist locally but not in the API
    """
    tournament_name = league_round.name

    params = {
        'limit': limit,
        'tournament__name': tournament_name
    }
    api_game_ids = set()
    url = BASE_URL

    # Fetch all pages from the API
    while url:
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f'Error fetching data for "{tournament_name}": {e}')
            raise

        results = data.get("results", [])
        api_game_ids.update(g["id"] for g in results if "id" in g)

        # Move to the next page
        url = data.get("next")
        params = None  # only send params once

    # Get all local game IDs for this round
    site_game_ids = set(
        Game.objects.filter(round=league_round).values_list("league_id", flat=True)
    )

    # Identify games missing from API
    deleted_ids = site_game_ids - api_game_ids

    return deleted_ids


def compare_league_game_count(league_round):
    """
    Compare the count of league games on the site to the API count

    Args:
        league_round: A round object from Root Digital League

    """

    tournament_name = league_round.name

    site_count = Game.objects.filter(round=league_round).count()

    api_count = count_games_from_api(tournament_name=tournament_name)

    missing_count = site_count - api_count


    return site_count, api_count, missing_count

def count_games_from_api(tournament_name):
    """
    Count games in tournament from the Root League API.
    
    Args:
        tournament_name: String included in the tournament's name
    """

    # Fetch data from API
    params = {
        'tournament__name': tournament_name,
        'limit': 1,
        }
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('count', 0)
    except requests.RequestException as e:
        print(f"Error fetching API data: {e}")
        return 0