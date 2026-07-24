from celery import shared_task
from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from the_keep.models import StatusChoices, Faction, Vagabond, Deck, Map, Landmark, Hireling, Tweak
from the_warroom.models import Game, Effort
from .models import BotUsage, DiscordGuild, GuildLFGRole, LFGThread, Profile

from .services.discordservice import send_discord_message, send_rich_discord_message, send_discord_dm, sync_bot_guilds, post_interaction_followup, DM_ERROR
from .services.context_service import get_daily_user_summary
from .utils import format_bulleted_list

import logging

logger = logging.getLogger(__name__)


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
def sync_bot_guilds_task():
    if sync_bot_guilds() is None:
        raise RuntimeError("Failed to sync bot guilds from Discord")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def post_interaction_followup_task(token, message_data):
    # UNUSED: /draft and /random now edit their public prompt into the result in
    # place (RESPONSE_UPDATE_MESSAGE), so no separate followup message is posted.
    # Kept for future use — any interaction flow that must send an ADDITIONAL
    # public message after its initial response (e.g. an ephemeral command whose
    # result should also post publicly, or a multi-message result) should enqueue
    # this task with (interaction_token, message_data). Retries heal Discord's
    # transient 404s when a followup briefly races ahead of the initial ACK.
    try:
        post_interaction_followup(token, message_data)
    except Exception:
        logger.exception("Discord interaction followup failed")
        raise

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
    except Exception:
        # Lost a create race (unique discord/discord_id): fall back to the now-existing row.
        logger.exception("Profile create raced for discord_id %s", discord_id)
        return (Profile.objects.filter(discord_id=discord_id).first()
                or Profile.objects.filter(discord__iexact=discord_val).first())


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
                           players, embed=None):
    """Create the game thread, ping the players, link the original message's title
    to the thread, and persist the LFGThread row. `players` = [{"id","name"}] parsed
    from the Players field lines, so this task resolves-or-creates every Profile
    itself (no dependency on Join-time onboarding).

    If the game's LFG role has a `forum_channel_id`, the thread is created as a post
    in that forum channel; otherwise it hangs off the LFG message. A role's optional
    `thread_message` is appended to the kickoff ping."""
    from .services.discordservice import (
        create_message_thread, create_forum_thread, post_channel_message,
        edit_channel_message,
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
        return  # no thread → nothing to persist; message already shows "started"

    # Link the original message's title to the new thread (embed titles support a url).
    if embed is not None:
        gid = guild_id or "@me"
        embed["url"] = f"https://discord.com/channels/{gid}/{thread_id}"
        edit_channel_message(channel_id, message_id, [embed])

    thread, _ = LFGThread.objects.get_or_create(
        thread_id=thread_id,
        defaults={"guild": guild, "lfg_role": role, "description": description or ""},
    )
    # Resolve-or-create each player's Profile synchronously (display name is in the
    # embed line), so players.set attaches everyone — no reliance on Join-time tasks.
    profiles = [ensure_profile_from_discord(p["id"], None, p.get("name")) for p in players]
    thread.players.set([p for p in profiles if p])


def _lfg_message_jump_url(guild_id, channel_id, message_id):
    gid = guild_id or "@me"
    return f"https://discord.com/channels/{gid}/{channel_id}/{message_id}"


# kinds whose result is one of the Game's direct FKs (latest selection/roll wins)
_LFG_FK_KINDS = {"Map": "map", "Deck": "deck"}


@shared_task
def record_lfg_components_task(channel_id, items):
    """Record components surfaced inside an LFG thread (from /random, /map, /deck,
    other lookups, /draft). No-op when the channel isn't a known LFG thread."""
    if not channel_id or not items:
        return
    # select_for_update guards against two concurrent captures in the same thread
    # clobbering each other's rolls-list append (last-write-wins otherwise).
    with transaction.atomic():
        thread = LFGThread.objects.select_for_update().filter(thread_id=channel_id).first()
        if not thread:
            return  # not an LFG thread (the common case) — no-op
        now = timezone.now().isoformat()
        for it in items:
            kind, slug, title = it.get("kind"), it.get("slug"), it.get("title")
            thread.rolls.append({"kind": kind, "slug": slug, "title": title, "at": now})
            field = _LFG_FK_KINDS.get(kind)
            if field and slug:
                model = {"map": Map, "deck": Deck}[field]
                setattr(thread, field,
                        model.objects.filter(slug=slug).first() or getattr(thread, field))
        thread.save(update_fields=["rolls", "map", "deck"])
