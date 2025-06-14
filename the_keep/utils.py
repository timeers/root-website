import random
from django.utils.text import slugify, Truncator
from django.utils.html import strip_tags
from django.apps import apps
from django.core.exceptions import ValidationError
from django.conf import settings
import re
from PIL import Image
import os
import math
import uuid

from itertools import combinations



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


