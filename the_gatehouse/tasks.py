from celery import shared_task
from the_keep.models import StatusChoices, Faction, Vagabond, Deck, Map, Landmark, Hireling, Tweak
from the_warroom.models import Game, Effort
from django.utils import timezone
from .discordservice import send_discord_message, send_rich_discord_message
from .utils import format_bulleted_list
from .models import DailyUserVisit

@shared_task
def test_task():
    send_discord_message("This is a scheduled test.", category="feedback")

def update_status_fk_model(obj_queryset, related_model, related_field, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_related = related_model.objects.filter(
            **{related_field: obj},
            date_posted__gte=inactive_period
        ).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_related:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])


        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_related and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])


    return development_count, development_list, inactive_count, inactive_list


def update_status_m2m_model(obj_queryset, related_name, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_games = getattr(obj, related_name).filter(date_posted__gte=inactive_period).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_games:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])

        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_games and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])

    return development_count, development_list, inactive_count, inactive_list



@shared_task
def update_post_status():
    from dateutil.relativedelta import relativedelta
    inactive_period = timezone.now() - relativedelta(months=6)

    development_count = 0
    development_list = []
    inactive_count = 0
    inactive_list = []


    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Faction.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'faction',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list,
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Vagabond.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'vagabond',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Deck.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'deck',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Map.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'map',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )


    # Many-to-many fields (Landmark, Tweak, Hireling)
    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Landmark.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Tweak.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Hireling.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    # Create cleanup message
    fields = []
    if development_count or inactive_count:
        if development_count:
            fields.append({
                'name': 'Development',
                'value': format_bulleted_list(development_list)
            })
        if inactive_count:
            fields.append({
                'name': 'Inactive', 
                'value': format_bulleted_list(inactive_list)
                })
        if not inactive_count:
            message = f'{development_count} Post(s) moved to Development.'
        elif not development_count:
            message = f'{inactive_count} Post(s) moved to Inactive.'
        else:
            message = f'{development_count} Post(s) moved to Development and {inactive_count} Post(s) moved to Inactive.'
    else:
        message = f'No Posts moved during cleanup.'

        
    # Call cleanup summary message to Discord
    send_rich_discord_message(
        message,
        author_name='RDB Admin',
        category='inactive-cleanup',
        title='Inactive Cleanup',
        fields=fields
    )

@shared_task
def daily_users():
    today_users = DailyUserVisit.objects.filter(date=timezone.localdate())
    print(today_users)

    # Count them
    user_count = today_users.count()

    # List their names
    usernames = today_users.values_list('profile__discord', flat=True)

    message = f'{user_count} Authenticated Users'
    fields = []
    if user_count:
        fields.append({
            'name': 'Users', 
            'value': format_bulleted_list(usernames)
            })
    print(user_count)
    send_rich_discord_message(
        message,
        author_name='RDB Admin',
        category='user-summary',
        title='Daily User Summary',
        fields=fields
    )