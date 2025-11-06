import random

from itertools import groupby
from operator import attrgetter
from datetime import date

from django.db.models import Q, F
from django.utils import timezone
from django.contrib.auth.models import User

from the_gatehouse.models import Website, Theme, BackgroundImage, ForegroundImage, Holiday, DailyUserVisit
from the_gatehouse.utils import format_bulleted_list


def get_theme(request):
    # now = timezone.now()
    config = Website.get_singular_instance()
    
    current_holiday = get_current_holiday()

    if current_holiday:
        current_theme = Theme.objects.filter(holiday=current_holiday, active=True).first()
    else:
        current_theme = None  # No active holiday theme
    
    # Determine the theme to use
    if current_theme:
        theme = current_theme
    else:
        if request.user.is_authenticated and request.user.profile.theme:
            theme = request.user.profile.theme  # User's theme if authenticated
        else:
            theme = config.default_theme  # Default theme if user is not authenticated
    
    return theme


def get_current_holiday():
    today_doy = date.today().timetuple().tm_yday

    current_holidays = Holiday.objects.filter(
        (
            Q(start_day_of_year__lte=today_doy, end_day_of_year__gte=today_doy)  # Normal range
            | Q(start_day_of_year__gt=F('end_day_of_year'), end_day_of_year__gte=today_doy)  # Wrap after new year
            | Q(start_day_of_year__gt=F('end_day_of_year'), start_day_of_year__lte=today_doy)  # Wrap before new year
        ),
        # Only include holidays that have a theme
        theme__isnull=False,
        theme__active=True,
    ).distinct()


    if current_holidays.exists():
        return current_holidays.order_by('-start_date', 'end_date', 'id').first()
    
    return None



def get_thematic_images(theme, page=None):
    """
    Returns a background image and a list of randomly chosen foreground images grouped by location,
    all filtered by theme and page.
    If the theme has a backup theme, 
    then any locations that don't have an image in the theme will use backup images.
    """    
    # If there is no page, return a list of the theme's artists and nothing else
    if page is None:
        return None, [], list(theme.theme_artists.all())


    # 1. Random background image
    background_image = BackgroundImage.objects.filter(theme=theme, page=page).order_by('?').first()
    if not background_image and theme.backup_theme and theme != theme.backup_theme:
        background_image = BackgroundImage.objects.filter(theme=theme.backup_theme, page=page).order_by('?').first()

    # 2. Foreground images from main theme
    all_foreground_images = list(ForegroundImage.objects.filter(theme=theme, page=page))
    location_to_images = {}

    # 3. Group main theme by location
    for location, group in groupby(sorted(all_foreground_images, key=attrgetter('location')), key=attrgetter('location')):
        location_to_images[location] = list(group)

    # 4. Check for missing locations from backup theme
    if theme.backup_theme and theme != theme.backup_theme:
        backup_foreground_images = ForegroundImage.objects.filter(theme=theme.backup_theme, page=page)

        for location, group in groupby(sorted(backup_foreground_images, key=attrgetter('location')), key=attrgetter('location')):
            if location not in location_to_images:
                location_to_images[location] = list(group)

    # 5. Select one random image per location
    foreground_images = [random.choice(images) for images in location_to_images.values()]

    artists = {image.artist for image in foreground_images if image.artist}

    if background_image and background_image.artist:
        artists.add(background_image.artist)

    for artist in theme.theme_artists.all():
        if artist:
            artists.add(artist)

    artists = list(artists)

    if background_image:
        pattern = background_image.pattern


    return background_image, foreground_images, artists, pattern



def get_daily_user_summary(date=None):
    """Collects all user activity stats for a given date (defaults to today)."""
    date = date or timezone.localdate()

    # Get users who visited today
    active_users = DailyUserVisit.objects.filter(date=date)
    user_count = active_users.count()

    # Pull usernames and groups
    user_info = active_users.values_list('profile__discord', 'profile__group')
    usernames = [
        f"{discord} ({group})" if group else f"{discord}"
        for discord, group in user_info
    ]

    # Get newly registered users
    new_users = User.objects.filter(date_joined__date=date)
    new_user_count = new_users.count()
    new_usernames = [u.profile.discord for u in new_users if hasattr(u, 'profile')]

    # Format for reuse elsewhere (e.g., dashboard)
    summary = {
        'date': date,
        'user_count': user_count,
        'new_user_count': new_user_count,
        'usernames': usernames,
        'new_usernames': new_usernames,
        'fields': [],
    }

    if new_usernames:
        summary['fields'].append({
            'name': 'New Users',
            'value': format_bulleted_list(new_usernames),
        })
    if usernames:
        summary['fields'].append({
            'name': 'Active Users',
            'value': format_bulleted_list(usernames),
        })

    # Human-readable message for Discord or display
    if user_count == 1:
        user_string = "1 Active User"
    else:
        user_string = f"{user_count} Active Users"

    if new_user_count:
        if new_user_count == 1:
            new_user_string = "1 New User"
        else:
            new_user_string = f"{new_user_count} New Users"
        message = f"{user_string} - {new_user_string}"
    else:
        message = user_string

    summary['message'] = message

    return summary



# def detect_language(request):

#     if request.user.is_authenticated:
#         profile = request.user.profile
#         if profile.language:
#             language = profile.language.code
#             return language
#     # First try getting from the request (browser or URL)
#     language = get_language_from_request(request)
    
#     # If not found, fall back to session or default language
#     if not language:
#         language = get_language()
    
#     return language

# def set_language(request):
#     # Check if the user is logged in and has a saved language preference
#     if request.user.is_authenticated and hasattr(request.user.profile, 'language'):
#         user_language = request.user.profile.language.code
#     elif 'language' in request.session:
#         # Use the session value if available
#         user_language = request.session['language']
#     else:
#         # Fall back to the browser's default language
#         user_language = get_language_from_request(request)

#     # Activate the language
#     activate(user_language)

#     # Optionally, store the selected language in the session
#     request.session['language'] = user_language
