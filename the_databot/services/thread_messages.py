"""Moderator-authored thread-start message templates.

GuildLFGRole.thread_message and Tournament.thread_message let a moderator
customize the text posted alongside a new game/match thread. Both support a
small set of {token} placeholders that get substituted with real,
context-specific links -- the moderator writes the surrounding sentence and
any markdown link text themselves, e.g. "Compare availability
[here]({availability_link})", and only the bare URL is substituted in.
"""
import re

from the_databot.services.discordservice import config

KNOWN_TOKENS = ("record_link", "availability_link", "rules_link")

_TOKEN_RE = re.compile(r"\{(" + "|".join(KNOWN_TOKENS) + r")\}")


def record_url(path):
    """Absolute URL under SITE_URL, or None when SITE_URL isn't configured."""
    site = (config.get("SITE_URL") or "").rstrip("/")
    return f"{site}{path}" if site else None


def has_thread_message_tokens(template):
    """True if `template` contains any recognized {token} placeholder."""
    return bool(template) and bool(_TOKEN_RE.search(template))


def render_thread_message(template, links):
    """Substitute {token} placeholders in a moderator-authored template with
    bare URLs from `links` (a dict of token name -> value). A token with no
    value (missing, None, or empty) is replaced with an empty string, not left
    as literal text -- a moderator would rather see a slightly awkward
    sentence than raw braces or the literal word "None". An unrecognized
    token is left untouched (never raises) so a typo can't break message
    send."""
    def substitute(match):
        return links.get(match.group(1)) or ""

    return _TOKEN_RE.sub(substitute, template)
