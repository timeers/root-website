"""Steam account linking via Steam's OpenID 2.0 endpoint.

Proves that a visitor controls a Steam account and hands back its SteamID64, which
is stored on Profile.steam_id so Tabletop Simulator box scores can later be matched
to site profiles by verified id instead of by display name.

This is NOT a login provider: it attaches an identifier to a Profile that already
exists (site login is Discord-only, LOGIN_URL = 'discord_login'). allauth ships a
Steam provider, but it is unusable here on three counts -- it needs `python3-openid`
(not installed, unmaintained), it is a *login* provider, and it requires a Steam Web
API key purely to fetch a persona name we don't want. Steam's OpenID response can be
checked with a single POST using `requests`, which is what this module does.

Steam only implements the parts of OpenID 2.0 it needs, so no discovery library is
required: the endpoint is fixed and the only claim we read is the identity URL.
"""
import logging
import re
from urllib.parse import urlencode

import requests
from django.core import signing

logger = logging.getLogger(__name__)

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
OPENID_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"

# Anchored on the whole identity URL so a look-alike host (steamcommunity.com.evil.tld,
# or a scheme-less variant) can't smuggle an id through.
#
# Matches 17 digits GENERALLY rather than a "7656119" prefix: an individual SteamID64 is
# (1<<56)|(1<<52)|(1<<32)|account_id, so it starts at 76561197960265728 but crosses out of
# 76561198... into 765612... as account ids grow. Steam is already issuing those, and a
# prefix match would reject real users.
_STEAM_ID_RE = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")

# Signed hand-off from the Discord bot: /link steam sends the user a URL carrying one of
# these so a brand-new user can link without logging into the site first. It names a
# profile pk and nothing else -- it grants exactly one capability (attach a Steam id to
# that profile) and is NOT a login.
STEAM_LINK_SALT = "steam-link"
STEAM_LINK_MAX_AGE = 900  # 15 minutes


def build_redirect_url(return_to, realm):
    """The Steam URL to send the user to, beginning the OpenID handshake.

    `realm` is what Steam shows the user and pins their saved approval to, and
    `return_to` must live under it -- so both must come from ONE canonical host
    (settings.SITE_URL), never from the request's Host header.

    identifier_select means "you tell me who this is": we don't know the SteamID64
    before they log in, which is the entire point.
    """
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.identity": OPENID_IDENTIFIER_SELECT,
        "openid.claimed_id": OPENID_IDENTIFIER_SELECT,
        "openid.return_to": return_to,
        "openid.realm": realm,
    }
    return f"{STEAM_OPENID_URL}?{urlencode(params)}"


def verify_response(query_params, timeout=10):
    """Verify a Steam OpenID callback and return the SteamID64, or None.

    THE SECURITY BOUNDARY OF THE WHOLE FEATURE. `query_params` is an attacker-supplied
    query string -- anyone can hand-craft a GET to the callback claiming any SteamID64.
    What makes a claim trustworthy is step 2: echoing the parameters back to Steam with
    mode=check_authentication and requiring `is_valid:true`. Only Steam can produce
    signature values that pass. Never read the id without that round trip.

    Returns None for every failure so callers have exactly one error path.
    """
    # Only forward the openid.* namespace; anything else in the query string is ours
    # (or noise) and has no business being echoed to Steam.
    params = {k: v for k, v in query_params.items() if k.startswith("openid.")}

    # Cheap structural rejects first, so a junk/probing request never becomes an
    # outbound HTTP call.
    if params.get("openid.mode") != "id_res":
        return None
    claimed_id = params.get("openid.claimed_id")
    if not claimed_id:
        return None

    params["openid.mode"] = "check_authentication"

    try:
        response = requests.post(STEAM_OPENID_URL, data=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Steam OpenID verification request failed", exc_info=True)
        return None

    # The response is key-value form ("ns:...\nis_valid:true\n"), not JSON.
    if "is_valid:true" not in response.text:
        logger.warning("Steam OpenID verification returned is_valid:false")
        return None

    # Only NOW is the claimed identity trustworthy.
    match = _STEAM_ID_RE.match(claimed_id)
    if not match:
        logger.warning("Steam OpenID returned an unexpected claimed_id")
        return None
    return match.group(1)


def make_link_token(profile_pk):
    """A short-lived signed token naming the profile a Steam id may be attached to."""
    return signing.dumps({"pk": profile_pk}, salt=STEAM_LINK_SALT)


def read_link_token(token):
    """The profile pk from a link token, or None if it's missing, forged or expired."""
    if not token:
        return None
    try:
        return signing.loads(token, salt=STEAM_LINK_SALT, max_age=STEAM_LINK_MAX_AGE)["pk"]
    except (signing.BadSignature, signing.SignatureExpired, KeyError, TypeError):
        return None
