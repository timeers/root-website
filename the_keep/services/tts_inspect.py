"""Read-only analysis of a post's TTS JSON.

Nothing here changes the JSON: TTS JSON is either built correctly by the site
or uploaded by an admin who reviewed it, and is served exactly as stored.

- describe_tts_json(): the derived Post.tts_info -- where the JSON came from and
  what the in-game loader needs (deck roles, map board, ...).
- inspect_tts_json(): the warnings shown on the upload review page (Lua scripts,
  files from untrusted hosts, Library notes).
"""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from .tts import LOCK_ON_REST_LUA, CARD_SUITS

# Scripts the site's own builders write. Anything else in uploaded JSON is
# listed for review.
KNOWN_LUA = {LOCK_ON_REST_LUA}

# Tags the RTT coordinator's shuffleMaps acts on; a map carrying any of them
# gets random set-up (ruins, suits, ...) when the loader asks for a shuffle.
MAP_SHUFFLE_TAGS = {'Ruin', 'Dummy', 'Clearing Marker', 'Shuffleable', 'City Marker', 'Tool',
                    'Clearing 1', 'Clearing 2', 'Clearing 3', 'Clearing 4', 'Clearing 5'}
DECK_OBJECT_NAMES = {'Deck', 'DeckCustom', 'Card', 'CardCustom'}
DECK_MARKER_PREFIX = 'rdb:deck:'

DEFAULT_TRUSTED_HOSTS = (
    'steamusercontent.com',            # and its subdomains (cloud-3.steamusercontent.com, ...)
    'steamusercontent-a.akamaihd.net',
    'steamuserimages-a.akamaihd.net',
    'mrdrouf.github.io',               # the RTT mod's own art
)

_XML_URL = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)


# ── Walking objects ─────────────────────────────────────────────────────────

def _children(obj):
    for key in ('ContainedObjects', 'ChildObjects'):
        for child in obj.get(key) or []:
            if isinstance(child, dict):
                yield child
    states = obj.get('States')
    if isinstance(states, dict):
        for child in states.values():
            if isinstance(child, dict):
                yield child


def iter_objects(tts_json):
    """Every object in the save at any depth: inside decks and bags, other
    states, and attached objects."""
    stack = [o for o in (tts_json or {}).get('ObjectStates') or [] if isinstance(o, dict)]
    while stack:
        obj = stack.pop(0)
        yield obj
        stack[0:0] = list(_children(obj))


def _label(obj):
    return obj.get('Nickname') or obj.get('Name') or 'Unnamed object'


def _card_count(obj):
    if obj.get('Name') in ('Deck', 'DeckCustom'):
        return len(obj.get('ContainedObjects') or [])
    return 1


# ── tts_info ────────────────────────────────────────────────────────────────

def deck_roles(states):
    """'main' / 'accessory' per top-level object (None for non-decks). Objects
    tagged "Deck Accessory" are accessories; otherwise the deck with the most
    cards is the main deck (first on ties) and the rest are accessories."""
    decks = [i for i, o in enumerate(states) if o.get('Name') in DECK_OBJECT_NAMES]
    tagged = {i for i in decks if 'Deck Accessory' in (states[i].get('Tags') or [])}
    candidates = [i for i in decks if i not in tagged] or decks
    main = max(candidates, key=lambda i: (_card_count(states[i]), -i)) if candidates else None
    return [None if i not in decks else ('main' if i == main else 'accessory') for i in range(len(states))]


def map_board_index(states):
    """1-based index of the map board: the object with the most snap points
    (first on ties). None for an empty save."""
    if not states:
        return None
    return max(range(len(states)), key=lambda i: (len(states[i].get('AttachedSnapPoints') or []), -i)) + 1


def describe_tts_json(post, tts_json, *, source=None, profile=None, filename=None, previous=None):
    """The Post.tts_info for `tts_json`. `source` is 'generated' / 'uploaded' /
    'forge'; with no source (a recheck) the previous source fields are kept."""
    if not tts_json:
        return None
    states = [o for o in tts_json.get('ObjectStates') or [] if isinstance(o, dict)]
    info = {}
    if source:
        info['source'] = source
        info['updated_at'] = timezone.now().isoformat()
        info['updated_by'] = profile.name if profile else ''
        if filename:
            info['filename'] = filename
    elif previous:
        for key in ('source', 'updated_at', 'updated_by', 'filename'):
            if key in previous:
                info[key] = previous[key]
    info['object_count'] = len(states)

    if post.component == 'Deck':
        roles = deck_roles(states)
        info['deck_roles'] = roles
        main = states[roles.index('main')] if 'main' in roles else None
        cards = (main.get('ContainedObjects') or [main]) if main else []
        info['dominance'] = any((c.get('Nickname') or '').strip().lower() == 'dominance' for c in cards)
    elif post.component == 'Map':
        info['map_board'] = map_board_index(states)
        info['shuffle'] = any(MAP_SHUFFLE_TAGS & set(o.get('Tags') or []) for o in states)
    return info


# ── Review warnings ─────────────────────────────────────────────────────────

def trusted_hosts():
    hosts = set(getattr(settings, 'TTS_TRUSTED_ASSET_HOSTS', DEFAULT_TRUSTED_HOSTS))
    site_host = (urlsplit(settings.SITE_URL).hostname or '').lower()
    if site_host:
        bare = site_host[4:] if site_host.startswith('www.') else site_host
        hosts.update({bare, f'www.{bare}'})
    return {h.lower() for h in hosts}


def _host_trusted(host, hosts):
    return any(host == h or host.endswith('.' + h) for h in hosts)


def _urls(value, key=''):
    """(url) strings found under keys ending in URL, and http(s) URLs in XmlUI."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str):
                if k.lower().endswith('url'):
                    url = v.replace('{verifycache}', '').strip()
                    if url:
                        yield url
                elif k == 'XmlUI':
                    yield from _XML_URL.findall(v)
            else:
                yield from _urls(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _urls(v, key)


def inspect_tts_json(post, tts_json, *, fixed_clearings=False, forge_linked=False):
    """Warnings for the upload review page. Returns a dict:
    scripts: [{name, script, known}], untrusted_hosts: [{host, count, examples}],
    notes: [str]. Nothing is changed."""
    scripts = []
    top_script = (tts_json or {}).get('LuaScript') or ''
    if top_script.strip():
        scripts.append({'name': 'Save file (global script)', 'script': top_script, 'known': False})
    for obj in iter_objects(tts_json):
        script = obj.get('LuaScript') or ''
        if script.strip():
            scripts.append({'name': _label(obj), 'script': script, 'known': script in KNOWN_LUA})

    hosts = trusted_hosts()
    untrusted = {}
    for url in _urls(tts_json):
        host = (urlsplit(url).hostname or '').lower()
        if not host or _host_trusted(host, hosts):
            continue
        entry = untrusted.setdefault(host, {'host': host, 'count': 0, 'examples': []})
        entry['count'] += 1
        if len(entry['examples']) < 3 and url not in entry['examples']:
            entry['examples'].append(url)

    return {
        'scripts': scripts,
        'untrusted_hosts': sorted(untrusted.values(), key=lambda e: (-e['count'], e['host'])),
        'notes': _library_notes(post, tts_json, fixed_clearings=fixed_clearings, forge_linked=forge_linked),
    }


def _examples(names):
    shown = ', '.join(names[:3])
    return f"{shown}, ..." if len(names) > 3 else shown


def _library_notes(post, tts_json, *, fixed_clearings, forge_linked):
    states = [o for o in (tts_json or {}).get('ObjectStates') or [] if isinstance(o, dict)]
    notes = []
    if forge_linked:
        notes.append("This faction's JSON comes from a Forge draft. Uploading replaces the Forge-synced "
                     "JSON, and the Forge sync page will show it as replaced.")
    if post.component == 'Deck':
        decks = [o for o in states if o.get('Name') in DECK_OBJECT_NAMES]
        cards = [c for d in decks for c in (d.get('ContainedObjects') or [d])]
        untagged = [d for d in decks if 'Deck Object' not in (d.get('Tags') or [])]
        untagged += [c for d in decks for c in (d.get('ContainedObjects') or [])
                     if 'Deck Object' not in (c.get('Tags') or [])]
        if untagged:
            notes.append(f"{len(untagged)} deck or card object(s) aren't tagged \"Deck Object\", so the RTT "
                         "table won't clear them when the deck is replaced.")
        if not any((d.get('GMNotes') or '').startswith(DECK_MARKER_PREFIX) for d in decks):
            notes.append(f"No deck has the \"{DECK_MARKER_PREFIX}{post.slug}\" GM note, so the Box Score "
                         "won't name this deck or upload its slug.")
        near_dominance = [c.get('Nickname') or '' for c in cards
                          if 'dominance' in (c.get('Nickname') or '').lower()
                          and (c.get('Nickname') or '').strip() != 'Dominance']
        if near_dominance:
            notes.append(f"{len(near_dominance)} card(s) are named like a dominance card but not exactly "
                         f"\"Dominance\" ({_examples(near_dominance)}); the Box Score won't record them.")
        odd = [c.get('Nickname') or 'Unnamed card' for c in cards
               if any(word not in CARD_SUITS for word in (c.get('Description') or '').split())]
        if odd:
            notes.append(f"{len(odd)} card description(s) hold more than suits ({_examples(odd)}); the RTT "
                         "table only reads a description that is exactly a suit.")
    elif post.component == 'Map':
        missing = [o for o in states if 'Map Object' not in (o.get('Tags') or [])]
        if missing:
            notes.append(f"{len(missing)} object(s) aren't tagged \"Map Object\", so the RTT table won't "
                         "clear them when the map is replaced.")
        if not any('RTT Map' in (o.get('Tags') or []) for o in states):
            notes.append("No object is tagged \"RTT Map\", so the RTT table won't recognise the board.")
        if fixed_clearings and any('Clearing Marker' in (o.get('Tags') or []) for o in iter_objects(tts_json)):
            notes.append("This map has fixed clearings, but the JSON tags suit markers \"Clearing Marker\", "
                         "so the RTT table will shuffle them.")
    return notes
