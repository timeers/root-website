import random
import uuid
from django.db import models

from urllib.parse import urljoin
from unidecode import unidecode

from django.utils.text import slugify

from randomcolor import RandomColor

RESERVED_SLUGS = {'edit', 'admin', 'lang', 'add', 'delete', 'create', 'history', 'new', 'copy', 'duplicate'}

def generate_neon_color():
    random_color = RandomColor()
    color = random_color.generate(luminosity='light', hue='pastel')[0]  # Use bright luminosity for neon-like colors
    return color[0]


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


def slugify_changelog(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.version))

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

def slugify_survey_title(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.title))
    # Check if the slug is reserved
    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"

    Klass = instance.__class__
    qs = Klass.objects.filter(slug=slug).exclude(id=instance.id)

    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_survey_title(instance, save=save, new_slug=slug)
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



def get_int_param(param, default=None):
    try:
        return int(param)
    except (TypeError, ValueError):
        return default
    
def format_bulleted_list(items):
    return "\n".join(f"• {item}" for item in items[:20])


def plural(value, unit):
    return f"{value} {unit}" + ("s" if value != 1 else "")

# --------------------
# Naming Utils
# --------------------
def int_to_roman(n: int) -> str:
    if n < 1:
        raise ValueError("Roman numerals must be >= 1")

    vals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")
    ]

    result = []
    for value, symbol in vals:
        while n >= value:
            result.append(symbol)
            n -= value

    return "".join(result)


def int_to_alpha(n):
    # Converts 1 -> a, 2 -> b, ..., 26 -> z, 27 -> aa, etc.
    result = ''
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(97 + remainder) + result  # 97 = 'a'
    return result

def int_to_alpha_upper(n):
    # Converts 1 -> A, 2 -> B, ..., 26 -> Z, 27 -> AA, etc.
    result = ''
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result  # 65 = 'A'
    return result


class NameConvention(models.TextChoices):
    NUMERIC = "numeric", "Numbered"
    GREEK = "greek", "Greek Alphabet"
    ROMAN = "roman", "Roman Numerals"
    ALPHABET = "alphabet", "English Alphabet"
    NATO = "nato", "Nato Phonetic"
    ROOT = "root", "Root Names"
    


GREEK_LABELS = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi",
    "Rho", "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega"
]

NATO_CALLSIGNS = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
    "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

ROOT_TERMS = [
    "Ambush", "Brigadier", "Companion", "Disciple", "Exile", "Foxfolk", "Gorge", "Homeland",
    "Informant", "Jailor", "Keeper", "Lookout", "Marquise", "Knave", "Outcast", "Partisan",
    "Quest", "Riverstead", "Squire", "Turmoil", "Underworld", "Vagrant", "Woodland", "Exert",
    "Yellow", "Zeal"
]


NAME_GENERATORS = {
    NameConvention.GREEK: lambda n: generate_compound_name(n, GREEK_LABELS),
    NameConvention.NATO: lambda n: generate_compound_name(n, NATO_CALLSIGNS),
    NameConvention.ROOT: lambda n: generate_compound_name(n, ROOT_TERMS, " "),
    NameConvention.ALPHABET: int_to_alpha_upper,
    NameConvention.ROMAN: int_to_roman,
}

def generate_compound_name(n: int, labels: list[str], separator: str = "-") -> str:
    if n < 1:
        raise ValueError("n must be >= 1")

    base = len(labels)
    result = []

    while n > 0:
        n -= 1  # convert to 0-based for math
        result.append(labels[n % base])
        n //= base

    return separator.join(reversed(result))


def generate_name(n: int, convention, separator: str = "-") -> str:
    if convention not in NAME_GENERATORS:
        return f'Group {n}'
    
    # Pass the separator; for others, ignore it
    if convention == NameConvention.GREEK:
        return generate_compound_name(n, GREEK_LABELS, separator)
    
    return NAME_GENERATORS[convention](n)
