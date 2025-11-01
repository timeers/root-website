import random
import uuid

from urllib.parse import urljoin
from unidecode import unidecode

from django.utils.text import slugify



def slugify_instance_discord(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        if instance.discord:
            slug = slugify(unidecode(instance.discord))
        elif instance.user:
            slug = slugify(unidecode(instance.user.username))
        else:
            slug = slugify(unidecode(instance.dwd))
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

def get_int_param(param, default=None):
    try:
        return int(param)
    except (TypeError, ValueError):
        return default
    
def format_bulleted_list(items):
    return "\n".join(f"• {item}" for item in items[:20])


