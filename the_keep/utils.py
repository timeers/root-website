import re
import unicodedata
import os
import math
import uuid
import string

from collections import defaultdict
from PIL import Image
from itertools import combinations

from django.utils.text import Truncator
from django.utils.html import strip_tags
from django.core.exceptions import ValidationError
from django.conf import settings

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
        'fr': 'Fabrication',
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

# Saves space but not supported by TTS
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

# # PNG Version
# def resize_image_in_place(image_field, max_size=None):
#     if not image_field or not image_field.name:
#         return

#     path = image_field.path
#     if not os.path.exists(path):
#         return

#     try:
#         img = Image.open(path)

#         # Skip if already PNG and within max size
#         if img.format == "PNG" and (not max_size or (img.width <= max_size and img.height <= max_size)):
#             return  # nothing to do

#         # Resize if needed
#         if max_size and (img.width > max_size or img.height > max_size):
#             img.thumbnail((max_size, max_size), Image.LANCZOS)

#         # Normalize mode
#         if img.mode not in ("RGB", "RGBA"):
#             img = img.convert("RGBA")

#         # Save only if changes were made
#         img.save(path, format="PNG", optimize=True)

#     except Exception as e:
#         print(f"Failed to resize image {path}: {e}")

# WebP Version
def resize_image_in_place(image_field, max_size=None, quality=85):
    """
    Resize and convert image to WebP format in place.
    
    Args:
        image_field: Django ImageField
        max_size: Maximum width/height in pixels (maintains aspect ratio)
        quality: WebP quality (0-100, default 85 for good balance)
    """
    if not image_field or not image_field.name:
        return
    
    path = image_field.path
    if not os.path.exists(path):
        return
    
    try:
        img = Image.open(path)
        
        # Check if we need to do anything
        is_webp = img.format == "WEBP"
        needs_resize = max_size and (img.width > max_size or img.height > max_size)
        
        # Skip if already WebP and within max size
        if is_webp and not needs_resize:
            return  # nothing to do
        
        # Resize if needed
        if needs_resize:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        
        # Normalize mode for WebP
        # WebP supports both RGB and RGBA
        if img.mode not in ("RGB", "RGBA"):
            # Use RGBA if image has transparency, otherwise RGB
            if img.mode in ("LA", "P") and "transparency" in img.info:
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
        
        # Change extension to .webp if needed
        if not is_webp:
            base_path = os.path.splitext(path)[0]
            new_path = f"{base_path}.webp"
            
            # Save as WebP
            img.save(new_path, format="WEBP", quality=quality, method=6)
            
            # Update the image_field to point to new file
            old_name = image_field.name
            new_name = old_name.rsplit('.', 1)[0] + '.webp'
            image_field.name = new_name
            
            # Delete old file
            if os.path.exists(path) and path != new_path and not image_field.name.startswith('default_images/'):
                os.remove(path)

        else:
            # Already WebP, just save optimized version
            img.save(path, format="WEBP", quality=quality, method=6)
            
    except Exception as e:
        print(f"Failed to resize/convert image {path}: {e}")


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

def validate_png(image):
    try:
        img = Image.open(image)
        if img.format != "PNG":
            raise ValidationError("Only PNG files are allowed.")
    except Exception:
        raise ValidationError("Invalid image file.")



 
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
    
    # Replace {{...}} blocks - remove braces, capitalize first letter
    def capitalize_template(match):
        content = match.group(1).strip()  # Get content between {{ }}
        if content:
            return content[0].upper() + content[1:]
        return content
    
    text = re.sub(r'\{\{\s*(.*?)\s*\}\}', capitalize_template, text)
    
    # Replace **text** blocks - remove asterisks, capitalize content
    def capitalize_bold(match):
        content = match.group(1).strip().upper()
        return content
    
    text = re.sub(r'\*\*\s*(.*?)\s*\*\*', capitalize_bold, text)
    
    # Remove \ from markdown
    text = re.sub(r'\\([()_*[\]{}#.!\\])', r'\1', text)
    
    # Remove _ for italics (just remove the underscores, don't capitalize)
    text = text.replace('_', '')
    
    # Strip extra whitespace
    return text.strip()


# mapying from RDB to seyria
INLINE_ICON_MAP = {
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
        return INLINE_ICON_MAP.get(key, match.group(0))  # fallback to original if not found

    return re.sub(r'\{\{\s*(\w+)\s*\}\}', replacer, text or '')



def normalize_name(name):
    if not name:
        return ''
    name = unicodedata.normalize('NFKD', name)
    name = name.strip().strip(string.punctuation).lower()
    name = re.sub(r'\s+', ' ', name)
    return name





def generate_comparison_markdown(results):
    """
    Takes a dict of comparison results and returns a Markdown-formatted string
    instead of writing it to a file.
    """
    def categorize(msg):
        if "Missing expected" in msg:
            return "Missing Laws"
        elif "Unexpected" in msg:
            return "Unexpected Laws"
        elif "Out of order" in msg:
            return "Order"
        elif "Name mismatch" in msg:
            return "Name Changes"
        elif "Length mismatch" in msg:
            return "Length Mismatch"
        else:
            return "Other"

    def extract_path(msg):
        match = re.search(r"at ('.*?')", msg)
        return match.group(1) if match else "Unknown Path"


    def build_tree(msgs):
        """Builds a nested tree structure from paths in Missing/Unexpected items"""
        tree = lambda: defaultdict(tree)
        root = tree()
        for msg in msgs:
            match = re.search(r"'(.+?)'.+?at 'Law' > (.+)", msg)
            if not match:
                continue
            item_name = match.group(1)
            path = match.group(2).split(" > ")
            node = root
            for level in path:
                node = node[level]
            if "_items" in node:
                node["_items"].append(item_name)
            else:
                node["_items"] = [item_name]
        return root

    def render_tree(node, indent=0):
        """Recursively renders the nested tree as Markdown"""
        md = []
        for key, child in node.items():
            if key == "_items":
                for item in child:
                    md.append("  " * indent + f"- {item}")
            else:
                # Use headings only for top-level nodes
                if indent == 0:
                    md.append(f"### {key}")
                else:
                    md.append("  " * indent + f"- **{key}**")
                md.extend(render_tree(child, indent + 1))
        return md


    markdown_lines = ["# ⚖️ Law Comparison Report\n"]

    # Overall summary counts
    total_counts = defaultdict(int)

    for law_name, issues in results.items():
        markdown_lines.append(f"## 🧩 {law_name}\n")
        markdown_lines.append(f"**Total Issues:** {len(issues)}\n")

        # Group by category
        categorized = defaultdict(list)
        for issue in issues:
            category = categorize(issue)
            categorized[category].append(issue)
            total_counts[category] += 1

        # Render each category section
        for category, msgs in categorized.items():
            markdown_lines.append(f"### {category} ({len(msgs)})\n")

            if category in ("Missing Laws", "Unexpected Laws"):
                # Build a tree for hierarchical display
                tree = build_tree(msgs)
                markdown_lines.append("**Details:**")
                markdown_lines.extend(render_tree(tree))
            else:
                # Flat list for other categories
                for msg in msgs:
                    markdown_lines.append(f"- {msg}")

            markdown_lines.append("")  # blank line for spacing

        markdown_lines.append("\n---\n")

    # Add summary section at the top
    summary_lines = ["## 📊 Summary\n"]
    total_all = sum(total_counts.values())
    summary_lines.append(f"**Total Issues Across All Laws:** {total_all}\n")
    for cat, count in total_counts.items():
        summary_lines.append(f"- **{cat}:** {count}")
    summary_lines.append("\n---\n")

    # Insert summary after the title
    markdown_lines[1:1] = summary_lines

    return "\n".join(markdown_lines)


def get_fresh_image_url(image_field):
    """
    Returns the URL for an ImageField with a cache-busting query parameter
    based on the file's last-modified timestamp.
    """
    if not image_field:
        return None
    
    ts = 0  # default fallback
    
    try:
        # Check if file exists before trying to get modified time
        if image_field.storage.exists(image_field.name):
            ts = int(image_field.storage.get_modified_time(image_field.name).timestamp())
    except (FileNotFoundError, ValueError, AttributeError, OSError):
        # Silently fall back to ts=0
        pass
    
    try:
        return f"{image_field.url}?v={ts}"
    except (ValueError, AttributeError):
        return None



def user_can_edit(request, post=None):
    # Check to make sure the user is logged in and has a profile
    user = request.user
    if not user.is_authenticated:
        return False
    profile = getattr(user, "profile", None)
    if not profile:
        return False
    
    # Admins can always make changes
    if profile.admin:
        return True
    
    if post:
        # Only accounts with Editor status can make changes
        if profile.editor:
            if profile == post.designer:
                return True
            # Co-designers can only make changes if the post is marked as such
            if post.co_designers.filter(pk=profile.pk).exists() and post.co_designers_can_edit:
                return True

    return False

