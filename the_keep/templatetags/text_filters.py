import re
import bleach
from django.utils.safestring import mark_safe
from django.templatetags.static import static
from django import template
from django.urls import reverse

register = template.Library()

@register.filter
def italicize_parentheses(value):
    def replacer(match):
        return f"<em>{match.group(0)}</em>"

    # Strip all HTML from the input
    clean_value = bleach.clean(str(value), tags=[], strip=True)

    # Add <em> around parentheses
    result = re.sub(r"\([^)]*\)", replacer, clean_value)

    return mark_safe(result)


# Map of keywords to law image URLs
INLINE_IMAGES = {
    'torch': static('items/law/torch.png'),
    'tea': static('items/law/tea.png'),
    'sword': static('items/law/sword.png'),
    'bag': static('items/law/bag.png'),
    'sack': static('items/law/sack.png'),
    'hammer': static('items/law/hammer.png'),
    'crossbow': static('items/law/crossbow.png'),
    'coins': static('items/law/coin.png'),
    'coin': static('items/law/coin.png'),
    'boot': static('items/law/boot.png'),
    'hired': static('items/law/hired.png'),
    'ability': static('items/law/ability.png'),
    'daylight': static('items/law/daylight.png'),
    'birdsong': static('items/law/birdsong.png'),
    'bunny': static('items/law/bunny.png'),
    'rabbit': static('items/law/bunny.png'),
    'mouse': static('items/law/bunny.png'),
    'rat': static('items/law/rat.png'),
    'raccoon': static('items/law/raccoon.png'),
    'vb': static('items/law/raccoon.png'),
    'otter': static('items/law/otter.png'),
    'cat': static('items/law/cat.png'),
    'badger': static('items/law/badger.png'),
    'bird': static('items/law/bird.png'),
    'mole': static('items/law/mole.png'),
    'lizard': static('items/law/lizard.png'),
    'crow': static('items/law/crow.png'),
}

# This will link the official faction icons to the corresponding law pages
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
}

# This makes [[]] into SMALLCAPS () into italics and {{}} into images with optional links to faction laws
@register.filter
def format_law_text(value, lang_code='en'):
    # Step 1: Clean input by stripping all HTML
    clean_value = bleach.clean(str(value), tags=[], strip=True)

    # Step 2: Replace {{ keyword.image }} with image tag
    def image_replacer(match):
        keyword = match.group(1)
        img_url = INLINE_IMAGES.get(keyword)

        if not img_url:
            return match.group(0)

        img_tag = f'<img src="{img_url}" alt="{keyword}" class="inline-icon">'

        # If this image corresponds to a faction, wrap in a link
        if keyword in FACTION_SLUGS:
            slug = FACTION_SLUGS[keyword]
            url = reverse('law-view', kwargs={'slug': slug, 'lang_code': lang_code})
            return f'<a href="{url}">{img_tag}</a>'

        return img_tag

    clean_value = re.sub(r"\{\{\s*(\w+)\s*\}\}", image_replacer, clean_value)

    # Step 3: Wrap _text_ in <em> for italics
    def italics_replacer(match):
        return f"<em>{match.group(1)}</em>"

    clean_value = re.sub(r"_(.*?)_", italics_replacer, clean_value)


    # Step 4: Wrap **text** in span with class 'smallcaps'
    def smallcaps_replacer(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"

    final_value = re.sub(r"\*\*([^\*]+)\*\*", smallcaps_replacer, clean_value)


    # Step 5: Replace newlines with <br> for visual line breaks
    final_value = final_value.replace('\n', '<br>')

    return mark_safe(final_value)


# This makes [[]] into SMALLCAPS () into italics and {{}} into images with optional links to faction laws
@register.filter
def format_law_text_no_link(value):
    # Step 1: Clean input by stripping all HTML
    clean_value = bleach.clean(str(value), tags=[], strip=True)

    # Step 2: Replace {{ keyword.image }} with image tag
    def image_replacer(match):
        keyword = match.group(1)
        img_url = INLINE_IMAGES.get(keyword)

        if not img_url:
            return match.group(0)

        img_tag = f'<img src="{img_url}" alt="{keyword}" class="inline-icon">'

        return img_tag

    clean_value = re.sub(r"\{\{\s*(\w+)\s*\}\}", image_replacer, clean_value)

    # Step 3: Wrap _text_ in <em> for italics
    def italics_replacer(match):
        return f"<em>{match.group(1)}</em>"

    clean_value = re.sub(r"_(.*?)_", italics_replacer, clean_value)


    # Step 4: Wrap **text** in span with class 'smallcaps'
    def smallcaps_replacer(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"

    final_value = re.sub(r"\*\*([^\*]+)\*\*", smallcaps_replacer, clean_value)


    # Step 5: Replace newlines with <br> for visual line breaks
    final_value = final_value.replace('\n', '<br>')

    return mark_safe(final_value)


@register.simple_tag
def open_braces():
    return '{{'

@register.simple_tag
def close_braces():
    return '}}'

@register.filter
def ensure_punctuation(text):
    """
    Adds a period to the end of the string if it doesn't already end with 
    ., !, ?, :, ; or a closing quotation mark.
    """
    if not text:
        return ''
    
    if re.search(r'[.?!:;”\'"]$', text.strip()):
        return text
    return text + '.'

@register.filter
def emphasize_caps(value):
    """
    Wrap capital letters and punctuation in <span class="large-char">.
    Then convert the rest of the string to uppercase for a small-caps effect.
    """
    if not value:
        return ''

    clean_value = bleach.clean(str(value), tags=[], strip=True)

    def replacer(match):
        char = match.group(0)
        return f"<span class='large-char'>{char}</span>"

    wrapped = re.sub(r'([A-Z]|[“”"\'.,:;!?()\[\]])', replacer, clean_value)

    def uppercase_non_spans(text):
        parts = re.split(r'(<span.*?>.*?</span>)', text)
        return ''.join(
            part if part.startswith('<span') else part.upper()
            for part in parts
        )

    final = uppercase_non_spans(wrapped)
    return mark_safe(final)