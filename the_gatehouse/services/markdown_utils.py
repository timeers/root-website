import re

import bleach
import markdown
from django.utils.safestring import mark_safe

ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'code', 'pre',
    'ul', 'ol', 'li',
    'h3', 'h4', 'h5', 'h6',
    'blockquote',
    'a',
]
ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'rel', 'target', 'class', 'data-external-link-warning'],
}
ALLOWED_PROTOCOLS = ['http', 'https']

# The site's global `a` rule (the_keep/main.css) is `color: inherit` with no
# underline, so a plain link in prose is invisible -- opt every link this
# renderer produces into the shared .visible-link style (the_keep/main.css).
LINK_CLASS = 'visible-link'

_H1_H2_RE = re.compile(r'<(/?)h[12]>')


def _demote_top_headings(html):
    # A user's '#'/'##' become <h1>/<h2>; neither is in ALLOWED_TAGS (they'd
    # outrank the page's own title), and bleach.clean(strip=True) unwraps a
    # disallowed tag rather than dropping its text -- so without this, "#
    # Heading" would degrade to a bare unwrapped line instead of a heading.
    # Collapsing to <h3> keeps it looking like a heading either way.
    return _H1_H2_RE.sub(r'<\1h3>', html)


def _force_safe_link_attrs(attrs, new=False):
    href_key = (None, 'href')
    href = attrs.get(href_key, '')
    if not (href.startswith('http://') or href.startswith('https://')):
        return None  # drop the autolink entirely (bleach.linkify convention)
    attrs[(None, 'rel')] = 'nofollow noopener noreferrer'
    attrs[(None, 'target')] = '_blank'
    attrs[(None, 'class')] = LINK_CLASS
    # Marks this link as coming from untrusted user-submitted markdown, so
    # the site-wide "you're leaving Root Database" confirmation (see
    # the_keep/templates/the_keep/template.html) knows to intercept it --
    # unlike other .visible-link usages elsewhere on the site, which point
    # to trusted internal routes and should navigate normally.
    attrs[(None, 'data-external-link-warning')] = '1'
    return attrs


def render_description_markdown(value):
    """Convert user-entered markdown to sanitized, safe HTML."""
    if not value:
        return ""
    # markdown.markdown does NOT escape or strip raw HTML it doesn't
    # recognize (e.g. "<script>...</script>" passes through completely
    # unchanged) -- bleach.clean() below is the only thing that removes it,
    # so it must always run, and must run LAST, after linkify.
    html = markdown.markdown(str(value), extensions=['sane_lists'])
    html = _demote_top_headings(html)

    # Autolink bare URLs BEFORE the final clean pass (not after), and skip
    # <pre>/<code> so URLs inside code samples aren't turned into links.
    # Running clean() before linkify() (the tempting order) would let
    # linkify's own generated <a> tags -- including autolinked schemes
    # linkify allows by default (e.g. ftp://) -- escape the tag/attribute/
    # protocol allowlist entirely, since nothing re-validates them afterward.
    linked = bleach.linkify(
        html,
        callbacks=bleach.linkifier.DEFAULT_CALLBACKS + [_force_safe_link_attrs],
        skip_tags={'pre', 'code'},
    )

    # Final pass: enforce the tag/attribute/protocol allowlist on
    # everything, including the <a> tags linkify just added.
    cleaned = bleach.clean(
        linked,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return mark_safe(cleaned)


def render_description_plaintext(value, max_length=160):
    """Strip markdown syntax and HTML down to plain text, for <meta> tags."""
    if not value:
        return ''
    html = markdown.markdown(str(value), extensions=['sane_lists'])
    text = bleach.clean(html, tags=[], strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    if max_length and len(text) > max_length:
        text = text[:max_length].rsplit(' ', 1)[0] + '...'
    return text
