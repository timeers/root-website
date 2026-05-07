import re
from html.parser import HTMLParser

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from the_forge.inline_images import FORGE_INLINE_IMAGES, sheet_inline_images

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
def json_list_attr(value):
    """JSON-encode a list/tuple for embedding in an HTML attribute. Django's
    template auto-escape will then HTML-escape the resulting string.
    """
    import json
    if not value:
        return '[]'
    if not isinstance(value, (list, tuple)):
        return '[]'
    return json.dumps(list(value))


@register.filter
def make_range(value):
    """Return range(int(value)) — for `{% for i in track.num_columns|make_range %}`."""
    try:
        return range(int(value))
    except (TypeError, ValueError):
        return range(0)


_FORGE_TAG_MAP = {
    'strong': ('<strong>', '</strong>'),
    'b':      ('<strong>', '</strong>'),
    'em':     ('<em>', '</em>'),
    'i':      ('<em>', '</em>'),
}


class _ForgeHtmlSanitizer(HTMLParser):
    """Sanitize forge rich-text HTML against a strict allowlist.

    Allowlist:
      <strong>/<b>                   -> <strong>
      <em>/<i>                       -> <em>
      <span data-forge="header">     -> <span class="forge-header">
      <span data-forge="luminari">   -> <span class="luminari">
      <img data-forge-image="KEY">   -> <img src=...> resolved from
                                        FORGE_INLINE_IMAGES (dropped if KEY
                                        unknown)
      <br>                           -> <br>
      Text                           -> HTML-escaped passthrough

    Anything else (attributes, tags, comments) is dropped silently.
    """

    def __init__(self, sheet=None):
        super().__init__(convert_charrefs=True)
        self._out = []
        self._inline_map = sheet_inline_images(sheet)
        # Stack of close strings to emit when matching tags close. None
        # entries mark dropped open tags so we can pair them with their
        # close events without emitting anything.
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        tag = tag.lower()
        if tag == 'br':
            self._out.append('<br>')
            return
        if tag == 'img':
            key = attr_map.get('data-forge-image')
            if not key:
                return
            url = self._inline_map.get(key)
            if not url:
                return
            self._out.append(
                f'<img src="{escape(url)}" alt="{escape(key)}" class="inline-icon">'
            )
            return
        if tag == 'span':
            forge = attr_map.get('data-forge')
            if forge == 'header':
                self._out.append("<span class='forge-header'>")
                self._stack.append('</span>')
                return
            if forge == 'luminari':
                self._out.append("<span class='luminari'>")
                self._stack.append('</span>')
                return
            self._stack.append(None)
            return
        mapped = _FORGE_TAG_MAP.get(tag)
        if mapped:
            self._out.append(mapped[0])
            self._stack.append(mapped[1])
            return
        # Unknown tag: drop the open, but record so its close pairs cleanly.
        self._stack.append(None)

    def handle_startendtag(self, tag, attrs):
        # Self-closing form (e.g. <br/>, <img ... />). Treat as a start tag
        # for void elements so <br/> and <img ... /> still emit, then don't
        # push anything to the stack.
        tag = tag.lower()
        if tag in ('br', 'img'):
            self.handle_starttag(tag, attrs)
            return
        # Anything else self-closing is unknown: drop entirely.

    def handle_endtag(self, tag):
        if tag.lower() == 'br':
            return
        if not self._stack:
            return
        close = self._stack.pop()
        if close is not None:
            self._out.append(close)

    def handle_data(self, data):
        if data:
            self._out.append(escape(data))

    def result(self):
        # Close any still-open allowlisted tags so output is well-formed.
        while self._stack:
            close = self._stack.pop()
            if close is not None:
                self._out.append(close)
        return ''.join(self._out)


@register.filter
def format_forge_text(value):
    """Sanitize forge rich-text HTML for display (globals only).

    Storage is a strict-allowlist HTML produced by the rich-text editor's
    serializer (see the_forge/static/the_forge/forge_richtext.js). This
    filter parses it, drops anything outside the allowlist, and emits the
    final HTML to render: <strong>/<em> kept as-is, <span data-forge="…">
    rewritten to use CSS classes, and <img data-forge-image="KEY"> resolved
    against FORGE_INLINE_IMAGES at render time.

    For per-sheet custom images use `{% forge_text value sheet=sheet %}`.
    """
    if not value:
        return ""
    parser = _ForgeHtmlSanitizer()
    parser.feed(str(value))
    parser.close()
    return mark_safe(parser.result())


@register.simple_tag
def forge_text(value, sheet=None):
    """Sanitize forge rich-text HTML, resolving custom_image_N tokens
    against `sheet.custom_inline_images` when a sheet is supplied."""
    if not value:
        return ""
    parser = _ForgeHtmlSanitizer(sheet=sheet)
    parser.feed(str(value))
    parser.close()
    return mark_safe(parser.result())
