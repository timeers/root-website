import re
import bleach
import markdown
from django.utils.safestring import mark_safe
from django.templatetags.static import static
from django import template
from django.urls import reverse
from the_keep.utils import FACTION_SLUGS

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
    'sack': static('items/law/bag.png'),
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
    'frog': static('items/law/frog.png'),
    'bat': static('items/law/bat.png'),
    'skunk': static('items/law/skunk.png'),
}

# Only allowing table related tags and the three I need so that users don't go crazy
ALLOWED_TAGS = [
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'em', 'strong', 'p'
]

ALLOWED_ATTRIBUTES = {
    # No special attributes needed for basic table rendering
}

# # This makes [[]] into SMALLCAPS () into italics and {{}} into images with links to faction laws
@register.filter
def format_law_text(value, language_code='en'):
    if not value:
        return ""

    # Step 1: Convert markdown to HTML (supporting tables)
    markdown_html = markdown.markdown(str(value), extensions=['tables'])

    # Step 2: Sanitize the markdown HTML
    clean_html = bleach.clean(markdown_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)

    # Step 3: Replace {{ keyword }} with <img> tags and optional faction links
    def image_replacer(match):
        keyword = match.group(1)
        img_url = INLINE_IMAGES.get(keyword)
        if not img_url:
            return match.group(0)

        img_tag = f'<img src="{img_url}" alt="{keyword}" class="inline-icon">'
        if keyword in FACTION_SLUGS:
            slug = FACTION_SLUGS[keyword]
            url = reverse('law-view', kwargs={'slug': slug, 'language_code': language_code})
            return f'<a href="{url}">{img_tag}</a>'
        return img_tag

    clean_html = re.sub(r"\{\{\s*(\w+)\s*\}\}", image_replacer, clean_html)

    # Step 4: Replace _text_ with <em>
    def italics_replacer(match):
        return f"<em>{match.group(1)}</em>"
    clean_html = re.sub(r"_(.*?)_", italics_replacer, clean_html)

    # Step 5: Replace <strong> with <span class='smallcaps'>
    def strong_to_smallcaps(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"
    clean_html = re.sub(r"<strong>(.*?)</strong>", strong_to_smallcaps, clean_html, flags=re.IGNORECASE)

    # Step 6: Replace **TEXT** with <span class='smallcaps'> as a backup
    def smallcaps_replacer(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"
    clean_html = re.sub(r"\*\*([^\*]+)\*\*", smallcaps_replacer, clean_html)

    # Step 7: Remove all <p> and </p> tags
    clean_html = re.sub(r'</?p>', '', clean_html, flags=re.IGNORECASE)

    return mark_safe(clean_html)



# This makes [[]] into SMALLCAPS () into italics and {{}} into images without links to faction laws
@register.filter
def format_law_text_no_link(value):
    if not value:
        return ""

    # Step 1: Convert markdown to HTML (supporting tables)
    markdown_html = markdown.markdown(str(value), extensions=['tables'])

    # Step 2: Sanitize the markdown HTML (allowing necessary tags and attributes)
    clean_html = bleach.clean(markdown_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)

    # Step 3: Replace {{ keyword }} with <img> tags (no links here)
    def image_replacer(match):
        keyword = match.group(1)
        img_url = INLINE_IMAGES.get(keyword)

        if not img_url:
            return match.group(0)

        return f'<img src="{img_url}" alt="{keyword}" class="inline-icon">'

    clean_html = re.sub(r"\{\{\s*(\w+)\s*\}\}", image_replacer, clean_html)

    # Step 4: Replace _text_ with <em>
    def italics_replacer(match):
        return f"<em>{match.group(1)}</em>"
    clean_html = re.sub(r"_(.*?)_", italics_replacer, clean_html)

    # Step 5: Replace <strong>TEXT</strong> with smallcaps span
    def strong_to_smallcaps(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"
    clean_html = re.sub(r"<strong>(.*?)</strong>", strong_to_smallcaps, clean_html, flags=re.IGNORECASE)

    # Step 6: Replace **TEXT** with smallcaps span (in case markdown didn't convert)
    def smallcaps_replacer(match):
        text = match.group(1).upper()
        return f"<span class='smallcaps'>{text}</span>"
    clean_html = re.sub(r"\*\*([^\*]+)\*\*", smallcaps_replacer, clean_html)

    # Step 7: Remove <p> tags
    clean_html = re.sub(r'</?p>', '', clean_html, flags=re.IGNORECASE)

    return mark_safe(clean_html)



# Converts law text to text only for meta descriptions.
@register.filter
def format_law_text_only(value):
    
    # Step 1: Strip all HTML
    clean_value = bleach.clean(str(value), tags=[], strip=True)

    # Step 2: Remove complete {{ ... }} patterns
    clean_value = re.sub(r"\{\{\s*[^}]+\s*\}\}", "", clean_value)

    # Step 3: Remove underscores
    clean_value = clean_value.replace("_", "")

    # Step 4: Convert **text** to UPPERCASE
    clean_value = re.sub(r"\*\*([^\*]+)\*\*", lambda m: m.group(1).upper(), clean_value)

    # Step 5: Handle unclosed **text ... to end of line
    clean_value = re.sub(r"\*\*([^\n<]*)", lambda m: m.group(1).upper(), clean_value)

    # Step 6: Remove everything from leftover '{{' to the end of the string
    clean_value = re.sub(r"\{\{.*", "", clean_value)

    # Step 7: Replace newlines and collapse excess whitespace
    final_value = clean_value.replace('\n', ' ')

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