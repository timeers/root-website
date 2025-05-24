import random
from decimal import Decimal
from django.utils.text import slugify

from django.apps import apps
import uuid
from urllib.parse import urljoin

from datetime import date
from django.db.models import Q, F

from itertools import groupby
from operator import attrgetter


def slugify_instance_discord(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        if instance.discord:
            slug = slugify(instance.discord)
        elif instance.user:
            slug = slugify(instance.user.username)
        else:
            slug = slugify(instance.dwd)
    Klass = instance.__class__
    qs = Klass.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_instance_discord(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance



def get_uuid(request):
    # Check if the 'visitor_uuid' is already in the session
    if 'visitor_uuid' not in request.session:
        # If not, create a new UUID and store it in the session
        # Generate a UUID
        original_uuid = uuid.uuid4()
        # Take the first 8 characters
        shortened_uuid = str(original_uuid).replace('-', '')[:8]
        request.session['visitor_uuid'] = shortened_uuid

    # Retrieve the UUID from the session
    visitor_uuid = request.session['visitor_uuid']

    # Pass the UUID
    return visitor_uuid

def incriment_session_pages(request):
    # Check if the 'session_pages' is already in the session
    if 'session_pages' not in request.session:
        # If not, create a new UUID and store it in the session
        request.session['session_pages'] = 0

    # Retrieve the session_pages from the session
    session_pages = request.session['session_pages']+1
    request.session['session_pages'] = session_pages
    # Pass the session_pages
    return session_pages

def get_base_url(request):
    # Get the scheme (http or https)
    scheme = request.scheme  # 'http' or 'https'

    # Get the domain (host) and port (if available)
    host = request.get_host()  # Includes domain and optional port, like 'example.com' or 'example.com:8000'

    # Combine scheme and host to form the full base URL
    base_url = f"{scheme}://{host}"

    return base_url


def build_absolute_uri(request, relative_url):
    full_url = urljoin(get_base_url(request), relative_url)
    return full_url



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


def get_theme(request):
    # now = timezone.now()
    config = apps.get_model('the_gatehouse', 'Website').get_singular_instance()
    
    # Get the current holidays that are active
    # current_holidays = apps.get_model('the_gatehouse', 'Holiday').objects.filter(start_date__lte=now, end_date__gte=now)
    
    # today_doy = date.today().timetuple().tm_yday

    # current_holidays = apps.get_model('the_gatehouse', 'Holiday').objects.filter(
    #     Q(start_day_of_year__lte=today_doy, end_day_of_year__gte=today_doy) |  # Normal range
    #     Q(start_day_of_year__gt=F('end_day_of_year'),  # Handles year wrap (e.g. Dec 25–Jan 5)
    #     end_day_of_year__gte=today_doy) | 
    #     Q(start_day_of_year__gt=F('end_day_of_year'),
    #     start_day_of_year__lte=today_doy)
    # )

    current_holiday = get_current_holiday()


    # # If there are any active holidays, get the most recent one
    # if current_holidays.exists():
    #     current_holiday = current_holidays.order_by('-start_date').first()  # Get the holiday that started most recently
    if current_holiday:
        current_theme = apps.get_model('the_gatehouse', 'Theme').objects.filter(holiday=current_holiday).first()
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
    Holiday = apps.get_model('the_gatehouse', 'Holiday')

    current_holidays = Holiday.objects.filter(
        Q(start_day_of_year__lte=today_doy, end_day_of_year__gte=today_doy) |  # Normal range
        Q(start_day_of_year__gt=F('end_day_of_year'), end_day_of_year__gte=today_doy) |  # Wrap: after new year
        Q(start_day_of_year__gt=F('end_day_of_year'), start_day_of_year__lte=today_doy)  # Wrap: before new year
    )

    if current_holidays.exists():
        return current_holidays.order_by('-start_date').first()
    
    return None



def get_thematic_images(theme, page):
    """
    Returns a background image and a list of randomly chosen foreground images grouped by location,
    all filtered by theme and page.
    """    
    
    BackgroundImage = apps.get_model('the_gatehouse', 'BackgroundImage')
    ForegroundImage = apps.get_model('the_gatehouse', 'ForegroundImage')

    # # Get a random background image
    # background_image = BackgroundImage.objects.filter(theme=theme, page=page).order_by('?').first()
    # if not background_image and theme.backup_theme and theme != theme.backup_theme:
    #     background_image = BackgroundImage.objects.filter(theme=theme.backup_theme, page=page).order_by('?').first()

    # # Get all matching foreground images
    # all_foreground_images = ForegroundImage.objects.filter(theme=theme, page=page)



    # # Sort and group foreground images by location
    # grouped_by_location = groupby(
    #     sorted(all_foreground_images, key=attrgetter('location')),
    #     key=attrgetter('location')
    # )

    # if theme.backup_theme and theme != theme.backup_theme:
    #     backup_foreground_images = ForegroundImage.objects.filter(theme=theme.backup_theme, page=page)
    #     backup_by_location = groupby(
    #         sorted(backup_foreground_images, key=attrgetter('location')),
    #         key=attrgetter('location')
    #     )

    # # Select one random image per location
    # foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]



    # return background_image, foreground_images

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

    return background_image, foreground_images




def int_to_roman(n):
    val = [
        1000, 900, 500, 400,
        100, 90, 50, 40,
        10, 9, 5, 4, 1
    ]
    syms = [
        "M", "CM", "D", "CD",
        "C", "XC", "L", "XL",
        "X", "IX", "V", "IV", "I"
    ]
    roman = ''
    i = 0
    while n > 0:
        for _ in range(n // val[i]):
            roman += syms[i]
            n -= val[i]
        i += 1
    return roman

def int_to_alpha(n):
    # Converts 1 -> a, 2 -> b, ..., 26 -> z, 27 -> aa, etc.
    result = ''
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(97 + remainder) + result  # 97 = 'a'
    return result
