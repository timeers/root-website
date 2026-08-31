from django import template
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
import re

register = template.Library()

# The three inline-markup rules used by the shared LFG copy in
# the_gatehouse/services/discord_commands.py. Discord renders all three natively, so the
# embed sends the bodies as-is; this filter is the HTML half of that pair.
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([a-z0-9-]+)\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_EM_RE = re.compile(r"\*([^*]+)\*")


@register.filter
def lfg_body(text):
    """Render an LFG_HELP_STEPS body as HTML: `code` -> chip, *em*, [label](url-name).

    Link targets are Django URL names, resolved with reverse() so the copy never
    hardcodes a path.
    """
    if not text:
        return ""
    # escape() FIRST: everything after this deliberately inserts markup, so expanding
    # before escaping would let the copy inject raw HTML.
    out = escape(text)
    # .databot-link because the global `a` rule is `color: inherit` with no underline,
    # which would render these as plain prose. Same class the Databot page's hand-written
    # links use, so every link on that page matches.
    out = _LINK_RE.sub(
        lambda m: f'<a class="databot-link" href="{reverse(m.group(2))}">{m.group(1)}</a>',
        out,
    )
    out = _CODE_RE.sub(r'<code class="databot-inline-cmd">\1</code>', out)
    out = _EM_RE.sub(r"<em>\1</em>", out)
    return mark_safe(out)
