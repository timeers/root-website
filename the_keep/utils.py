import random
from django.utils.text import slugify, Truncator
from django.utils.html import strip_tags
from django.apps import apps
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import transaction
import re
from PIL import Image
import os
import math
import uuid
import string

from itertools import combinations

class NoPrimeLawError(Exception):
    pass

FACTION_SLUGS = {
    'bunny': 'woodland-alliance',
    'rabbit': 'woodland-alliance',
    'mouse': 'woodland-alliance',
    'rat': 'lord-of-the-hundreds',
    'raccoon': 'vagabond',
    'vb': 'vagabond',
    'otter': 'riverfolk-company',
    'cat': 'marquise-de-cat',
    'badger': 'keepers-in-iron',
    'bird': 'eyrie-dynasties',
    'mole': 'underground-duchy',
    'lizard': 'lizard-cult',
    'crow': 'corvid-conspiracy',
    'frog': 'lilypad-diaspora',
    'bat': 'twilight-council',
    'skunk': 'knaves-of-the-deepwood',
}


def resize_image(image_field, max_size):
    """Helper function to resize the image if necessary."""
    try:
        if image_field and os.path.exists(image_field.path):  # Check if the image exists
            img = Image.open(image_field.path)

            # Resize if the image is larger than the max_size
            if img.height > max_size or img.width > max_size:
                # Calculate the new size while maintaining the aspect ratio
                if img.width > img.height:
                    ratio = max_size / img.width
                    new_size = (max_size, int(img.height * ratio))
                else:
                    ratio = max_size / img.height
                    new_size = (int(img.width * ratio), max_size)

                # Resize image and save
                img = img.resize(new_size, Image.LANCZOS)
                img.save(image_field.path)
                print(f'Resized image saved at: {image_field.path}')
            else:
                print(f'Original image saved at: {image_field.path}')
        
    except Exception as e:
        print(f"Error resizing image: {e}")


# def resize_image_to_webp(image_field, max_size, instance=None, field_name=None):
#     """Resize and convert the image to WebP format if needed."""
          
#     print(image_field)
#     try:
#         if not image_field or not os.path.exists(image_field.path):
#             print("Image field is empty or file does not exist.")
#             return

#         original_path = image_field.path
#         file_ext = os.path.splitext(original_path)[1].lower()

#         # Open image
#         img = Image.open(original_path)

#         # Skip if already WebP and smaller than or equal to max size
#         if file_ext == '.webp' and img.width <= max_size and img.height <= max_size:
#             print("Image is already WebP and within max size — skipping.")
#             return

#         # Resize if too large
#         if img.width > max_size or img.height > max_size:
#             if img.width > img.height:
#                 ratio = max_size / img.width
#                 new_size = (max_size, int(img.height * ratio))
#             else:
#                 ratio = max_size / img.height
#                 new_size = (int(img.width * ratio), max_size)

#             img = img.resize(new_size, Image.LANCZOS)
#             print(f"Image resized to: {new_size}")
#         else:
#             print("Image is already within size limits — skipping resize.")

#         # Check if the image has an alpha channel
#         if img.mode in ("RGBA", "LA"):
#             img = img.convert("RGBA")  # Keep transparency
#         else:
#             img = img.convert("RGB")  # No transparency needed

#         # Save new WebP version
#         base, _ = os.path.splitext(original_path)
#         # Save as WebP
#         new_path = base + ".webp"
#         img.save(new_path, 'WEBP', quality=80)

#         # Remove the original file
#         if file_ext != '.webp':
#             delete_old_image(image_field)
#             # os.remove(original_path)

#         # 🧠 Update the field with the new path (if instance and field name provided)
#         if instance and field_name:
#             relative_path = os.path.relpath(new_path, settings.MEDIA_ROOT)
#             getattr(instance, field_name).name = relative_path
#             instance.save(update_fields=[field_name])

#     except Exception as e:
#         print(f"Error resizing image: {e}")


def resize_image_to_webp(image_field, max_size=None):
    """
    Resize and convert an image to WebP if needed.
    Returns the relative path to the new file if converted, or None if unchanged.
    """
    try:
        if not image_field or not os.path.exists(image_field.path):
            print("Image field is empty or file does not exist.")
            return None

        original_path = image_field.path
        file_ext = os.path.splitext(original_path)[1].lower()

        img = Image.open(original_path)

        # Skip if already WebP and small enough
        if file_ext == '.webp' and img.width <= max_size and img.height <= max_size:
            print("Image is already WebP and within max size — skipping.")
            return None

        # Resize if too large
        if max_size:
            if img.width > max_size or img.height > max_size:
                if img.width > img.height:
                    ratio = max_size / img.width
                    new_size = (max_size, int(img.height * ratio))
                else:
                    ratio = max_size / img.height
                    new_size = (int(img.width * ratio), max_size)

                img = img.resize(new_size, Image.LANCZOS)
                print(f"Image resized to: {new_size}")
            else:
                print("Image is within size limits — no resize needed.")
        else:
            print('No max size')

        # Convert image mode
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")

        # Determine the target directory
        original_dir = os.path.dirname(original_path)
        unique_filename = f"{uuid.uuid4().hex}.webp"

        # Redirect if in 'default_images' folder
        if 'default_images' in original_path:
            target_dir = original_dir.replace('default_images', 'component_pictures')
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = original_dir

        new_path = os.path.join(target_dir, unique_filename)


        # Save image
        img.save(new_path, format='WEBP', quality=80)
        print(f"Saved WebP image: {new_path}")

        # Delete original if it wasn’t already WebP
        if file_ext != '.webp' and os.path.exists(original_path):
            delete_old_image(image_field)
            # os.remove(original_path)

        # Return path relative to MEDIA_ROOT
        return os.path.relpath(new_path, settings.MEDIA_ROOT)

    except Exception as e:
        print(f"Error resizing image: {e}")
        return None



def delete_old_image(old_image):
    """Helper method to delete old image if it exists."""
    if old_image:
        if not old_image.name.startswith('default_images/'):
            if old_image and os.path.exists(old_image.path):
                os.remove(old_image.path)
                print(f"Old image deleted: {old_image}")
        else:
            print(f"Default image saved: {old_image}")

def validate_hex_color(value):
    # Regular expression to check for valid hex color codes (e.g., #RRGGBB)
    if not re.match(r'^#([0-9A-Fa-f]{6})$', value):
        raise ValidationError(f"{value} is not a valid hex color code.")


def slugify_post_title(instance, save=False, new_slug=None):
    RESERVED_SLUGS = {'edit', 'lang', 'add'}
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.title)
    
    # Check if the slug is reserved
    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"

    Post = apps.get_model('the_keep', 'Post')
    qs = Post.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_post_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance



def slugify_expansion_title(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.title)

    Expansion = apps.get_model('the_keep', 'Expansion')
    qs = Expansion.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_expansion_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance


def slugify_law_group_title(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    elif instance.post:
        slug = instance.post.slug
    else:
        slug = slugify(instance.title)

    LawGroup = apps.get_model('the_keep', 'LawGroup')
    qs = LawGroup.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_law_group_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance

 
def color_distance(rgb1, rgb2):
    """
    Calculate the Euclidean distance between two RGB colors.
    """
    return math.sqrt(
        (rgb1[0] - rgb2[0]) ** 2 +
        (rgb1[1] - rgb2[1]) ** 2 +
        (rgb1[2] - rgb2[2]) ** 2
    )

def hex_to_rgb(hex_code):
    # Remove the '#' if present and convert hex to RGB
    hex_code = hex_code.lstrip('#')
    return tuple(int(hex_code[i:i+2], 16) for i in (0, 2, 4))
 
def rgb_to_hex(r, g, b):
    # Convert RGB back to hex format
    return f'#{r:02x}{g:02x}{b:02x}'
 
def complementary_color(hex_code):
    # Convert hex to RGB
    r, g, b = hex_to_rgb(hex_code)
   
    # Calculate the complementary color
    r_complement = 255 - r
    g_complement = 255 - g
    b_complement = 255 - b
   
    # Convert back to hex and return
    return rgb_to_hex(r_complement, g_complement, b_complement)


def rgb_to_color_name(rgb):
    """
    Maps an RGB tuple to a corresponding color name, including purple, violet, lavender, and blue shades.
    
    Args:
    - rgb: A tuple (R, G, B), where R, G, and B are integers between 0 and 255.
    
    Returns:
    - The name of the color corresponding to the given RGB value.
    """
    r, g, b = rgb

    # Check if the color is grayscale (R == G == B)
    if r == g == b:
        if r > 200:
            return "White"
        elif r < 50:
            return "Black"
        else:
            return "Gray"
    
    # Determine color family (hue)
    if r > g and r > b:
        color_family = "Red"
    elif g > r and g > b:
        color_family = "Green"
    elif b > r and b > g:
        color_family = "Blue"
    elif r == g and r > b:
        color_family = "Yellow"  # Yellow is a mix of red and green
    elif g == b and g > r:
        color_family = "Cyan"  # Cyan is a mix of green and blue
    elif r == b and r > g:
        color_family = "Magenta"  # Magenta is a mix of red and blue
    else:
        color_family = "Unknown"
    
    # Handle purple shades (mix of red and blue, low green)
    if r > 100 and b > 100 and g < 100:
        if r > 150 and b > 150:
            return "Purple"  # Strong purple
        elif r < 150 and b < 150:
            return "Lavender"  # Lighter purple
        return "Violet"  # A more reddish purple

    # Check for light or dark blue more specifically
    if color_family == "Blue":
        brightness = (r + g + b) / 3
        if brightness > 180:  # High brightness, likely a light shade
            return "Light Blue"
        elif brightness < 80:  # Low brightness, likely a dark shade
            return "Dark Blue"
        else:  # Otherwise, normal blue
            return "Blue"

    # Handle green shades more specifically
    if color_family == "Green":
        brightness = (r + g + b) / 3  # Using average brightness
        
        # Light green logic: if the green channel is dominant and the brightness is high
        if g > r and g > b and brightness > 180:
            return "Light Green"
        
        # Dark green logic: if the brightness is low
        elif brightness < 80:
            return "Dark Green"
        
        # Otherwise, normal green
        return "Green"
    
    # Brightness and Saturation for other color families
    brightness = (r + g + b) / 3  # Simple average to represent brightness
    if brightness > 200:
        brightness_level = "Light"
    elif brightness < 80:
        brightness_level = "Dark"
    else:
        brightness_level = "Medium"

    # Map to more specific names based on family and brightness
    if color_family == "Red":
        if b < 100 and g < 100:
            return f"Light {color_family}"
        if b > 100 and g > 100:
            return f"Pink"
        return f"Dark {color_family}"
    elif color_family == "Yellow":
        return f"Yellow"
    elif color_family == "Cyan":
        return f"Cyan"
    elif color_family == "Magenta":
        return f"Magenta"
    
    # Return based on brightness and family
    return f"{brightness_level} {color_family}"



DEFAULT_TITLES_TRANSLATIONS = {
    'Overview': {
        'en': 'Overview',
        'ru': 'Краткое описание',
        'es': 'Resumen',
        'nl': 'Overzicht',
        'pl': 'Omówienie',
        'fr': 'Aperçu',
    },
    'Faction Rules and Abilities': {
        'en': 'Faction Rules and Abilities',
        'ru': 'Правила и способности фракции',
        'es': 'Reglas y Habilidades de Facción',
        'nl': 'Factieregels en Vaardigheden',
        'pl': 'Zasady Frakcji i jej Zdolności',
        'fr': 'Règles et Capacités de Faction',
    },
    'Faction Setup': {
        'en': 'Faction Setup',
        'ru': 'Подготовка к игре',
        'es': 'Preparación Individual',
        'nl': 'Factie Voorbereiding',
        'pl': 'Przygotowanie Frakcji',
        'fr': 'Mise en place de Faction',
    },
    'Birdsong': {
        'en': 'Birdsong',
        'ru': 'Утро',
        'es': 'Alba',
        'nl': 'Vogelzang',
        'pl': 'Świt',
        'fr': 'Aurore',
    },
    'Daylight': {
        'en': 'Daylight',
        'ru': 'День',
        'es': 'Día',
        'nl': 'Daglicht',
        'pl': 'Dzień',
        'fr': 'Jour',
    },
    'Evening': {
        'en': 'Evening',
        'ru': 'Вечер',
        'es': 'Noche',
        'nl': 'Avond',
        'pl': 'Wieczór',
        'fr': 'Crépuscule',
    },
    'Crafting': {
        'en': 'Crafting',
        'ru': 'Ремесло',
        'es': 'Fabricar',
        'nl': 'Vervaardigen',
        'pl': 'Przekuwanie',
        'fr': 'Artisanat',
    },
    'Setup Modifications': {
        'en': 'Setup Modifications',
        'ru': 'Изменения конфигурации',
        'es': 'Cambios en la preparación',
        'nl': 'Configuratiewijzigingen',
        'pl': 'Zmiany konfiguracji',
        'fr': 'Modifications de configuration',
    },
    'Starting Items': {
        'en': 'Starting Items',
        'ru': 'Начальные предметы',
        'es': 'Objetos iniciales',
        'nl': 'Startvoorwerpen',
        'pl': 'Przedmioty Startowe',
        'fr': 'Éléments de départ',
    },
    'Draw and Discard': {
        'en': 'Draw and Discard',
        'ru': 'Добор и сброс карт',
        'es': 'Robar y descartar',
        'nl': 'Trek en Leg Af',
        'pl': 'Dobranie i Odrzucenie Kart',
        'fr': 'Piocher et défausser',
    },
    'Build': {
        'en': 'Build',
        'ru': 'Строительство',
        'es': 'Construir',
        'nl': 'Bouw.',
        'pl': 'Budowa',
        'fr': 'Construire',
    },
    'Recruit': {
        'en': 'Recruit',
        'ru': 'Вербовка',
        'es': 'Reclutar',
        'nl': 'Rekruteer',
        'pl': 'Werbunek',
        'fr': 'Recruter',
    },
    'Move': {
        'en': 'Move',
        'ru': 'Перемещение',
        'es': 'Mover',
        'nl': 'Verplaats',
        'pl': 'Ruch',
        'fr': 'Déplacer',
    },
    'Battle': {
        'en': 'Battle',
        'ru': 'Сражение',
        'es': 'Batallar',
        'nl': 'Vecht',
        'pl': 'Walka',
        'fr': 'Batailler',
    },


}
def get_translated_title(key, target_lang_code):
    translations = DEFAULT_TITLES_TRANSLATIONS.get(key)
    if translations:
        return translations.get(target_lang_code, key)  # fallback to English key
    return key





def clean_meta_description(raw_text, max_length=250):
    text = strip_tags(raw_text or "")
    text = re.sub(r'{{|}}|\[\[|\]\]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return Truncator(text).chars(max_length, truncate='...')





SMALL_WORDS = {
    'a', 'an', 'and', 'the', 'of', 'in', 'on', 'to', 'with', 'at', 'by', 'for',
    'el', 'la', 'los', 'las', 'de', 'del', 'y', 'en', 'con', 'por', 'para',
    'le', 'les', 'des', 'du', 'et', 'dans', 'avec', 'pour', 'par',
    'de', 'het', 'een', 'en', 'van', 'tot', 'onder',
    'i', 'w', 'z', 'na', 'do', 'od', 'za', 'po', 'dla', 'o', 'u',
    'и', 'в', 'во', 'на', 'с', 'по', 'от', 'до', 'о', 'об', 'при'
}

def generate_abbreviation_choices(title, abbreviation):
    words = re.findall(r"\b[\w']+\b", title)
    words = [w for w in words if w]

    basic = ''.join(w[0] for w in words).upper()
    no_smalls = [w for w in words if w.lower() not in SMALL_WORDS]
    compact = ''.join(w[0] for w in no_smalls).upper()

    variants = set()
    variants.add(basic)
    variants.add(compact)

    for r in range(2, min(4, len(no_smalls) + 1)):
        for combo in combinations(no_smalls, r):
            variants.add(''.join(w[0] for w in combo).upper())

    # Only keep abbreviations with 4 or fewer characters
    filtered = [abbr for abbr in variants if len(abbr) <= 4]

    # Ensure abbreviation is included (if short enough)
    abbr_clean = abbreviation.strip().upper()
    if abbr_clean and len(abbr_clean) <= 4 and abbr_clean not in filtered:
        filtered.append(abbr_clean)

    # Ensure uniqueness and return sorted list
    return sorted(filtered) or ['1']


def strip_formatting(text):
    if not text:
        return ''
    
    # Remove {{...}} blocks (like {{ keyword.image }})
    text = re.sub(r'\{\{.*?\}\}', '', text)
    
    # Remove ** for small caps and _ for italics
    text = text.replace('**', '').replace('_', '')
    
    # Strip extra whitespace
    return text.strip()


def serialize_group(prime_law):
    Law = apps.get_model('the_keep', 'Law')
    laws = Law.objects.filter(group=prime_law.group, language=prime_law.language).select_related('parent').prefetch_related('children')
    if prime_law.group.post:
        law_color = prime_law.group.post.color
    else:
        law_color = '#000000'
    if not prime_law:
        raise NoPrimeLawError("No prime law found.")

    # Only add pretext if description exists
    if prime_law.description:
        text = replace_placeholders(prime_law.description.strip())
        references = ''
        if prime_law.reference_laws:
            for reference in prime_law.reference_laws.all():
                if reference.group.type == "Official":
                    references += f"(`rule:{reference.get_law_index()}`)"
                else:
                    references += f"({reference})"
        output = [{
            'name': prime_law.title.strip(),
            'color': law_color,
            'pretext': text + references,
            'id': prime_law.id,
            'children': []
        }]
    else:
        output = [{
            'name': prime_law.title.strip(),
            'color': law_color,
            'id': prime_law.id,
            'children': []
        }]

    top_level_laws = laws.filter(parent__isnull=True, prime_law=False).order_by('position')
    for law in top_level_laws:
        output[0]['children'].append(serialize_law(law))
    return output



def serialize_law(law):
    entry = {
        'name': replace_placeholders(law.title.strip())
    }

    if law.plain_title and law.plain_title.strip() != law.title.strip():
        entry['plainName'] = replace_placeholders(law.plain_title.strip())

    if law.description:
        text = replace_placeholders(law.description.strip())

        references = ''
        if law.reference_laws:
            for reference in law.reference_laws.all():
                if reference.group.type == "Official":
                    references += f"(`rule:{reference.get_law_index()}`)"
                else:
                    references += f"({reference})"
        if law.level == 0:
            entry['pretext'] = text + references
        else:
            entry['text'] = text + references

    entry['id'] = law.id

    children = law.children.all().order_by('position')
    if children.exists():
        if law.level == 0:
            entry['children'] = [serialize_law(child) for child in children]
        else:
            entry['subchildren'] = [serialize_law(child) for child in children]

    return entry
# mapying from RDB to seyria
INLINE_MAP = {
    'torch': '`item:torch`',
    'tea': '`item:tea`',
    'sword': '`item:sword`',
    'bag': '`item:sack`',
    'sack': '`item:sack`',
    'hammer': '`item:hammer`',
    'crossbow': '`item:crossbow`',
    'coins': '`item:coin`',
    'coin': '`item:coin`',
    'boot': '`item:boot`',
    'hired': '`hireling:whenhired`',
    'ability': '`hireling:ability`',
    'daylight': '`hireling:daylight`',
    'birdsong': '`hireling:birdsong`',
    'cat': '`faction:marquise:6.1`',
    'bird': '`faction:eyrie:7.1`',
    'bunny': '`faction:woodland:8.1`',
    'rabbit': '`faction:woodland:8.1`',
    'mouse': '`faction:woodland:8.1`',
    'raccoon': '`faction:vagabond:9.1`',
    'vb': '`faction:vagabond:9.1`',
    'lizard': '`faction:cult:10.1`',
    'otter': '`faction:riverfolk:11.1`',
    'mole': '`faction:duchy:12.1`',
    'crow': '`faction:corvid:13.1`',
    'rat': '`faction:warlord:14.1`',
    'badger': '`faction:keepers:15.1`',
    'frog': '`faction:diaspora:16.1`',
    'bat': '`faction:council:17.1`',
    'skunk': '`faction:knaves:18.1`',
}
# Replace RDB bracket tags with seyria format
def replace_placeholders(text):
    def replacer(match):
        key = match.group(1).strip()
        return INLINE_MAP.get(key, match.group(0))  # fallback to original if not found

    return re.sub(r'\{\{\s*(\w+)\s*\}\}', replacer, text or '')

def normalize_name(name):
    if not name:
        return ''
    return name.strip().rstrip(string.punctuation).lower()

# WIP for comparing an uploaded yaml law file with the current law
def compare_structure_strict(generated, uploaded, path="'Law'"):
    mismatches = []

    if type(generated) != type(uploaded):
        mismatches.append(f"Type mismatch at {path}: {type(generated).__name__} vs {type(uploaded).__name__}")
        return mismatches

    if isinstance(generated, list):
        if len(generated) != len(uploaded):
            mismatches.append(f"Length mismatch at {path}: {len(generated)} vs {len(uploaded)}")

        gen_names = [normalize_name(item.get('name')) for item in generated]
        up_names = [normalize_name(item.get('name')) for item in uploaded]


        for name in up_names:
            if name not in gen_names:
                mismatches.append(f"Unexpected item '{name}' found in uploaded at {path}")

        for i, (gen_item, up_item) in enumerate(zip(generated, uploaded)):
            expected_name = gen_item.get('name')
            actual_name = up_item.get('name')
            # new_path = f"{path}[{i}] ('{expected_name}')"
            new_path = f"{path} > '{expected_name}'"

            if expected_name != actual_name:
                if actual_name in gen_names:
                    correct_index = gen_names.index(actual_name)
                    mismatches.append(
                        f"Order mismatch at {new_path}: expected '{expected_name}', got '{actual_name}' "
                        f"(found at index {correct_index})"
                    )
                else:
                    mismatches.append(
                        f"Name mismatch at {new_path}: expected '{expected_name}', got '{actual_name}'"
                    )

            mismatches += compare_structure_strict(gen_item, up_item, new_path)

    elif isinstance(generated, dict):
        for key in ('children', 'subchildren'):
            gen_child = generated.get(key, [])
            up_child = uploaded.get(key, [])
            if gen_child or up_child:
                child_path = f"{path}"
                mismatches += compare_structure_strict(gen_child, up_child, child_path)

    return mismatches





def update_laws_by_structure(generated_data, uploaded_data, lang_code):
    Law = apps.get_model('the_keep', 'Law')
    @transaction.atomic
    def recursive_update(generated, uploaded):
        for gen_item, up_item in zip(generated, uploaded):
            law_id = gen_item.get("id")
            if law_id is None:
                continue  # Skip if no ID found
            try:
                law = Law.objects.get(id=law_id)
            except Law.DoesNotExist:
                continue  # Optionally log or collect for reporting

            # Use uploaded text or pretext to update description
            new_desc = up_item.get("text") or up_item.get("pretext")
            new_title, _ = replace_special_references(up_item.get("name"), lang_code)
            reference_laws = None
            law.title = new_title
            if new_desc:
                description, reference_laws = replace_special_references(new_desc.strip(), lang_code)
                law.description = description
            law.save()
            if reference_laws:
                law.reference_laws.set(reference_laws)  # This replaces all existing references
            else:
                law.reference_laws.clear()  # If no references found, clear the field

            # Recurse through children/subchildren
            for key in ("children", "subchildren"):
                if key in gen_item or key in up_item:
                    gen_children = gen_item.get(key, [])
                    up_children = up_item.get(key, [])
                    recursive_update(gen_children, up_children)

    # Run the update inside an atomic block
    recursive_update(generated_data, uploaded_data)

# maping from seyria to rdb {{ }} format
REFERENCE_MAP = {
    'whenhired': 'hired',
    'ability': 'ability',
    'daylight': 'daylight',
    'birdsong': 'birdsong',
    'marquise': 'cat',
    'eyrie': 'bird',
    'woodland': 'bunny',
    'vagabond': 'vb',
    'cult': 'lizard',
    'riverfolk': 'otter',
    'duchy': 'mole',
    'corvid': 'crow',
    'warlord': 'rat',
    'keepers': 'badger',
    'diaspora': 'frog',
    'council': 'bat',
    'knaves': 'skunk',

}

def replace_special_references(text, lang_code):
    reference_laws = set()
    Law = apps.get_model('the_keep', 'Law')
    LawGroup = apps.get_model('the_keep', 'LawGroup')

    def find_law_by_rule_index(rule_index_str, lang_code, second_pass=False):

        try:
            group_idx_str, law_idx_str = rule_index_str.split('.', 1)
            group_idx = int(group_idx_str)

            if law_idx_str == "0":
                law_idx_str = ""

        except ValueError:
            return None

        all_groups = list(LawGroup.objects.all())
        if group_idx - 1 >= len(all_groups) or group_idx <= 0:
            return None

        group = all_groups[group_idx - 1]
        law = Law.objects.filter(group=group, law_index=law_idx_str, language__code=lang_code).first()

        # if not second_pass:
        #     # Get the last LawGroup that is Official and has a post
        #     last_official_group = (
        #         LawGroup.objects
        #         .filter(type='Official', post__isnull=False)
        #         .last()
        #     )

        #     if last_official_group:
        #         # Get index in full list (1-based)
        #         try:
        #             last_index = all_groups.index(last_official_group) + 1
        #             if last_index < group_idx:

        #                 print(f'{group_idx} larger than last post group')
        #             print(f"Last official LawGroup with post is at index {last_index}")
        #         except ValueError:
        #             print("Last qualified LawGroup not found in full list.")
        #     else:
        #         print("No Official LawGroup with post found.")

        return law

    # def rule_replacer(match):
    #     rule_content = match.group(1)
    #     law = find_law_by_rule_index(rule_content, lang_code)
    #     if law:
    #         reference_laws.add(law)
    #         return ""  # Remove the (`rule:x`) reference entirely
    #     else:
    #         return f"({rule_content})"  # Replace with a fallback if no law found

    def rule_replacer(match):
        full_match = match.group(0)
        rule_content = match.group(1)

        print(f"Rule replacer {match}")

        law = find_law_by_rule_index(rule_content, lang_code)
        if law:
            reference_laws.add(law)

            # Check if there's a space before the match — remove it later
            if full_match.startswith(" (") or full_match.startswith(" (`rule:"):
                return ""  # Remove the whole match and let re.sub() handle space collapse
            return ""
        else:
            return f"({rule_content})"

    def backtick_replacer(match):
        content = match.group(1)

        # Handle rule: still if present and not already matched in previous pass
        if content.startswith("rule:"):
            print(f"Backtick replacer {content}")
            rule_content = content[5:]
            law = find_law_by_rule_index(rule_content, lang_code)
            if law:
                rule_content=law.law_code
                reference_laws.add(law)
                return f"{rule_content}"  # Remove `rule:x`
            else:
                return f"{rule_content}"

        # map faction references
        if content.startswith("faction:"):
            parts = content.split(":")
            if len(parts) >= 2:
                key = parts[1]
                if key in REFERENCE_MAP:
                    return f"{{{{{REFERENCE_MAP[key]}}}}}"

        # map hireling references
        elif content.startswith("hireling:"):
            parts = content.split(":")
            if len(parts) == 2:
                key = parts[1]
                if key in REFERENCE_MAP:
                    return f"{{{{{REFERENCE_MAP[key]}}}}}"

        # Replace `item:x` with {{x}}
        elif content.startswith("item:"):
            parts = content.split(":")
            if len(parts) == 2:
                key = parts[1]
                return f"{{{{{key}}}}}"

        return match.group(0)  # leave unchanged

    # First handle (`rule:x`) with optional leading space
    # text = re.sub(r'\(`rule:([^\)]+)`\)', rule_replacer, text)
    # text = re.sub(r'\s?\(`rule:([^\)]+)`\)', rule_replacer, text)

    # Then handle remaining `...` references
    text = re.sub(r'`([^`]+)`', backtick_replacer, text)

    return text, list(reference_laws)

def create_laws_from_yaml(group, language, yaml_data):
    Law = apps.get_model('the_keep', 'Law')
    lang_code = language.code

    def create_law(entry, lang_code, parent=None, position=0, is_prime=False):
        # Prefer 'pretext' if present, otherwise use 'text'
        raw_description = entry.get('pretext') or entry.get('text', '')
        if raw_description:
            description, reference_laws = replace_special_references(raw_description, lang_code=lang_code)
        else:
            description, reference_laws = '', []        
        raw_title = entry['name']
        title, _ = replace_special_references(raw_title, lang_code=lang_code)
        if parent and parent.prime_law:
            parent=None

        law = Law.objects.create(
            title=title,
            group=group,
            language=language,
            parent=parent,
            position=position,
            prime_law=is_prime,
            description=description
        )
        if reference_laws:
            law.reference_laws.set(reference_laws)  # This replaces all existing references
        else:
            law.reference_laws.clear()
        # Handle both 'children' and 'subchildren'
        for key in ('children', 'subchildren'):
            for i, child in enumerate(entry.get(key, [])):
                create_law(child, lang_code, parent=law, position=i)

    for i, entry in enumerate(yaml_data):
        is_prime = i == 0  # Treat first item as prime law
        create_law(entry, lang_code, parent=None, position=i, is_prime=is_prime)
