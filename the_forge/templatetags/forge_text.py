import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from the_forge.inline_images import FORGE_INLINE_IMAGES

register = template.Library()


@register.filter
def dict_get(d, key):
    """Lookup `key` in a dict-like value. Returns empty string if missing."""
    if d is None:
        return ''
    try:
        return d.get(key, '')
    except AttributeError:
        return ''


@register.filter
def cost_choices_with(step, current):
    """Proxy to PhaseStep.cost_choices_with so templates can call it as a filter:
    `{% for v,l in action.step|cost_choices_with:action.cost %}`."""
    if step is None:
        return []
    return step.cost_choices_with(current)


@register.filter
def split(value, delimiter=","):
    """Split a string on `delimiter` (default ",") and return a list.

    Used for passing a list literal into `{% include %}` from a template — e.g.
    `{% include '...' with allowed_buttons='bold,italic'|split:',' %}`.
    """
    if not value:
        return []
    return [s.strip() for s in str(value).split(delimiter)]


@register.filter
def format_forge_text(value):
    """Render forge semi-markdown as HTML.

    Mirrors the marker set in the_forge/pdf_engine.py:format_step_markup so on-site
    rendering matches the generated PDF:
      ##text##    -> .forge-header (larger)
      ~~text~~    -> .luminari (decorative font)
      _**x**_     -> bold italic (combined form, checked before individual)
      **text**    -> real <strong> (NOT smallcaps — forge differs from law semantics)
      _text_      -> <em>
      {{ key }}   -> inline <img> using FORGE_INLINE_IMAGES map
    """
    if not value:
        return ""

    html = escape(str(value))

    def image_replacer(match):
        key = match.group(1).strip()
        url = FORGE_INLINE_IMAGES.get(key)
        if not url:
            return match.group(0)
        return f'<img src="{url}" alt="{key}" class="inline-icon">'
    html = re.sub(r"\{\{\s*(\w+)\s*\}\}", image_replacer, html)

    html = re.sub(r"##(.+?)##", r"<span class='forge-header'>\1</span>", html)
    html = re.sub(r"~~(.+?)~~", r"<span class='luminari'>\1</span>", html)

    # Boundary classes use [^A-Za-z0-9] (not \w) so that an adjacent `_` —
    # which can appear when two italic/BI spans abut after serialization —
    # doesn't suppress the match. \w treats `_` as a word char, which broke
    # parsing of `_x __**y**_` style sequences.
    html = re.sub(r"(?<![A-Za-z0-9])_\*\*(.+?)\*\*_(?![A-Za-z0-9])", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*_(.+?)_\*\*", r"<strong><em>\1</em></strong>", html)

    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<![A-Za-z0-9])_(.+?)_(?![A-Za-z0-9])", r"<em>\1</em>", html)

    html = html.replace("\n", "<br>")
    return mark_safe(html)
