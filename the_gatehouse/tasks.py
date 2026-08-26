from datetime import timedelta

from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from the_keep.models import StatusChoices, Post, Faction, Vagabond, Deck, Map, Landmark, Hireling, Tweak
from the_warroom.models import Game, Effort
from .models import (BotUsage, DiscordGuild, GuildLFGRole, LFGThread, Profile,
                     LFGRoll, LFGDraft, LFGDraftPick)

from .services.discordservice import send_discord_message, send_rich_discord_message, send_discord_dm, sync_bot_guilds, post_interaction_followup, update_discord_avatar, register_guild_commands, DM_ERROR
from .services.context_service import get_daily_user_summary
from .utils import format_bulleted_list

import logging

logger = logging.getLogger(__name__)

# Discord message flag: only the invoking user sees the message. Mirrors EPHEMERAL in
# discord_interactions.py — defined locally to avoid a circular import (that module
# imports from this one).
EPHEMERAL = 64


@shared_task
def test_task(message=None):
    if message:
        send_discord_message(message, category="feedback")
    else:
        send_discord_message("This is a scheduled test.", category="feedback")

def update_status_fk_model(obj_queryset, related_model, related_field, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_related = related_model.objects.filter(
            **{related_field: obj},
            date_posted__gte=inactive_period
        ).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_related:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])


        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_related and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])


    return development_count, development_list, inactive_count, inactive_list


def update_status_m2m_model(obj_queryset, related_name, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_games = getattr(obj, related_name).filter(date_posted__gte=inactive_period).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_games:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])

        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_games and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])

    return development_count, development_list, inactive_count, inactive_list



@shared_task
def update_post_status():
    
    inactive_period = timezone.now() - relativedelta(months=6)

    development_count = 0
    development_list = []
    inactive_count = 0
    inactive_list = []


    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Faction.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'faction',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list,
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Vagabond.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'vagabond',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Deck.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'deck',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Map.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'map',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )


    # Many-to-many fields (Landmark, Tweak, Hireling)
    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Landmark.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Tweak.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Hireling.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    # Create cleanup message
    fields = []
    if development_count or inactive_count:
        if development_count:
            fields.append({
                'name': 'Development',
                'value': format_bulleted_list(development_list)
            })
        if inactive_count:
            fields.append({
                'name': 'Inactive', 
                'value': format_bulleted_list(inactive_list)
                })
        if not inactive_count:
            message = f'{development_count} Post(s) moved to Development.'
        elif not development_count:
            message = f'{inactive_count} Post(s) moved to Inactive.'
        else:
            message = f'{development_count} Post(s) moved to Development and {inactive_count} Post(s) moved to Inactive.'
    else:
        message = f'No Posts moved during cleanup.'

        
    # Call cleanup summary message to Discord
    send_rich_discord_message(
        message,
        author_name='RDB Admin',
        category='inactive-cleanup',
        title='Inactive Cleanup',
        fields=fields
    )


@shared_task
def daily_users():
    summary = get_daily_user_summary()

    send_rich_discord_message(
        summary['message'],
        author_name='RDB Admin',
        category='user-summary',
        title='Daily User Summary',
        fields=summary['fields']
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_rich_discord_message_task(*args, **kwargs):
    try:
        send_rich_discord_message(*args, **kwargs)
    except Exception:
        logger.exception("Discord webhook failed")
        raise

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_discord_message_task(*args, **kwargs):
    try:
        send_discord_message(*args, **kwargs)
    except Exception:
        logger.exception("Discord webhook failed")
        raise


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def post_channel_message_task(channel_id, content):
    """Post a message into a channel/thread off the request path (the underlying
    call blocks for up to 5s, which an interaction response can't afford).
    post_channel_message never raises, so surface a transient failure as one to
    trigger the retry."""
    from .services.discordservice import post_channel_message, THREAD_ERROR
    if post_channel_message(channel_id, content) == THREAD_ERROR:
        raise RuntimeError(f"Transient failure posting message in channel {channel_id}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_discord_dm_task(user_id, content=None, embed=None):
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.get(pk=user_id)
    result = send_discord_dm(user, content=content, embed=embed)
    # Only retry transient failures. A blocked DM (no shared server / DMs off)
    # is permanent — return quietly instead of retrying 3x per recipient.
    if result == DM_ERROR:
        raise RuntimeError(f"Transient failure sending DM to user {user_id}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def update_discord_avatar_task(user_id, force=False):
    # Deferred from user_logged_in_handler: the avatar download is a pure
    # side-effect (its result isn't used in the login flow), so it doesn't
    # need to block the request path on a Discord CDN call.
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    update_discord_avatar(user, force=force)


@shared_task(bind=True, max_retries=3)
def refresh_user_guilds_task(self, user_id):
    """Refresh a user's Discord guild membership OFF the login request thread.

    Deferred from user_logged_in_handler so a slow/rate-limited Discord API call can
    never block (and eventually exhaust) the WSGI worker pool — the outage this fixes.
    This task is the AUTHORITY for guild flags + group promotion (it has fresh data);
    login itself runs against the cached Profile flags.

    Retry is manual (NOT autoretry_for) so `guilds_refreshing` stays True across pending
    retries — clearing it per-failed-attempt would drop the header spinner while a retry
    is still queued. The flag clears exactly once: on success, on the no-token no-op, or
    when retries are exhausted.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from the_keep.models import Post
    from .services.discordservice import (
        get_user_guilds, update_user_guilds, derive_guild_membership,
        get_discord_display_name,
    )

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None or not hasattr(user, 'profile'):
        return

    def _clear_flag(profile):
        profile.guilds_refreshing = False
        profile.guilds_synced_at = timezone.now()
        profile.save(update_fields=['guilds_refreshing', 'guilds_synced_at'])

    profile = user.profile

    guilds = get_user_guilds(user)
    if guilds is None:
        # API/token failure — distinct from "really in no guilds" ([]). Do NOT touch
        # flags/group (never demote on a transient failure). Retry a few times, then
        # give up and clear the spinner so it can't get stuck.
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=30 * (self.request.retries + 1))
        _clear_flag(profile)
        return

    update_user_guilds(user, guilds)
    in_ww, in_wr, in_fr = derive_guild_membership(guilds)

    updated = False

    # Promote a Woodland Warriors member who's still Outcast/Player (moved here from the
    # login signal so it runs against FRESH membership, incl. first login).
    if profile.group in ('O', 'P') and in_ww:
        has_posts = Post.objects.filter(
            Q(designer=profile) | Q(co_designers=profile) | Q(moderators=profile)
        ).exists()
        if has_posts:
            profile.group = 'E'
            updated = True
        elif profile.group == 'O':
            profile.group = 'P'
            updated = True

    # Feature flags are only ever turned ON (never demoted here) — matches prior behavior.
    if not profile.in_weird_root and in_wr:
        profile.in_weird_root = True
        profile.weird = True
        updated = True
    if not profile.in_french_root and in_fr:
        profile.in_french_root = True
        updated = True
    if not profile.in_woodland_warriors and in_ww:
        profile.in_woodland_warriors = True
        updated = True

    display_name = get_discord_display_name(user)
    if display_name and profile.display_name != display_name:
        profile.display_name = display_name
        updated = True

    profile.guilds_refreshing = False
    profile.guilds_synced_at = timezone.now()
    if updated:
        profile.save(update_fields=[
            'group', 'in_weird_root', 'weird', 'in_french_root', 'in_woodland_warriors',
            'display_name', 'guilds_refreshing', 'guilds_synced_at',
        ])
    else:
        profile.save(update_fields=['guilds_refreshing', 'guilds_synced_at'])


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def sync_bot_guilds_task():
    if sync_bot_guilds() is None:
        raise RuntimeError("Failed to sync bot guilds from Discord")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def post_interaction_followup_task(token, message_data):
    # Sends an ADDITIONAL message after an interaction's initial response, on the
    # interaction token. Used by /lfg's "add LFG tags" nudge and by /draft's
    # ephemeral seating prompt. Retries heal Discord's transient 404s when a
    # followup briefly races ahead of the initial ACK (callers also pass a small
    # countdown to let the initial response land first).
    try:
        post_interaction_followup(token, message_data)
    except Exception:
        logger.exception("Discord interaction followup failed")
        raise

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def register_guild_commands_task(guild_id):
    """Re-register a guild's slash commands off the request path. Enqueued (debounced
    with a short countdown) after LFG-role changes so /lfg swaps SINGLE↔MULTI and its
    tag choices stay current. register_guild_commands swallows errors and returns False;
    re-raise so Celery retries with backoff on a transient failure / 429."""
    guild = DiscordGuild.objects.filter(pk=guild_id).first()
    if guild and not register_guild_commands(guild):
        raise RuntimeError(f"guild command registration failed for {guild.guild_id}")


@shared_task
def record_bot_usage_task(guild_id, user_id, command):
    # Best-effort per-(guild, user, command) usage count for the Discord bot.
    # Fire-and-forget from the interaction dispatch, so it never delays the 3s
    # response; a lost count is harmless. get_or_create + F() increment is atomic
    # across workers (no read-modify-write race).
    if not user_id:
        return
    try:
        obj, _ = BotUsage.objects.get_or_create(
            guild_id=guild_id or None, user_id=user_id, command=command,
        )
        BotUsage.objects.filter(pk=obj.pk).update(
            count=F("count") + 1, last_used=timezone.now(),
        )
    except Exception:
        logger.exception("Failed to record bot usage")


# ── /lfg ─────────────────────────────────────────────────────────────────────

def ensure_profile_from_discord(discord_id, username, display_name):
    """Lookup-or-create a Profile for a Discord user. Plain function (callable both
    from a task and inline). Match order: unique username (`discord`, case-insensitive)
    when we have one, then `discord_id`. Returns the Profile. `username` may be None
    (e.g. when only a display name + id are known, as at Start) — then match by id and,
    on create, derive the handle from the id."""
    from the_warroom.services.root_league_api import sanitize_discord
    if not discord_id:
        return None
    discord_id = str(discord_id)
    cleaned = sanitize_discord(username) if username else None
    # 1) by username (only if we have one)
    profile = Profile.objects.filter(discord__iexact=cleaned).first() if cleaned else None
    # 2) by discord id
    if not profile:
        profile = Profile.objects.filter(discord_id=discord_id).first()
    if profile:
        # Backfill discord_id if we matched by username and it was missing.
        if not profile.discord_id and not Profile.objects.filter(discord_id=discord_id).exists():
            profile.discord_id = discord_id
            profile.save()
        return profile
    # 3) create — `discord` must be unique; fall back to id-suffixed handle on clash.
    discord_val = cleaned
    if not discord_val or Profile.objects.filter(discord__iexact=discord_val).exists():
        discord_val = sanitize_discord(f"{username or ''}{discord_id}") or discord_id
    try:
        return Profile.objects.create(
            discord=discord_val, discord_id=discord_id, display_name=display_name,
        )
    except IntegrityError:
        # Lost a create race (unique discord/discord_id): fall back to the now-existing row.
        # Only IntegrityError means "raced" — anything else is a real fault and is
        # re-raised below rather than being mislabelled and swallowed.
        profile = (Profile.objects.filter(discord_id=discord_id).first()
                   or Profile.objects.filter(discord__iexact=discord_val).first())
        if profile is None:
            # Neither the id nor the handle resolves: we return None and the caller
            # silently drops this player. Log it — this is the one path that can
            # quietly shrink an LFGThread's player list.
            logger.warning("Profile lookup failed after IntegrityError for discord_id "
                           "%s (discord=%s)", discord_id, discord_val)
        return profile


@shared_task
def ensure_profile_from_discord_task(discord_id, username, display_name):
    """Fire-and-forget wrapper for Join/Notify/lfg onboarding."""
    ensure_profile_from_discord(discord_id, username, display_name)


@shared_task
def notify_lfg_task(notify_ids, joiner_name, description, jump_url, owner_id=None):
    """DM every notify subscriber that a new player joined. The game host (owner_id)
    gets a host-specific line (they can start the game); everyone else is told they'll
    be pinged in the thread. Raw-id DMs (subscribers may have no Profile/SocialAccount);
    Discord's 403 is swallowed per id."""
    from .services.discordservice import send_dm_by_id
    game = f"*{description}*" if description else "your game"
    link = f" {jump_url}" if jump_url else ""
    for uid in notify_ids:
        if owner_id and str(uid) == str(owner_id):
            content = (f"**{joiner_name}** joined {game}.{link}\n"
                       "When it's full, press ✅ to start the thread and ping each player.")
        else:
            content = (f"**{joiner_name}** joined {game}.{link}\n"
                       "You'll be pinged in the game thread when it starts.")
        send_dm_by_id(uid, content=content)


@shared_task
def create_lfg_thread_task(channel_id, message_id, guild_id, role_id, description,
                           players, embed=None, token=None):
    """Create the game thread, ping the players, link the original message's title
    to the thread, and persist the LFGThread row. `players` = [{"id","name"}] parsed
    from the Players field lines, so this task resolves-or-creates every Profile
    itself (no dependency on Join-time onboarding).

    If the game's LFG role has a `forum_channel_id`, the thread is created as a post
    in that forum channel; otherwise it hangs off the LFG message. A role's optional
    `thread_message` is appended to the kickoff ping."""
    from .services.discordservice import (
        create_message_thread, create_forum_thread, post_channel_message,
    )
    guild = DiscordGuild.objects.filter(guild_id=guild_id).first() if guild_id else None
    role = (GuildLFGRole.objects.filter(guild=guild, role_id=role_id).first()
            if role_id and guild else None)

    pings = " ".join(f"<@{p['id']}>" for p in players)
    kickoff = f"{pings} your game can start!".strip()
    if role and role.thread_message:
        kickoff = f"{kickoff} {role.thread_message}".strip()

    thread_name = (description or "Game")[:100]

    if role and role.forum_channel_id:
        # Forum post: the starter message carries the kickoff ping (+ the game embed
        # for context). No parent message to hang off of.
        forum_embed = None
        if embed is not None:
            forum_embed = dict(embed)
            forum_embed["url"] = _lfg_message_jump_url(guild_id, channel_id, message_id)
        thread_id = create_forum_thread(role.forum_channel_id, thread_name, content=kickoff,
                                        embeds=[forum_embed] if forum_embed else None,
                                        tag_id=role.forum_tag_id)
    else:
        thread_id = create_message_thread(channel_id, message_id, thread_name)
        if thread_id:
            post_channel_message(thread_id, kickoff)

    if not thread_id:
        # Thread creation failed (already logged with the Discord error). The message
        # was optimistically flipped to "started" in the synchronous response, so tell
        # the owner privately why no thread appeared. Ephemeral follow-up needs the
        # interaction token; skip if we weren't given one.
        if token:
            notice = "Couldn't create the game thread — check my permissions in this channel."
            if not (role and role.forum_channel_id):
                notice += " I likely need **Create Public Threads** and **Send Messages in Threads** here."
            post_interaction_followup_task.delay(token, {"content": notice, "flags": EPHEMERAL})
        return  # no thread → nothing to persist

    # Link the original message's title to the new thread (embed titles support a url).
    # Dispatched with a countdown to settle the race with ✔ Start's own interaction
    # edit — see link_lfg_message_task. Enqueued BEFORE the DB write below so a
    # persistence failure can't also cost the user the title link.
    if embed is not None:
        gid = guild_id or "@me"
        link_lfg_message_task.apply_async(
            (channel_id, message_id, embed,
             f"https://discord.com/channels/{gid}/{thread_id}"),
            countdown=2,
        )

    thread, _ = LFGThread.objects.get_or_create(
        thread_id=thread_id,
        defaults={"guild": guild, "lfg_role": role, "description": description or ""},
    )
    # Resolve-or-create each player's Profile synchronously (display name is in the
    # embed line), so players.set attaches everyone — no reliance on Join-time tasks.
    if not players:
        logger.warning("LFG thread %s: no players parsed from the embed", thread_id)
    profiles = [ensure_profile_from_discord(p["id"], None, p.get("name")) for p in players]
    resolved = [p for p in profiles if p]
    if len(resolved) != len(players):
        unresolved = [p["id"] for p, prof in zip(players, profiles) if not prof]
        logger.error("LFG thread %s: resolved %d/%d player profiles; unresolved ids=%s",
                     thread_id, len(resolved), len(players), unresolved)
    thread.players.set(resolved)
    logger.info("LFG thread %s: attached %d players", thread_id, len(resolved))


def _lfg_message_jump_url(guild_id, channel_id, message_id):
    gid = guild_id or "@me"
    return f"https://discord.com/channels/{gid}/{channel_id}/{message_id}"


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
)
def link_lfg_message_task(channel_id, message_id, embed, thread_url):
    """Point the started LFG message's embed title at the new game thread.

    Split out of create_lfg_thread_task and dispatched with a small countdown to
    settle a race: ✔ Start answers the interaction with RESPONSE_UPDATE_MESSAGE,
    but the embed Celery serialized at .delay() time predates `url`, so whichever
    write Discord applies LAST wins. The countdown lets the interaction's own edit
    land first, and this PATCH re-sends `components: []` so it's a strict superset
    of that edit and wins on content regardless of arrival order.

    Unlike thread creation this edit is idempotent, so retrying is safe — but only
    for transient failures; a 403/404 is permanent and must not burn retries."""
    from .services.discordservice import (
        edit_channel_message, THREAD_OK, THREAD_BLOCKED, THREAD_ERROR,
    )
    embed = dict(embed or {})
    embed["url"] = thread_url
    result = edit_channel_message(channel_id, message_id, embeds=[embed], components=[])
    if result == THREAD_BLOCKED:
        # Permanent (message deleted, or we may not edit components this way).
        # Fall back to an embeds-only edit so we still land the title link rather
        # than leaving the message worse off than before this task existed.
        if edit_channel_message(channel_id, message_id, embeds=[embed]) == THREAD_OK:
            logger.warning("LFG link: components edit rejected for message %s; "
                           "fell back to embeds-only", message_id)
        return
    if result == THREAD_ERROR:
        raise RuntimeError(f"transient failure linking LFG message {message_id}")


# kinds whose result is one of the Game's direct FKs (latest selection/roll wins)
_LFG_FK_KINDS = {"Map": "map", "Deck": "deck"}


@shared_task
def record_lfg_components_task(channel_id, items, source="", draft=None):
    """Record components surfaced inside an LFG thread (from /random, /map, /deck,
    other lookups, /draft). No-op when the channel isn't a known LFG thread.

    `source` tags where the items came from (random / lookup / draft). `draft`,
    when given, REPLACES the thread's current draft: {"players", "platform",
    "drafted_by": <discord id>, "picks": [{"faction","vagabond","captains",
    "order"}]}. Everything on the wire is slugs and ids — Celery serializes as
    JSON, so model instances would raise EncodeError in the caller.

    `source` and `draft` are keyword-defaulted so the other capture call sites
    keep working and so tasks enqueued by older code still deserialize.
    """
    if not channel_id or not (items or draft):
        return
    # select_for_update still earns its place: the map/deck update below is a
    # read-modify-write, and the draft replacement must not interleave with a
    # concurrent one. (The roll rows themselves are plain inserts and don't race.)
    with transaction.atomic():
        thread = LFGThread.objects.select_for_update().filter(thread_id=channel_id).first()
        if not thread:
            return  # not an LFG thread (the common case) — no-op

        items = items or []
        # One query for every rolled component. NOTE this dict holds base Post
        # instances and must NOT be reused for the typed FKs below (map/deck/
        # faction): MTI forbids assigning a parent instance to a child FK.
        slugs = [it.get("slug") for it in items if it.get("slug")]
        posts_by_slug = {}
        if slugs:
            posts_by_slug = {p.slug: p for p in Post.objects.filter(slug__in=slugs)}

        rolls = []
        for it in items:
            kind, slug = it.get("kind"), it.get("slug")
            # created_at is deliberately not passed: letting the field default
            # fire per row keeps draw order unambiguous.
            rolls.append(LFGRoll(thread=thread, kind=kind or "", slug=slug or "",
                                 post=posts_by_slug.get(slug), source=source or ""))
        if rolls:
            LFGRoll.objects.bulk_create(rolls)

        # map/deck keep their own typed lookup -- posts_by_slug holds Post rows,
        # and `thread.map = <Post>` raises ValueError under multi-table inheritance.
        touched = []
        for it in items:
            field = _LFG_FK_KINDS.get(it.get("kind"))
            slug = it.get("slug")
            if field and slug:
                model = {"map": Map, "deck": Deck}[field]
                obj = model.objects.filter(slug=slug).first()
                if obj:
                    setattr(thread, field, obj)
                    touched.append(field)
        if touched:
            thread.save(update_fields=sorted(set(touched)))

        if draft:
            _replace_lfg_draft(thread, draft)


def _replace_lfg_draft(thread, draft):
    """Replace the thread's current draft with `draft` (a JSON-safe dict).

    A thread holds ONE draft: re-running /draft supersedes the previous one
    rather than accumulating. Runs inside record_lfg_components_task's locked
    transaction. Unresolvable slugs are skipped and logged rather than aborting
    the batch -- a bad slug must never cost the user their delivered draft.
    """
    picks = draft.get("picks") or []
    faction_slugs = [p.get("faction") for p in picks if p.get("faction")]
    # Typed querysets, NOT the Post dict from the caller (see the MTI note above).
    factions = {f.slug: f for f in Faction.objects.filter(slug__in=faction_slugs)}

    vb_slugs = set()
    for p in picks:
        if p.get("vagabond"):
            vb_slugs.add(p["vagabond"])
        vb_slugs.update(p.get("captains") or [])
    vagabonds = {v.slug: v for v in Vagabond.objects.filter(slug__in=vb_slugs)} if vb_slugs else {}

    drafted_by = None
    if draft.get("drafted_by"):
        drafted_by = Profile.objects.filter(discord_id=str(draft["drafted_by"])).first()

    obj, _ = LFGDraft.objects.update_or_create(
        thread=thread,
        defaults={"players": draft.get("players"),
                  "platform": draft.get("platform") or "",
                  "drafted_by": drafted_by},
    )
    obj.picks.all().delete()

    for p in picks:
        faction = factions.get(p.get("faction"))
        if not faction:
            logger.warning("LFG draft: unknown faction slug %r for thread %s",
                           p.get("faction"), thread.thread_id)
            continue
        pick = LFGDraftPick.objects.create(
            draft=obj, faction=faction, order=p.get("order") or 0,
            vagabond=vagabonds.get(p.get("vagabond")) if p.get("vagabond") else None,
        )
        caps = [vagabonds[s] for s in (p.get("captains") or []) if s in vagabonds]
        if caps:
            pick.captains.set(caps)


# ── /schedule proposals ──────────────────────────────────────────────────────
# A proposal's public message is posted by the BOT (not as an interaction followup)
# so it stays editable indefinitely: an interaction token expires after 15 minutes
# and proposals routinely outlive that.

# Copy for a proposal retired without anyone rejecting it. Keyed by the `reason`
# the caller passes, so the task stays a dumb renderer.
_PROPOSAL_RETIRED_TEXT = {
    "superseded": "A different time was confirmed for this match. This proposal is "
                  "no longer active.",
    "cancelled": "This proposed time is no longer active — the match's scheduled "
                 "time was changed or cleared.",
    "expired": "This proposed time has passed without everyone confirming.",
}


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
)
def post_schedule_proposal_task(proposal_id, message_data):
    """Post a schedule proposal's public message and record its id on the row.

    The id is the whole point: without it the proposal can't be edited later
    (superseded/cancelled) from outside its own interaction.

    Bails out when the proposal stopped being OPEN while the task sat queued — e.g.
    another proposal finalized first. Posting live buttons for a dead proposal would
    hand someone a control that could overwrite a confirmed time."""
    from .models import ScheduleProposal
    from .services.discordservice import (
        post_channel_message_full, THREAD_OK, THREAD_ERROR,
    )

    proposal = ScheduleProposal.objects.filter(pk=proposal_id).first()
    if not proposal or not proposal.is_open or not proposal.channel_id:
        return

    result, message_id = post_channel_message_full(
        proposal.channel_id, **(message_data or {}))
    if result == THREAD_ERROR:
        # Transient: let autoretry have another go.
        raise RuntimeError(f"transient failure posting proposal {proposal_id}")
    if result == THREAD_OK and message_id:
        # .update() rather than .save(): never clobber a status another request
        # changed while this task was in flight.
        ScheduleProposal.objects.filter(pk=proposal_id).update(message_id=message_id)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 10},
)
def strip_schedule_proposal_messages_task(proposal_ids, reason):
    """Remove the buttons from retired proposal messages and replace the embed with
    a short explanation.

    Purely cosmetic: the DB status is what actually stops a retired proposal being
    confirmed, so a PERMANENT failure (message deleted, missing perms) is logged and
    skipped rather than retried. Only transient failures re-raise, and only after
    every id has been attempted — one dead message must not block the rest."""
    from .models import ScheduleProposal
    from .services.discordservice import (
        edit_channel_message, THREAD_ERROR,
    )

    text = _PROPOSAL_RETIRED_TEXT.get(reason, _PROPOSAL_RETIRED_TEXT["cancelled"])
    transient = []
    for proposal in ScheduleProposal.objects.filter(pk__in=list(proposal_ids or [])):
        if not proposal.channel_id or not proposal.message_id:
            continue  # never posted (or the id never landed) — nothing to strip
        result = edit_channel_message(
            proposal.channel_id, proposal.message_id,
            embeds=[{"title": "Proposal closed", "description": text}],
            components=[],
        )
        if result == THREAD_ERROR:
            transient.append(proposal.pk)
    if transient:
        raise RuntimeError(f"transient failure stripping proposals {transient}")


@shared_task
def cleanup_stale_schedule_proposals(max_age_days=14):
    """Retire OPEN proposals that can never complete: ones whose proposed time has
    passed, and ones left open longer than `max_age_days`.

    Runs on a schedule created in Django admin (django_celery_beat) — this project
    uses DatabaseScheduler, so there is no beat_schedule in code to register it."""
    from .models import ScheduleProposal

    now = timezone.now()
    stale = ScheduleProposal.objects.filter(
        status=ScheduleProposal.Status.OPEN,
    ).filter(
        Q(proposed_time__lt=now)
        | Q(created_at__lt=now - timedelta(days=max_age_days))
    )
    ids = list(stale.values_list("pk", flat=True))
    if not ids:
        return 0
    ScheduleProposal.objects.filter(pk__in=ids).update(
        status=ScheduleProposal.Status.CANCELLED, resolved_at=now)
    strip_schedule_proposal_messages_task.delay(ids, "expired")
    logger.info("Retired %d stale schedule proposals", len(ids))
    return len(ids)
