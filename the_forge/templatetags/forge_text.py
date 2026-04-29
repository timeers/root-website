import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from the_forge.inline_images import FORGE_INLINE_IMAGES

register = template.Library()


@register.filter
def dict_get(d, key):
    """Lookup `key` in a dict-like value, or by integer index in a list/tuple.

    Returns empty string if missing. Used so templates can do
    `{{ mapping|dict_get:key }}` or `{{ list|dict_get:i }}`.
    """
    if d is None:
        return ''
    if isinstance(d, (list, tuple)):
        try:
            return d[int(key)]
        except (IndexError, ValueError, TypeError):
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
def padded_pipe_split(value, length):
    """Split a pipe-delimited string and pad/truncate to exactly `length` items."""
    parts = (value or '').split('|') if value else []
    return [parts[i] if i < len(parts) else '' for i in range(int(length))]


@register.filter
def divider_index_set(value):
    """Parse a comma-separated string of column indices into a set of ints."""
    out = set()
    if not value:
        return out
    for s in str(value).split(','):
        s = s.strip()
        if s.isdigit():
            out.add(int(s))
    return out


@register.filter
def make_range(value):
    """Return range(int(value)) — for `{% for i in track.num_columns|make_range %}`."""
    try:
        return range(int(value))
    except (TypeError, ValueError):
        return range(0)


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
    html = re.sub(r"\{\{\s*([\w-]+)\s*\}\}", image_replacer, html)

    html = re.sub(r"##(.+?)##", r"<span class='forge-header'>\1</span>", html)
    html = re.sub(r"~~(.+?)~~", r"<span class='luminari'>\1</span>", html)

    # Pre-pass: when two styled spans abut (no whitespace between), the
    # serializer emits sequences like `**__` / `__**` / `__` between
    # alphanumerics where one `_` is the close marker of the previous span
    # and one is the open marker of the next. Insert a ZWSP between them
    # so the regex engine sees clean boundaries; ZWSP is stripped at the
    # end so it doesn't render.
    html = re.sub(r"\*\*__", "**_\u200B_", html)
    html = re.sub(r"__\*\*", "_\u200B_**", html)
    html = re.sub(r"(?<=[A-Za-z0-9])__(?=[A-Za-z0-9])", "_\u200B_", html)

    # Boundary `(?<!_)…(?!_)` rejects only adjacent `_` (would-be ambiguous
    # abutting markers — handled by the pre-pass above) without blocking
    # alphanumeric neighbors so `oneitalictwo` with the middle word italicized
    # renders correctly.
    html = re.sub(r"(?<!_)_\*\*(.+?)\*\*_(?!_)", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*_(.+?)_\*\*", r"<strong><em>\1</em></strong>", html)

    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!_)_(.+?)_(?!_)", r"<em>\1</em>", html)

    html = html.replace("\u200B", "")
    html = html.replace("\n", "<br>")
    return mark_safe(html)
