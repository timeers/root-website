import os
import uuid
import calendar
import logging
import secrets
import hashlib
from datetime import timedelta

from urllib.parse import urlparse
from django.contrib.auth.models import User
from django.urls import reverse
from django.db import models
from PIL import Image
from django.db.models import Count, F, ExpressionWrapper, FloatField, Q, Case, When, Value
from django.db.models.functions import Cast
from django.apps import apps
from django.utils import timezone 
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from the_keep.utils import validate_hex_color, delete_old_image
from the_keep.services.upload_paths import avatar_upload_path, changelog_image_upload_path

logger = logging.getLogger(__name__)

# The shared fallback avatar every Profile starts on. Lives here (not in
# discordservice) because discordservice imports this module, not the reverse.
DEFAULT_PROFILE_IMAGE = "default_images/default_user.png"

# How long a guild refresh may plausibly be in flight before we stop believing it.
# refresh_user_guilds_task re-stamps guilds_refresh_started_at before each retry, so this
# only has to exceed the longest single gap BETWEEN attempts (90s worst case), not the
# whole ladder -- which keeps it independent of the task's max_retries. Past this the
# flag is treated as stale rather than in-progress, which is what stops a dropped task
# from stranding a profile on the "Finishing your sign-in" spinner forever.
# See Profile.guilds_refresh_in_progress.
GUILDS_REFRESH_MAX_AGE = timedelta(minutes=5)

# Component types eligible for the per-component Discord notification groups
# ("A new <X> is published" / "A <X> is marked Stable"). Each value equals the
# Post.component string on a saved post; a profile opts in by adding the value
# to its stable_notify / new_notify JSON list. Single source of truth for the
# model, the DiscordNotificationsForm, and the notifyservice broadcast lookups.
NOTIFY_COMPONENTS = [
    ("Faction", gettext_lazy("Faction")),
    ("Map", gettext_lazy("Map")),
    ("Deck", gettext_lazy("Deck")),
    ("Vagabond", gettext_lazy("Vagabond")),
    ("Hireling", gettext_lazy("Hireling")),
    ("Landmark", gettext_lazy("Landmark")),
    ("Clockwork", gettext_lazy("Clockwork")),
    ("Tweak", gettext_lazy("House Rule")),
]

class MessageChoices(models.TextChoices):
    DANGER = 'danger'
    WARNING = 'warning'
    SUCCESS = 'success'
    INFO = 'info'


class Language(models.Model):
    code = models.CharField(max_length=10, unique=True)  # 'en', 'fr', etc.
    name = models.CharField(max_length=50)
    
    LOCALE_MAP = {
        "en": "en-US",  # English (United States)
        "fr": "fr-FR",  # French (France)
        "es": "es-ES",  # Spanish (Spain)
        "nl": "nl-NL",  # Dutch (Netherlands)
        "pl": "pl-PL",  # Polish (Poland)
        "ru": "ru-RU",  # Russian (Russia)
        "de": "de-DE",  # German (Germany)

        # Future possible languages
        "pt": "pt-BR",  # Portuguese (Brazil)
        "it": "it-IT",  # Italian (Italy)
        "ja": "ja-JP",  # Japanese (Japan)
        "zh-hans": "zh-CN",    # Chinese Simplified (China)
        "zh-hant": "zh-TW",    # Chinese Traditional (Taiwan)
        "ko": "ko-KR",  # Korean (South Korea)
        "tr": "tr-TR",  # Turkish (Turkey)
    }

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name
    
    @property
    def locale(self):
        # Return mapped locale or fallback to just the code itself
        return self.LOCALE_MAP.get(self.code, self.code)




ALLOWED_DISCORD_DOMAINS = {
    "discord.gg",
    "www.discord.gg",
    "discord.com",
    "www.discord.com",
}

def validate_discord_invite(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Invite must be a valid http or https URL.")

    if parsed.netloc not in ALLOWED_DISCORD_DOMAINS:
        raise ValidationError("Invite must be a Discord invite URL.")

    # Enforce invite path formats
    if not (
        parsed.path.startswith("/invite/")
        or parsed.netloc.endswith("discord.gg")
    ):
        raise ValidationError("Invite must be a valid Discord invite link.")


def validate_discord_snowflake(value):
    # Discord IDs (snowflakes) are numeric strings, currently 17–20 digits.
    if not (value.isdigit() and 17 <= len(value) <= 20):
        raise ValidationError(
            "Enter a valid Discord role ID — a 17–20 digit number. "
            "In Discord, enable Developer Mode, right-click the role and choose “Copy Role ID”."
        )


class DiscordGuild(models.Model):
    guild_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    server_invite = models.URLField(
        max_length=1200, 
        null=True, 
        blank=True,
        validators=[validate_discord_invite],
        help_text="Discord server invite URL")
    request_message = models.TextField(
        blank=True, null=True,
        help_text="The message users will see when they request to join")
    server_rules = models.TextField(
        blank=True, null=True,
        help_text="Rules for the server that a user must acknowledge")
    approval_message = models.TextField(
        blank=True, null=True,
        help_text="Message the user sees next to the invite link upon approval")

    auto_approve_invite = models.BooleanField(
        default=False)

    # Whether OUR bot is a member of this guild. Maintained by the
    # sync_bot_guilds command/task. The bot can only DM users who share
    # a guild with it, so this gates DM reachability.
    bot_member = models.BooleanField(default=False, help_text="Whether the bot is a member of this guild.")

    # From Discord API
    actual_name = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(blank=True, null=True, help_text="Server description from Discord")
    icon_hash = models.CharField(max_length=200, blank=True, null=True, help_text="Discord server icon hash")
    banner_hash = models.CharField(max_length=200, blank=True, null=True, help_text="Discord server banner hash")
    member_count = models.IntegerField(default=0, help_text="Approximate member count")
    online_count = models.IntegerField(default=0, help_text="Approximate online count")
    last_updated = models.DateTimeField(auto_now=True, help_text="Last time Discord info was refreshed")

    # Profiles who moderate this guild. Separate from Profile.guilds (membership);
    # a moderator is not implicitly a member and vice versa.
    guild_moderators = models.ManyToManyField(
        'Profile',
        blank=True,
        related_name="moderated_guilds",
        help_text="Profiles who moderate this guild.",
    )

    # Slash command names enabled in this guild. Only these (plus /help, which is
    # always available and not stored here) are registered with Discord for the
    # guild. A moderator toggles them on the guild edit page. New guilds start
    # empty, so only /help is available until a moderator opts in to more.
    enabled_commands = models.JSONField(
        default=list, blank=True,
        help_text="Slash command names enabled in this guild. /help is always "
                  "available and is not stored here.",
    )
    
    

    def __str__(self):
        return self.name

    def guild_name(self):
        if self.actual_name:
            return self.actual_name
        return self.name

    def get_invite_code(self):
        """Extract invite code from server_invite URL"""
        if self.server_invite:
            return self.server_invite.split('/')[-1].split('?')[0]  # Handle query params
        return None
    
    def get_icon_url(self):
        """Get full Discord icon URL"""
        if self.guild_id and self.icon_hash:
            return f"https://cdn.discordapp.com/icons/{self.guild_id}/{self.icon_hash}.png?size=256"
        return None
    
    def get_banner_url(self):
        """Get full Discord banner URL"""
        if self.guild_id and self.banner_hash:
            return f"https://cdn.discordapp.com/banners/{self.guild_id}/{self.banner_hash}.png?size=512"
        return None

    def get_discord_url(self):
        """Deep link that opens this server in Discord for a logged-in member
        (as opposed to server_invite, which is the join flow)."""
        if self.guild_id:
            return f"https://discord.com/channels/{self.guild_id}"
        return None

class GuildLFGRole(models.Model):
    guild = models.ForeignKey(
        DiscordGuild,
        on_delete=models.CASCADE,
        related_name="lfg_roles",
    )
    name = models.CharField(
        max_length=100,
        help_text="Display tag for the role, e.g. Root Digital LFG or Root TTS LFG",
    )
    role_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        validators=[validate_discord_snowflake],
        help_text=(
            "Discord role ID (a 17–20 digit number), used to build a real mention "
            "(<@&id>). In Discord: enable Developer Mode, find a user with the role and right-click the role, "
            "choose “Copy Role ID”. Leave blank if you only want the display tag."
        ),
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Brief description of what this LFG role is for.",
    )
    # String reference, not a direct import: the_warroom.models imports DiscordGuild /
    # Profile from this module, so importing Tournament here would be circular.
    tournament = models.ForeignKey(
        "the_warroom.Tournament",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lfg_roles",
        help_text=(
            "Optional series this LFG role is for. Only series linked to this "
            "guild are available."
        ),
    )
    forum_channel_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        validators=[validate_discord_snowflake],
        help_text=(
            "Optional Discord forum channel ID (a 17–20 digit number). When set, "
            "starting a game with this role creates the game thread as a post in "
            "that forum channel instead of hanging it off the LFG message. In "
            "Discord: enable Developer Mode, right-click the forum channel, choose "
            "“Copy Channel ID”. Leave blank to thread off the message."
        ),
    )
    forum_tag_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        validators=[validate_discord_snowflake],
        help_text=(
            "Optional forum tag ID (a 17–20 digit number) applied to the game thread "
            "when it's created in the forum channel above. Only used when a Forum "
            "channel ID is set. In Discord: enable Developer Mode, then in the forum "
            "channel's settings right-click a tag to copy its ID (or copy it from the "
            "channel's tag list). Leave blank for no tag."
        ),
    )
    thread_message = models.TextField(
        blank=True,
        null=True,
        help_text=(
            "Optional extra text appended to the thread's first message when a "
            "game with this role starts, e.g. a link to the rules."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A guild shouldn't list the same role name twice.
        unique_together = ("guild", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.guild.name})"

    def mention(self):
        """Pingable Discord mention if we have the numeric role ID, else the display name."""
        if self.role_id:
            return f"<@&{self.role_id}>"
        return self.name

class LFGThread(models.Model):
    """A started LFG game's Discord thread, captured as a durable curation object.
    Later feeds an `lfg_mode` game-form flow (mirroring today's match_mode)."""
    thread_id = models.CharField(
        max_length=32, unique=True,
        help_text="Discord thread id (also the channel id inside the thread).")
    guild = models.ForeignKey(
        DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lfg_threads")
    lfg_role = models.ForeignKey(
        GuildLFGRole, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lfg_threads")
    description = models.TextField(blank=True, default="")
    players = models.ManyToManyField("Profile", blank=True, related_name="lfg_threads")
    # Who ran /lfg. Cannot be derived from `players`: Profile.Meta.ordering is
    # ['display_name'], so players.all() comes back alphabetically and .first()
    # is not the host. The host's snowflake otherwise survives only in the
    # lfg_start/lfg_cancel custom_ids, which are stripped the moment the thread
    # is created -- so it is recorded here or lost. NULL on threads created
    # before this field existed, and on tournament group threads (no host).
    host = models.ForeignKey(
        "Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="hosted_lfg_threads")
    # Whether this thread's seats are a real seating ORDER, or just placeholders.
    #
    # `seats.exists()` cannot answer that on its own: /pick can assign factions
    # without seating, and LFGSeat.seat_number is non-nullable with a uniqueness
    # constraint, so those rows still carry 1..N filler numbers. Without this flag
    # /seating would warn about "overwriting" a seating that was never set, and
    # /pick would never offer to seat such a table again.
    seating_set = models.BooleanField(
        default=False,
        help_text="True when the seats are a real seating order. False when /pick "
                  "assigned factions without seating and seat numbers are filler.")

    # Discord message id of the CURRENT faction-pick panel, so restarting a pick
    # session can close the superseded panel instead of leaving a live faction
    # select on screen pointing at seats that have been cleared.
    #
    # Written when a panel is opened (the mode handler, which edits its own
    # message into the panel and so is the only place the id is knowable -- an
    # interaction response never reveals the id of a message it creates), and
    # cleared by _pick_clear and on completion so a stale id can't outlive its
    # session. Nullable: most threads have no open panel, and one that was never
    # opened must be indistinguishable from one already closed.
    pick_panel_id = models.CharField(max_length=32, blank=True, null=True)

    # `map`/`deck` hold the MOST RECENT of each (whether rolled or selected) — the
    # fields a Game needs directly. The full history lives in the related LFGRoll
    # rows (`roll_log`), which also drive the game form's option narrowing.
    map = models.ForeignKey(
        "the_keep.Map", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    deck = models.ForeignKey(
        "the_keep.Deck", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RECORDED = "recorded", "Recorded"
        CANCELLED = "cancelled", "Cancelled"

    nickname = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Name for the recorded Game; seeds the game form's nickname.")
    # OneToOne: a thread produces at most one Game, so revisiting the record form
    # edits that game instead of creating a duplicate. String ref for the same
    # circular-import reason as GuildLFGRole.tournament.
    #
    # NOTE this is why a series-linked thread (below) must never set `game`: a
    # group thread spans every game of a best-of-N, which one FK can't hold.
    game = models.OneToOneField(
        "the_warroom.Game", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lfg_thread")
    # Set when this thread is a tournament series' group thread rather than an LFG
    # game, so the thread captures rolls/drafts the same way without becoming an
    # LFG game. Makes the LFGThread lookup alone enough to tell the two apart --
    # /record and /seating check it to stay in match mode. A SERIES, not a Match:
    # one group thread covers every game of the series. SET_NULL so deleting a
    # series keeps the captured roll history.
    series = models.ForeignKey(
        "the_warroom.MatchSeries", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lfg_threads")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN)

    # Per-seat box score for the recorded game, in the same shape the game form's
    # JSON import accepts: the `participants` array documented in
    # the_warroom/services/box_score_import.py (turn scores plus dominance and
    # brazen_demagogue). The record form pre-fills the Box Score grid from it.
    #
    # HERE rather than on LFGSeat, even though the seat already holds
    # faction/vagabond/captains: seats are replaced wholesale on re-seat
    # (`locked.seats.all().delete()` in discord_interactions), which would
    # destroy a box score stored there. Entries are keyed by their `turn_order`
    # (the seat number), not list position, so a re-seat can't silently reattach
    # a score to a different player.
    turns_data = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    # Set explicitly, NOT auto_now: nearly every mutation path here writes with a
    # narrow update_fields list (seating_set, nickname, host, map/deck,
    # game+status), and auto_now does not fire when its field is absent from
    # update_fields -- Django would silently leave this stale on exactly the
    # writes that matter. save() below widens the list instead.
    #
    # Not derivable from the children either: LFGSeat carries no timestamp, so
    # /seating and /rename would be invisible to a Greatest() over the roll and
    # draft created_at columns.
    last_activity = models.DateTimeField(
        default=timezone.now, db_index=True,
        help_text="Last time anything about this thread changed. Drives the "
                  "cleanup task; never used for display.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LFGThread {self.thread_id} ({self.players.count()} players)"

    def clean(self):
        """Reject a structurally invalid `turns_data` so the record form can
        trust it.

        Shape only -- the same check the JSON upload runs. Whether a faction or
        player is *allowed* needs a tournament's querysets, which a thread has no
        access to at write time (its role may not even name one yet), so that is
        left to the form.

        NOTE full_clean() is not automatic on save(), so this backstops admin and
        form edits; a caller writing turns_data directly should validate first.
        """
        super().clean()
        if self.turns_data:
            # Imported here: the_warroom imports from this module at module
            # level, so a top-level import would be circular.
            from the_warroom.services.box_score_import import (
                BoxScoreImportError, validate_participants)
            try:
                validate_participants(self.turns_data)
            except BoxScoreImportError as exc:
                raise ValidationError({'turns_data': str(exc)})

    def save(self, *args, **kwargs):
        """Keep `last_activity` current on every write, including the narrow
        update_fields writes that dominate this model's call sites.

        Callers pass update_fields=['seating_set'] etc. deliberately, so rather
        than amend a dozen call sites -- and every future one -- this widens the
        list here. Skipped when the caller names last_activity itself, so a
        fixture can pin a value.

        Three paths still need an explicit bump because they never save the
        thread at all: roll capture and draft replacement (children only),
        re-seating an already-seated thread, and /pick's faction commit (which
        saves the LFGSeat). See the cleanup task for why this field matters.
        M2M writes (players.set) don't call save() either, but they only happen
        on the create path alongside other saves."""
        update_fields = kwargs.get("update_fields")
        if not self._state.adding and (
                update_fields is None or "last_activity" not in update_fields):
            self.last_activity = timezone.now()
            if update_fields is not None:
                kwargs["update_fields"] = list(update_fields) + ["last_activity"]
        super().save(*args, **kwargs)

    def thread_url(self):
        """Discord permalink to this thread — seeds the game form's `link` field.
        Matches the formula in tasks.py and passes GameCreateForm's
        DISCORD_URL_PATTERN. `self.guild_id` is the FK column; `guild.guild_id` is
        the Discord snowflake."""
        if self.guild_id and self.guild and self.guild.guild_id:
            return f"https://discord.com/channels/{self.guild.guild_id}/{self.thread_id}"
        return None


class LFGRoll(models.Model):
    """One component surfaced in an LFG thread (/random, /map, /deck, the other
    lookups, /draft). Append-only history, and the source the game form narrows
    its component choices from.

    `kind` is load-bearing and cannot be derived from `post`: two of the nine
    kinds are not their own model. Clockwork is a Faction row with
    component="Clockwork", and Captain is a Vagabond with captain=True — so a
    rolled captain and a rolled vagabond resolve to the SAME Post row and are
    told apart only by this column. See ROLL_KIND_TO_BUCKET in
    services/lfg_game.py, which maps kind -> asset bucket.
    """
    thread = models.ForeignKey(
        LFGThread, on_delete=models.CASCADE, related_name="roll_log")
    kind = models.CharField(
        max_length=16,
        help_text="Faction/Clockwork/Map/Deck/Vagabond/Captain/Landmark/Hireling/Tweak")
    post = models.ForeignKey(
        "the_keep.Post", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    # Recovery value for when `post` goes NULL because the Post was deleted. NOT
    # the read path: rolled_components emits post.slug first, because slugs are
    # derived from the title and a rename would strand this snapshot.
    slug = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(
        max_length=16, blank=True, default="",
        help_text="Which command produced this: random / lookup / draft / pick.")
    # default=, not auto_now_add: auto_now_add's pre_save() overrides any value
    # passed in, so an explicit timestamp could never be set.
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        # The `id` tiebreak is required, not decoration: writers that reuse one
        # timestamp across a batch would otherwise leave draw order undefined.
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.kind}: {self.slug or self.post_id}"


class LFGDraft(models.Model):
    """The thread's CURRENT draft. Like seating — and unlike the roll log — a
    thread holds exactly one: re-running /draft REPLACES it.

    Drafts used to be appended to the roll log only because there was no way to
    tell a drafted faction from a rolled one. The roll log still keeps that
    history; this is the single current draft.
    """
    thread = models.OneToOneField(
        LFGThread, on_delete=models.CASCADE, related_name="draft")
    players = models.PositiveSmallIntegerField(null=True, blank=True)
    platform = models.CharField(max_length=32, blank=True, default="")
    drafted_by = models.ForeignKey(
        "Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Draft for {self.thread_id} ({self.picks.count()} picks)"


class LFGDraftPick(models.Model):
    """One faction drawn in a draft, with the vagabond/captains rolled FOR it.

    The draft unit is (faction, vagabond), not faction alone: all 12 vagabond
    variants share one Faction row, so faction alone would collapse Ranger and
    Thief. Same reasoning as the_warroom.SeatDraftOption. `captains` covers
    Knaves of the Deepwood, whose captains are rolled as a set. The two are
    mutually exclusive in a draft, so at most one pick carries either.
    """
    draft = models.ForeignKey(
        LFGDraft, on_delete=models.CASCADE, related_name="picks")
    faction = models.ForeignKey(
        "the_keep.Faction", on_delete=models.PROTECT, related_name="+")
    vagabond = models.ForeignKey(
        "the_keep.Vagabond", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+")
    captains = models.ManyToManyField(
        "the_keep.Vagabond", blank=True, related_name="+")
    # Non-null: the draft builder returns an ordered list, so this is always
    # enumerate(drawn, 1) -- never unknown.
    order = models.PositiveSmallIntegerField(help_text="Draw order, 1-based.")

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        label = self.vagabond.title if self.vagabond else self.faction.title
        return f"{self.order}. {label}"


class LFGSeat(models.Model):
    """One seat in a thread's CURRENT seating. Replaced wholesale on re-seat — a
    thread holds exactly one seating, mirroring LFGDraft. (Only LFGRoll
    accumulates; it is the history behind both.)

    The LAST seat has first pick of the faction draft.
    """
    thread = models.ForeignKey(
        LFGThread, on_delete=models.CASCADE, related_name="seats")
    # Nullable + SET_NULL on purpose: if the Profile is ever deleted the seat
    # must stay and render blank, not vanish. The record form places effort rows
    # by list position and sizes the formset from len(seats), so a CASCADE delete
    # would silently shrink the form by a player instead of leaving an empty slot.
    profile = models.ForeignKey(
        "Profile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lfg_seats")
    seat_number = models.PositiveSmallIntegerField()
    # Which faction this seat took, written by /pick. seated_profiles returns it
    # and the game form pre-selects it (in both LFG and match mode).
    faction = models.ForeignKey(
        "the_keep.Faction", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    # The vagabond that goes with `faction`, when the seat took Vagabond. Set
    # from the draft's LFGDraftPick, since all 12 vagabond variants share one
    # Faction row -- faction alone would collapse Ranger and Thief.
    #
    # SET_NULL like every other FK here, NOT LFGDraftPick's PROTECT: a seat must
    # survive a deleted referent and render blank (see the `profile` note above).
    vagabond = models.ForeignKey(
        "the_keep.Vagabond", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    # The captains this seat took, when it took Knaves of the Deepwood. On the
    # SEAT for the same reason as `vagabond`: the faction row alone cannot say
    # which captains a seat holds. Mirrors LFGDraftPick.captains.
    #
    # /pick writes this TWICE: the rolled 4 when the follow-up select is shown,
    # then the 3 chosen from them. It therefore holds 4 while a prompt is open --
    # nothing reads it in that window, and Stop clears it.
    captains = models.ManyToManyField(
        "the_keep.Vagabond", blank=True, related_name="+")
    # The 4th captain -- the one offered but NOT taken. Stored rather than
    # derived: `captains` holds the offered 4 only while the prompt is open, and
    # the commit overwrites it with the chosen 3, so the difference is knowable
    # only at that moment. The record form pre-selects it as Effort.discarded_captain.
    discarded_captain = models.ForeignKey(
        "the_keep.Vagabond", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    class Meta:
        ordering = ["seat_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "seat_number"],
                name="uniq_lfg_seat_per_thread"),
        ]

    def __str__(self):
        who = self.profile.name if self.profile_id else "(removed player)"
        return f"Seat {self.seat_number}: {who}"


class ScheduleProposal(models.Model):
    """A proposed time for a Match, pending confirmation from every roster player.

    /schedule no longer writes Match.scheduled_time directly (when the tournament
    opts in via require_participant_schedule_confirmation): it creates one of these
    and posts a public message with Confirm/Reject. The time is written only once
    every roster player has confirmed.

    Several proposals may be OPEN for one match at once — two players may each
    suggest a time. The first to reach full confirmation wins and supersedes the
    rest; the losers are retired so their buttons can no longer overwrite it.

    channel_id/message_id record the public message so a superseded or cancelled
    proposal can have its buttons stripped from OUTSIDE its own interaction."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        # Every roster player confirmed, but nobody who did could actually write
        # the time (MODERATORS-only recording_access). The proposal waits here
        # for a moderator to press Set Time. Still LIVE -- see LIVE_STATUSES.
        AGREED = "agreed", "Agreed (awaiting a moderator)"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"   # another proposal won first
        # Retired without a human rejecting it: the time was cleared or directly
        # rewritten, the proposer lost permission, or the cleanup task expired it.
        CANCELLED = "cancelled", "Cancelled"

    # The statuses that still compete for a match: a proposal in either can still
    # become the scheduled time, so both must be swept when a time is set another
    # way and both must be expired by the cleanup task. Query with
    # `status__in=ScheduleProposal.LIVE_STATUSES`.
    #
    # Assigned after the class body -- a TextChoices member is not addressable as
    # Status.OPEN from inside the class being defined.

    # Lazy string: this module never imports the_warroom at module level.
    match = models.ForeignKey(
        "the_warroom.Match", on_delete=models.CASCADE,
        related_name="schedule_proposals")
    proposed_time = models.DateTimeField(help_text="The proposed instant (UTC).")
    proposed_by = models.ForeignKey(
        'Profile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name="schedule_proposals_made")

    # Roster SNAPSHOT taken at proposal time, so a mid-flight roster change can't
    # strand a proposal that everyone present already confirmed.
    roster = models.ManyToManyField(
        'Profile', blank=True, related_name="schedule_proposals_on_roster")
    confirmed_by = models.ManyToManyField(
        'Profile', blank=True, related_name="schedule_proposals_confirmed",
        help_text="Roster players who pressed Confirm. The proposer is seeded here "
                  "at creation — proposing IS confirming.")
    # A LIST, not a single rejecter: a "No" is now a vote rather than a
    # termination, so several players can decline the same time and the poll stays
    # open until everyone has answered. Was a nullable FK when one rejection
    # closed the proposal outright; the migration copies those rows in.
    rejected_by = models.ManyToManyField(
        'Profile', blank=True, related_name="schedule_proposals_rejected",
        help_text="Roster players who pressed No. Mutually exclusive with "
                  "confirmed_by — answering moves you between the two.")

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)

    # Where the public message lives, so it can be edited later from a task or from
    # a DIFFERENT proposal's interaction (superseding).
    channel_id = models.CharField(max_length=32, blank=True, default="")
    message_id = models.CharField(max_length=32, blank=True, default="")
    guild_id = models.CharField(max_length=32, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["match", "status"])]

    def __str__(self):
        return f"ScheduleProposal #{self.pk} ({self.get_status_display()})"

    @property
    def is_open(self):
        """EXACTLY open. Kept narrow on purpose: callers that mean "can still be
        confirmed or rejected" want this, and must not silently start accepting
        AGREED rows, whose confirmations are already complete."""
        return self.status == self.Status.OPEN

    @property
    def is_live(self):
        """Still in play: open, or agreed and waiting on a moderator."""
        return self.status in self.LIVE_STATUSES

    def pending_profiles(self):
        """Roster players who have not ANSWERED yet — neither yes nor no.

        A player who said no has responded, so they are no longer pending even
        though they never confirmed. Excluding only confirmed_by would leave them
        listed as still-awaited forever and the poll could never complete."""
        answered = list(self.confirmed_by.values_list("pk", flat=True)) + \
            list(self.rejected_by.values_list("pk", flat=True))
        return self.roster.exclude(pk__in=answered)

    def all_confirmed(self):
        """True when every roster player has confirmed. An EMPTY roster is NOT
        'all confirmed' — an unpopulated player group must never auto-finalize.

        This decides whether a time is WRITTEN. all_responded decides whether the
        poll closes; the two differ exactly when somebody said no."""
        roster_ids = set(self.roster.values_list("pk", flat=True))
        return bool(roster_ids) and roster_ids <= set(
            self.confirmed_by.values_list("pk", flat=True))

    def all_responded(self):
        """True when every roster player has answered, yes or no.

        The poll's completion condition. Same EMPTY-roster guard as
        all_confirmed, and for the same reason: a group with nobody in it would
        otherwise satisfy 'everyone answered' vacuously and close on creation."""
        roster_ids = set(self.roster.values_list("pk", flat=True))
        if not roster_ids:
            return False
        answered = set(self.confirmed_by.values_list("pk", flat=True)) | set(
            self.rejected_by.values_list("pk", flat=True))
        return roster_ids <= answered


# See the note in ScheduleProposal.Status: assigned here because a TextChoices
# member can't be referenced from inside its own class body.
ScheduleProposal.LIVE_STATUSES = (
    ScheduleProposal.Status.OPEN,
    ScheduleProposal.Status.AGREED,
)


class Holiday(models.Model):
    name = models.CharField(max_length=100)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(default=timezone.now)
    start_day_of_year = models.PositiveSmallIntegerField(default=1)
    end_day_of_year = models.PositiveSmallIntegerField(default=1)
    date_modified = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Set the day of the year fields based on the datetime fields
        self.start_day_of_year = self.start_date.timetuple().tm_yday
        self.end_day_of_year = self.end_date.timetuple().tm_yday
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['start_date', 'name', 'id']

class Theme(models.Model):
    name = models.CharField(max_length=100)
    theme_artists = models.ManyToManyField('Profile', blank=True, related_name='theme_artwork')
    theme_color = models.CharField(
        max_length=7,
        default='#5f788a',
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    background_color = models.CharField(
        max_length=7,
        default='#fafafa',
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    holiday = models.ForeignKey(Holiday, blank=True, null=True, on_delete=models.SET_NULL)
    public = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    backup_theme = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


    def get_artists(self, visited=None):
        """
        Collect all artists associated with this theme, its backgrounds,
        foregrounds, and directly assigned theme_artists.
        Recursively includes artists from any backup_theme, 
        avoiding infinite loops via a visited set.
        """
        if visited is None:
            visited = set()

        # Prevent circular reference recursion
        if self.id in visited:
            return []

        visited.add(self.id)
        artists = set()

        # Background artists
        for image in self.backgrounds.all():
            if image.artist:
                artists.add(image.artist)

        # Foreground artists
        for image in self.foregrounds.all():
            if image.artist:
                artists.add(image.artist)

        # Theme-specific artists
        for artist in self.theme_artists.all():
            artists.add(artist)

        # Recursively include artists from backup theme (if any)
        if self.backup_theme:
            artists.update(self.backup_theme.get_artists(visited=visited))

        return list(artists)




    class Meta:
        ordering = ['name', 'id']

class PageChoices(models.TextChoices):
    LIBRARY = 'library','Library'
    GAMES = 'games', 'Games'
    RESOURCES = 'resources', 'Resources'
    FEEDBACK = 'feedback', 'Feedback'
    ABOUT = 'about', 'About'
    SETTINGS = 'settings', 'Settings'
    LAWS = 'laws', 'Laws'
    FAQ = 'faq', 'FAQ'
    SURVEYS = 'surveys', 'Surveys'
    SERIES = 'series', 'Series'


class BackgroundImage(models.Model):
    name = models.CharField(max_length=100)    
    # artist = models.CharField(max_length=100, blank=True, null=True)
    artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(upload_to='background_images')
    pattern = models.ImageField(upload_to='background_patterns', null=True, blank=True)
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name='backgrounds')
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    background_color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    small_image = models.ImageField(upload_to='background_images/small', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    



    def alt(self):
        if self.artist:
            alt = f'{ self.name } by {self.artist.name}'
        else:
            alt = f'{ self.name }'
        return alt

        
    def save(self, *args, **kwargs):
        
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                field_name = 'image'
                old_instance = BackgroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
                    delete_old_image(getattr(old_instance, 'small_image'))
            except BackgroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass
            try:
                field_name = 'pattern'
                old_instance = BackgroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)

            except BackgroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)


class ForegroundImage(models.Model):
    class LocationChoices(models.IntegerChoices):
        FAR_LEFT = 1, 'Far Left'
        LEFT = 3, 'Left'
        CENTER = 5, 'Center'
        RIGHT = 7, 'Right'
        FAR_RIGHT = 9, 'Far Right'
        TITLE = 100, 'Title'
        SECOND = 101, 'Second Title'
        THIRD = 102, 'Third Title'
    name = models.CharField(max_length=100)
    # artist = models.CharField(max_length=100, blank=True, null=True)
    artist = models.ForeignKey('Profile', on_delete=models.SET_NULL, null=True, blank=True)
    location = models.IntegerField(default=LocationChoices.CENTER, choices=LocationChoices.choices)
    image = models.ImageField(upload_to='foreground_images')
    
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name='foregrounds')
    page = models.CharField(max_length=15 , default=PageChoices.LIBRARY, choices=PageChoices.choices)
    depth = models.IntegerField(default=-1)
    start_position = models.TextField(default='0vw')
    slide = models.TextField(default='0vw')
    speed = models.TextField(default='50vh')
    small_image = models.ImageField(upload_to='foreground_images/small', null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)

    def style(self):
        return f'--offset-percent: { self.slide }; --slide-speed: { self.speed }; --z-depth: { self.depth }; --start-position: { self.start_position };'
    
    def __str__(self):
        return self.name


    def alt(self):
        if self.artist:
            alt = f'{ self.name } by {self.artist.name}'
        else:
            alt = f'{ self.name }'
        return alt
        
    def save(self, *args, **kwargs):
        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                old_instance = ForegroundImage.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
                    delete_old_image(getattr(old_instance, 'small_image'))
            except ForegroundImage.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)



class Profile(models.Model):
    class GroupChoices(models.TextChoices):
        OUTCAST = 'O'
        PLAYER = 'P'
        EDITOR = 'E'
        DESIGNER = 'D'
        ADMIN = 'A'
        BANNED = 'B'
    class StatusChoices(models.TextChoices):
        STABLE = '1','Stable'
        TESTING = '2', 'Testing'
        DEVELOPMENT = '3', 'Development'
        INACTIVE = '4', 'Inactive'
        ABANDONED = '5', 'Abandoned'

    component = 'Profile'

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    # theme = models.CharField(max_length=20 , default=Theme.LIGHT, choices=Theme.choices, null=True, blank=True)
    theme = models.ForeignKey(Theme, on_delete=models.SET_NULL, null=True, blank=True)
    image = models.ImageField(default='default_images/default_user.png', upload_to=avatar_upload_path)
    last_avatar_sync = models.DateTimeField(null=True, blank=True)
    # Set True by the login signal, cleared by refresh_user_guilds_task once the
    # background Discord guild-membership sync finishes. Drives the "syncing" spinner
    # on the header avatar so login never blocks on a slow Discord API call.
    # NEVER read this raw: use guilds_refresh_in_progress, which ignores a flag left
    # standing by a task that died before it could clear it.
    guilds_refreshing = models.BooleanField(default=False)
    # When guilds_refreshing was last raised. Paired with GUILDS_REFRESH_MAX_AGE to
    # expire the flag on read. NULL while the flag is up means "raised before this
    # field existed" — treated as stale, which is what frees the profiles stranded
    # before the expiry existed.
    guilds_refresh_started_at = models.DateTimeField(null=True, blank=True)
    guilds_synced_at = models.DateTimeField(null=True, blank=True)

    dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    rdl_cannonical_dwd = models.CharField(max_length=100, unique=True, blank=True, null=True)
    discord = models.CharField(max_length=100, unique=True, blank=True, null=True)
    league = models.BooleanField(default=False)
    group = models.CharField(max_length=1, choices=GroupChoices.choices, default=GroupChoices.OUTCAST)
    tester = models.BooleanField(default=False)
    weird = models.BooleanField(default=True)
    in_weird_root = models.BooleanField(default=False)
    in_woodland_warriors = models.BooleanField(default=False)
    in_french_root = models.BooleanField(default=False)
    view_status = models.CharField(max_length=15 , default=StatusChoices.INACTIVE, choices=StatusChoices.choices)
    language = models.ForeignKey(Language, on_delete=models.SET_NULL, null=True, blank=True)
    # IANA zone name (e.g. "America/New_York") used to interpret times the user types
    # at the bot, e.g. /schedule. Set once from the command's `timezone` option and
    # reused after that. NOTE: this field shadows the module-level `django.utils.timezone`
    # import inside model methods — always reach it through an instance (profile.timezone).
    timezone = models.CharField(
        max_length=64, blank=True, null=True,
        help_text="IANA timezone (e.g. America/New_York) used to interpret times "
                  "given to the Discord bot.")

    display_name = models.CharField(max_length=100, null=True, blank=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    bookmarks = models.ManyToManyField('self', through='PlayerBookmark')
    player_onboard = models.BooleanField(default=False)
    editor_onboard = models.BooleanField(default=False)
    designer_onboard = models.BooleanField(default=False)
    admin_onboard = models.BooleanField(default=False)
    forge_onboard = models.BooleanField(default=False)
    trusted_tournament_host = models.BooleanField(default=False)
    admin_nominated = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='nominated_by')
    admin_dismiss = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='dismissed_by')
    credit_link = models.CharField(max_length=400, null=True, blank=True, help_text="User's external link to their other endeavors.")
    date_modified = models.DateTimeField(auto_now=True)
    guilds = models.ManyToManyField(DiscordGuild, related_name="members", help_text="User's known Root Guilds.", blank=True)
    # Discord DM notification preferences (all opt-in). The bot can only DM
    # users who share a guild with it; see Profile.can_receive_dms.
    notify_survey_response = models.BooleanField(default=False)
    notify_game_recorded = models.BooleanField(default=False)
    notify_tournament_game_recorded = models.BooleanField(default=False)
    notify_post_game_recorded = models.BooleanField(default=False)
    notify_post_approved = models.BooleanField(default=False)
    # Per-component broadcast opt-ins: each holds a list of Post.component values
    # (see NOTIFY_COMPONENTS) the user wants a DM for. stable_notify -> a component
    # of that type is marked Stable; new_notify -> a new component of that type is
    # published. Empty list = opted out of the whole group.
    stable_notify = models.JSONField(default=list, blank=True)
    new_notify = models.JSONField(default=list, blank=True)
    discord_id = models.CharField(max_length=32, blank=True, null=True, unique=True, help_text="User's Discord ID number.")
    # Cached leaderboard inputs (coalition formula), maintained by
    # calculate_and_cache_winrate via Effort/Game signals. Let the default
    # /leaderboard/ board be a plain indexed query with no aggregation.
    cached_winrate = models.FloatField(null=True, blank=True, db_index=True)
    cached_plays = models.IntegerField(null=True, blank=True, db_index=True)
    cached_tourney_points = models.FloatField(null=True, blank=True)
    # Per-platform cached leaderboard inputs, maintained alongside the overall
    # cached_* fields by calculate_and_cache_winrate.
    cached_irl_winrate = models.FloatField(null=True, blank=True, db_index=True)
    cached_irl_plays = models.IntegerField(null=True, blank=True, db_index=True)
    cached_irl_tourney_points = models.FloatField(null=True, blank=True)
    cached_dwd_winrate = models.FloatField(null=True, blank=True, db_index=True)
    cached_dwd_plays = models.IntegerField(null=True, blank=True, db_index=True)
    cached_dwd_tourney_points = models.FloatField(null=True, blank=True)
    cached_tts_winrate = models.FloatField(null=True, blank=True, db_index=True)
    cached_tts_plays = models.IntegerField(null=True, blank=True, db_index=True)
    cached_tts_tourney_points = models.FloatField(null=True, blank=True)
    # Only a hash of the API key is stored, never the key itself (like GitHub/Discord
    # tokens). The raw key is shown once at generation and is not retrievable afterwards;
    # a DB leak therefore does not expose usable keys. API keys are high-entropy random
    # tokens, so a fast SHA-256 is sufficient (unlike low-entropy passwords).
    api_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True, help_text="SHA-256 hash of the user's game data API key.")
    api_key_created = models.DateTimeField(null=True, blank=True, help_text="When the current API key was generated.")

    @staticmethod
    def hash_api_key(raw_key):
        """Return the SHA-256 hex digest used to store/look up an API key."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def generate_api_key(self):
        """Generate (or regenerate) this profile's API key.

        Stores only the hash and returns the raw key. The raw value is returned exactly
        once here and cannot be recovered later; callers must surface it to the user
        immediately.
        """
        raw_key = secrets.token_urlsafe(32)
        self.api_key_hash = self.hash_api_key(raw_key)
        self.api_key_created = timezone.now()
        self.save(update_fields=['api_key_hash', 'api_key_created'])
        return raw_key

    @property
    def name(self):
        if self.display_name:
            name = self.display_name
        elif self.discord:
            name = self.discord
        else:
            name = "Anonymous"
            # name = self.user.username
        return name
    
    @property
    def active_posts(self):
        return self.posts.filter(status__lte=4).count()

    def __str__(self):
        if self.name.lower() == self.discord:
            return self.name
        else:
            return f'{self.name} ({self.discord})'

    @property
    def can_receive_dms(self):
        """
        True if the bot shares a guild with this user, which is Discord's
        requirement for the bot to be able to DM them. Relies on the
        bot_member flag kept current by sync_bot_guilds.
        """
        return self.guilds.filter(bot_member=True).exists()

    def save(self, *args, **kwargs):
        # Check for blank display names
        if not self.display_name:
            self.display_name = self.discord # set to discord if blank

        # Avatar writes happen in a Celery task (update_discord_avatar_task), so the
        # DB row can gain a new image while a request still holds an older in-memory
        # copy of this profile. A naive "db image != mine -> delete the db's file"
        # is direction-blind: it can't tell "I am replacing the image" from "someone
        # replaced it while I was stale", and deleting in the second case is what
        # left profiles pointing at files that no longer existed.
        if self.pk:
            try:
                db_image = Profile.objects.only('image').get(pk=self.pk).image

                # A save that doesn't write `image` must never touch the image file.
                # Note this block runs BEFORE super().save(), so without this guard
                # even an update_fields save deletes the file while leaving the DB
                # pointer intact — precisely the dead-link signature.
                update_fields = kwargs.get('update_fields')
                writing_image = update_fields is None or 'image' in update_fields

                if writing_image and db_image:
                    reverting_to_default = (
                        self.image.name == DEFAULT_PROFILE_IMAGE
                        and db_image.name != DEFAULT_PROFILE_IMAGE
                    )
                    if reverting_to_default:
                        # Our copy predates a real avatar written by another process.
                        # Adopt the stored value rather than reverting it, and leave
                        # its file alone. A deliberate reset to the default goes
                        # through queryset .update() instead (see
                        # repair_profile_avatars), which bypasses save() entirely.
                        self.image = db_image.name
                    elif db_image.name != self.image.name:
                        # A genuine replacement: clean up the file we're superseding.
                        delete_old_image(db_image)
            except Profile.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)
        self._resize_image()

    def _resize_image(self):
        """Helper method to resize the image if necessary."""
        try:
            # Check if the image exists and is a valid file
            if self.image and os.path.exists(self.image.path):
                img = Image.open(self.image.path)

                max_size = 125
                if img.height > max_size or img.width > max_size:
                    # Calculate the new size while maintaining the aspect ratio
                    if img.width > img.height:
                        ratio = max_size / img.width
                        new_size = (max_size, int(img.height * ratio))
                    else:
                        ratio = max_size / img.height
                        new_size = (int(img.width * ratio), max_size)

                    # Resize image and save
                    img = img.resize(new_size, Image.LANCZOS)
                    img.save(self.image.path)
                    # print(f'Resized image saved at: {self.image.path}')
                # else:
                    # print(f'Original image saved at: {self.image.path}')
        except Exception:
            # Was a bare print, so avatar corruption failed silently and never
            # surfaced in the logs — the reason this class of bug went unnoticed.
            logger.exception("Error resizing image for profile %s (%s)",
                             self.pk, getattr(self.image, 'name', None))

    #     img = Image.open(self.image.path)

    #     if img.height > 300 or img.width > 300:
    #         output_size = (300, 300)
    #         img.thumbnail(output_size)
    #         img.save(self.image.path)

    @property
    def outcast(self):
        group = self.group
        if group == "O":
            return True
        else:
            return False

    @property
    def banned(self):
        group = self.group
        if group == "B":
            return True
        else:
            return False

    @property
    def admin(self):
        group = self.group
        if group == "A":
            return True
        else:
            return False

    @property
    def designer(self):
        group = self.group
        if group == "A" or group == "D":
            return True
        else:
            return False

    @property
    def editor(self):
        group = self.group
        if group == "A" or group == "D" or group == "E":
            return True
        else:
            return False

    @property
    def player(self):
        group = self.group
        if group == "A" or group == "D" or group == "E" or group == "P":
            return True
        else:
            return False

    @property
    def guilds_refresh_in_progress(self):
        """Is a Discord guild refresh plausibly still running right now?

        Read this instead of the raw guilds_refreshing flag. The flag is cleared only
        by the task's terminal paths, so a worker killed mid-retry (or a task the broker
        never delivered) used to leave it True forever — an endless spinner with no way
        out. Anything older than GUILDS_REFRESH_MAX_AGE is treated as abandoned, so a
        stuck flag stops counting on its own rather than needing an operator to clear it.
        """
        if not self.guilds_refreshing:
            return False
        if self.guilds_refresh_started_at is None:
            # Flag raised before this field existed: it can't be timed, so don't trust it.
            return False
        return timezone.now() - self.guilds_refresh_started_at < GUILDS_REFRESH_MAX_AGE
        

    def winrate(self, faction = None, deck = None, tournament = None, round = None):
        # Coalition-based leaderboard formula (coalition wins count as half),
        # the site's single source of truth. See filtered_winrate().
        from the_warroom.models import filtered_winrate
        return filtered_winrate(
            player=self, faction=faction, deck=deck, tournament=tournament
        )['win_rate']
    
    def get_games_queryset(self, faction=None):
        # Get the model for Game
        Game = apps.get_model('the_warroom', 'Game')
        
        # Start with the Effort queryset
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction)

        # Filter for distinct games linked to these efforts
        games = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().order_by('-date_posted')

        return games


    def games_played(self, faction=None):
        Game = apps.get_model('the_warroom', 'Game')
        # Access the related Effort objects for this player
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction)
        
        # Count the distinct games linked to the efforts
        distinct_game_count = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().count()

        return distinct_game_count
    
    def games_won(self, faction=None):
        Game = apps.get_model('the_warroom', 'Game')
        # Access the related Effort objects for this player
        efforts = self.efforts.all()
        
        # Apply the faction filter if provided
        if faction:
            efforts = efforts.filter(faction=faction, win=True)
        
        # Count the distinct games linked to the efforts
        distinct_game_count = Game.objects.filter(
            id__in=efforts.values_list('game', flat=True),
            final=True
        ).distinct().count()

        return distinct_game_count



    def get_absolute_url(self):
        return reverse('player-detail', kwargs={'slug': self.slug})
    

    def most_used_faction(self):
        from the_keep.models import Faction
        
        most_used = (
            self.efforts.values('faction')
            .annotate(faction_count=Count('faction'))
            .order_by('-faction_count')
        ).first()  # Get the top result

        if most_used:
            faction_id = most_used['faction']
            try:
                # Fetch and return the faction instance
                return Faction.objects.get(pk=faction_id)
            except Faction.DoesNotExist:
                return None  # Handle case where faction doesn't exist
        return None  # Handle case where there are no efforts

    def most_successful_faction(self):
        # Lazy import to avoid circular imports
        from the_keep.models import Faction

        # Aggregate wins by faction
        wins_by_faction = (
            self.efforts.filter(win=True, game__test_match=False, game__final=True)
            .values('faction')  # Assuming 'faction' is the field name
            .annotate(win_count=Count('id'))  # Count wins
            .order_by('-win_count')  # Order by count descending
        )

        # Get the faction with the most wins
        most_successful = wins_by_faction.first()

        if most_successful:
            # Return the corresponding Faction object
            return Faction.objects.get(id=most_successful['faction'])

        return None  # No wins found
    
    class Meta:
        ordering = ['display_name']

    @classmethod
    def top_players(cls, faction_id=None, top_quantity=False, tournament=None, round=None, limit=5, game_threshold=10):
        """
        Get the top players based on their win rate (default) or total efforts.
        If faction_id is provided, get the top players for that faction.
        Otherwise, get the top players across all factions.
        The `limit` parameter controls how many players to return.
        """
        from the_warroom.models import (effort_counts_for_round_q,
                                         effort_counts_for_tournament_q)

        # Start with the base queryset for players
        queryset = cls.objects.filter(efforts__game__final=True, efforts__game__test_match=False)

        # If a tournament is provided, filter efforts that are related to that tournament
        # (via the game's primary round OR an extra round it counts toward)
        if tournament:
            queryset = queryset.filter(
                effort_counts_for_tournament_q(tournament, prefix='efforts__game')
            ).distinct()

        # If a round is provided, filter efforts that are related to that round
        if round:
            queryset = queryset.filter(
                effort_counts_for_round_q(round, prefix='efforts__game')
            ).distinct()
        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__faction_id=faction_id) if faction_id else Q()),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__faction_id=faction_id) if faction_id else Q(efforts__win=True)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__faction_id=faction_id) if faction_id else Q(efforts__win=True, efforts__game__coalition_win=True))
        )
        
        # Filter players who have enough efforts (before doing the annotation)

        queryset = queryset.filter(total_efforts__gte=game_threshold)

        # Annotate with win_rate after filtering
        queryset = queryset.annotate(
            win_rate=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    (Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2 )) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            ),
            tourney_points=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    Cast(F('win_count'), FloatField()) - (Cast(F('coalition_count'), FloatField()) / 2 ),  # Tourney Points
                    output_field=FloatField()
                ),
                output_field=FloatField()
            )
        )
        # Now we can order the queryset
        if top_quantity:
            # If top_quantity is True, order by total_efforts (most efforts) first
            return queryset.order_by('-tourney_points', '-win_rate')[:limit]
        else:
            # Otherwise, order by win_rate (highest win rate) first
            return queryset.order_by('-win_rate', '-total_efforts')[:limit]



    # Columns the leaderboard boards actually read: the template shows image/display_name,
    # get_absolute_url() needs slug, and the as_json payload falls back to discord.
    LEADERBOARD_FIELDS = ('display_name', 'discord', 'slug', 'image')

    @classmethod
    def _leaderboard_annotated(cls, effort_qs, game_threshold=10):
        """The annotated, threshold-filtered board queryset, WITHOUT ordering or slicing.

        Split out of leaderboard() so a caller needing both the "top" (by win rate) and
        "most" (by tourney points) boards can build this once and order it twice, instead
        of running the same aggregate twice. See leaderboard_pair().
        """
        # Start with the base queryset for profiles. .only(...) keeps hydration to the
        # columns the board actually reads (of ~54 on Profile) -- a smaller win than the
        # Faction equivalent, which also spans a multi-table-inheritance join, but this
        # hydrates far more rows (every threshold-qualifying player).
        queryset = cls.objects.filter(efforts__in=effort_qs).only(*cls.LEADERBOARD_FIELDS)

        # Now, annotate with the total efforts and win counts
        queryset = queryset.annotate(
            total_efforts=Count('efforts', filter=Q(efforts__game__final=True, efforts__game__test_match=False)),
            win_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__final=True, efforts__game__test_match=False)),
            coalition_count=Count('efforts', filter=Q(efforts__win=True, efforts__game__coalition_win=True, efforts__game__final=True, efforts__game__test_match=False))
        )
        
        # Filter players who have enough efforts (before doing the annotation)
        queryset = queryset.filter(total_efforts__gte=game_threshold)


        # Annotate with win_rate after filtering
        queryset = queryset.annotate(
            win_rate=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    (Cast(F('win_count'), FloatField()) - ( Cast(F('coalition_count'), FloatField()) / 2 )) / Cast(F('total_efforts'), FloatField()) * 100,  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            ),
            tourney_points=Case(
                When(total_efforts=0, then=Value(0)),
                default=ExpressionWrapper(
                    Cast(F('win_count'), FloatField()) - ( Cast(F('coalition_count'), FloatField()) / 2 ),  # Win rate as percentage
                    output_field=FloatField()
                ),
                output_field=FloatField()
            )
        )

        return queryset

    @classmethod
    def leaderboard(cls, effort_qs, top_quantity=False, limit=5, game_threshold=10, as_json=False, link_builder=None):
        """
        Get the players with the highest winrate (or most wins for top_quantity) from the effort_qs
        The limit is how many players will be displayed.
        The game theshold is how many games a player needs to play to qualify.
        link_builder: optional callable(profile) -> str URL. Defaults to player-detail.
        """
        queryset = cls._leaderboard_annotated(effort_qs, game_threshold=game_threshold)
        return cls._leaderboard_finish(queryset, top_quantity=top_quantity, limit=limit,
                                       as_json=as_json, link_builder=link_builder)

    @classmethod
    def leaderboard_pair(cls, effort_qs, limit=5, game_threshold=10, as_json=False,
                         link_builder=None):
        """Both boards ("top" by win rate, "most" by tourney points) from ONE annotated
        queryset. Equivalent to calling leaderboard() twice with the same game_threshold,
        but builds the expensive annotation once. Returns (top, most).

        Only valid when both boards use the SAME game_threshold -- a different threshold
        changes the HAVING filter, making them genuinely different queries. Callers with
        mismatched thresholds must keep using leaderboard() twice.

        Evaluates the aggregate ONCE and sorts the two orderings in Python. Slicing the
        same queryset twice would not help: querysets are lazy, so each slice re-executes
        the SQL. The row set here is small (the threshold-qualifying players, from which
        only `limit` are shown), so in-memory sorting is far cheaper than a second
        aggregate over the whole effort table.
        """
        queryset = cls._leaderboard_annotated(effort_qs, game_threshold=game_threshold)
        rows = list(queryset)
        top = cls._leaderboard_finish_rows(
            sorted(rows, key=lambda o: (-o.win_rate, -o.total_efforts, o.pk))[:limit],
            as_json=as_json, link_builder=link_builder)
        most = cls._leaderboard_finish_rows(
            sorted(rows, key=lambda o: (-o.tourney_points, -o.win_rate, o.pk))[:limit],
            as_json=as_json, link_builder=link_builder)
        return top, most

    @classmethod
    def _leaderboard_finish(cls, queryset, top_quantity=False, limit=5, as_json=False,
                            link_builder=None):
        """Order, slice and materialize an annotated board queryset."""
        # Order the queryset
        # `pk` is a deterministic final tie-break: without it, rows with equal
        # win_rate/tourney_points come back in whatever order the database happens to
        # produce, which shifts when the fetched column set changes. Matches the
        # leaderboard_pair() sort keys so both paths agree.
        if top_quantity:
            queryset = queryset.order_by('-tourney_points', '-win_rate', 'pk')
        else:
            queryset = queryset.order_by('-win_rate', '-total_efforts', 'pk')

        queryset = queryset[:limit]

        return cls._leaderboard_finish_rows(list(queryset), as_json=as_json,
                                            link_builder=link_builder)

    @classmethod
    def _leaderboard_finish_rows(cls, results, as_json=False, link_builder=None):
        """Decorate already-ordered, already-sliced board rows (link + optional JSON)."""
        for profile in results:
            if link_builder:
                profile.leaderboard_link = link_builder(profile)
            else:
                profile.leaderboard_link = reverse('player-detail', kwargs={'slug': profile.slug})

        # Return as JSON if requested
        if as_json:
            return [
                {
                    'title': profile.display_name or profile.discord,
                    'win_rate': round(profile.win_rate, 2),
                    'tourney_points': round(profile.tourney_points, 2),
                    'total_efforts': profile.total_efforts,
                    'url': profile.get_absolute_url(),
                    'slug': profile.slug,
                    'image_url': profile.image.url if profile.image else None,
                }
                for profile in results
            ]

        return results




class PlayerBookmark(models.Model):
    player = models.ForeignKey(Profile, on_delete=models.CASCADE)
    friend = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='followers')
    public = models.BooleanField(default=False)
    date_posted = models.DateTimeField(default=timezone.now)
    def __str__(self):
        return f"{self.player.name} > {self.friend.name}"


def get_first_theme():
    # This will return the first Theme object, or None if no Theme objects exist
    return Theme.objects.first()

class Website(models.Model):
    site_title = models.CharField(max_length=255, default="Root Database")
    default_theme = models.ForeignKey(Theme, on_delete=models.SET_NULL, null=True, blank=True)
    game_threshold = models.IntegerField(default=10)
    player_threshold = models.IntegerField(default=5)
    global_message = models.CharField(max_length=400, null=True, blank=True)
    message_type = models.CharField(max_length=15 , default=MessageChoices.INFO, choices=MessageChoices.choices)
    woodland_warriors_invite = models.CharField(max_length=100, null=True, blank=True)
    french_root_invite = models.CharField(max_length=100, null=True, blank=True)
    weird_root_invite = models.CharField(max_length=100, null=True, blank=True)
    rdb_feedback_invite = models.CharField(max_length=100, null=True, blank=True)
    date_modified = models.DateTimeField(auto_now=True)
    last_law_check = models.DateTimeField(null=True, blank=True)
    primary_discord_guild = models.ForeignKey(DiscordGuild, on_delete=models.SET_NULL, null=True, blank=True)

    @classmethod
    def get_singular_instance(cls):
        # This will return the first instance or create one if none exists
        obj, created = cls.objects.get_or_create(pk=1)  # You could use any constant key, like '1'
        return obj
    
    def __str__(self):
        return "Website Configuration"
    
class DailyUserVisit(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.localdate)

    class Meta:
        unique_together = ('profile', 'date')
        verbose_name = 'Daily User Visit'
        verbose_name_plural = 'Daily User Visits'

    def __str__(self):
        return f"{self.profile.discord} - {self.date}"


class BotUsage(models.Model):
    """Per-(guild, user, command) invocation count for the Discord bot. Stores raw
    Discord snowflake strings (most bot users have no site Profile), incremented
    atomically from a Celery task. guild_id is null for DM interactions."""
    guild_id = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    user_id = models.CharField(max_length=32, db_index=True)
    command = models.CharField(max_length=50)
    count = models.PositiveIntegerField(default=0)
    first_used = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('guild_id', 'user_id', 'command')
        verbose_name = 'Bot Usage'
        verbose_name_plural = 'Bot Usage'

    def __str__(self):
        return f"/{self.command} by {self.user_id} in {self.guild_id or 'DM'} ×{self.count}"


class BotBlacklist(models.Model):
    """A blocked Discord user or guild. When a matching user or guild triggers any
    bot interaction, it's refused. Keyed by raw snowflake string + kind."""
    class Kind(models.TextChoices):
        USER = 'user', 'User'
        GUILD = 'guild', 'Guild'

    kind = models.CharField(max_length=8, choices=Kind.choices)
    discord_id = models.CharField(max_length=32, help_text="The user id or guild id, per kind.")
    reason = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('kind', 'discord_id')
        verbose_name = 'Bot Blacklist Entry'
        verbose_name_plural = 'Bot Blacklist'

    def __str__(self):
        return f"{self.get_kind_display()} {self.discord_id}{'' if self.active else ' (inactive)'}"

    @classmethod
    def is_blocked(cls, user_id=None, guild_id=None):
        """True if the given user or guild is actively blocked."""
        q = models.Q()
        if user_id:
            q |= models.Q(kind=cls.Kind.USER, discord_id=user_id)
        if guild_id:
            q |= models.Q(kind=cls.Kind.GUILD, discord_id=guild_id)
        if not q:
            return False
        return cls.objects.filter(q, active=True).exists()


class Changelog(models.Model):
    version = models.CharField(max_length=50, unique=True) 
    title = models.CharField(max_length=200, blank=True)
    date = models.DateField(default=timezone.now)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=changelog_image_upload_path, blank=True, null=True)
    source_hash = models.CharField(max_length=65, blank=True, editable=False)
    slug = models.SlugField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ['-date', 'id']
    
    def __str__(self):
        return f"{self.version} - {self.date}"

    def save(self, *args, **kwargs):
        field_name = 'image'
        
        # Check if the instance already exists (i.e., is not a new object)
        if self.pk:
            try:
                old_instance = Changelog.objects.get(pk=self.pk)
                old_image = getattr(old_instance, field_name)
                new_image = getattr(self, field_name)
                
                # If the image has changed, delete the old one(s)
                if old_image and old_image != new_image:
                    delete_old_image(old_image)
            except Changelog.DoesNotExist:
                # The object does not exist yet, nothing to delete
                pass

        super().save(*args, **kwargs)


class ChangelogEntry(models.Model):
    CATEGORY_CHOICES = [
        ('feature', 'New Feature'),
        ('improvement', 'Improvement'),
        ('bugfix', 'Bug Fix'),
        ('breaking', 'Breaking Change'),
        ('issues', 'Known Issue'),
    ]

    CATEGORY_ORDER = {
        'feature': 0,
        'improvement': 1,
        'bugfix': 2,
        'breaking': 3,
        'issues': 4,
    }

    changelog = models.ForeignKey(Changelog, related_name='entries', on_delete=models.CASCADE)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    category_order = models.PositiveSmallIntegerField(editable=False)
    description = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['category_order', 'order', 'id']

    
    def __str__(self):
        return f"{self.get_category_display()}: {self.description[:50]}"
    
    def save(self, *args, **kwargs):
        self.category_order = self.CATEGORY_ORDER.get(self.category, 99)
        super().save(*args, **kwargs)


class DiscordGuildJoinRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        COMPLETED = "completed", "Completed"

    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="guild_join_requests",
    )
    guild = models.ForeignKey(
        DiscordGuild,
        on_delete=models.CASCADE,
        related_name="join_requests",
    )

    request_message = models.TextField()
    agreement_message = models.TextField()
    acknowledgement = models.BooleanField(default=False)
    

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    moderator_message = models.TextField(
        blank=True,
        null=True,
        help_text="Optional message shown to the user when approved/rejected"
    )
    moderator_note = models.TextField(
        blank=True,
        null=True,
        help_text="Internal note visible only to moderators"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("profile", "guild")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.profile} → {self.guild} ({self.status})"


    def clean(self):
        if not self.acknowledgement:
            raise ValidationError("You must acknowledge the server rules.")
        
    def approve(self):
        if self.status != self.Status.PENDING:
            raise ValueError("Only pending requests can be approved.")
        self.status = self.Status.APPROVED
        self.save(update_fields=["status"])

    def reject(self):
        if self.status != self.Status.PENDING:
            raise ValueError("Only pending requests can be rejected.")
        self.status = self.Status.REJECTED
        self.save(update_fields=["status"])

    def complete(self):
        if self.status != self.Status.APPROVED:
            raise ValueError("Only approved requests can be completed.")
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status"])



class UserNotification(models.Model):
    """
    Persistent notification system for users.
    Stores dismissible notifications that appear in the message bar.
    """
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    message = models.TextField()
    message_type = models.CharField(
        max_length=20,
        choices=MessageChoices.choices,
        default=MessageChoices.INFO
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_dismissed = models.BooleanField(default=False)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    # Optional: Link to related object
    related_post_id = models.IntegerField(null=True, blank=True, help_text="ID of related Post if applicable")
    related_url = models.CharField(max_length=500, null=True, blank=True, help_text="URL to navigate to")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile', 'is_dismissed']),
        ]

    def __str__(self):
        return f"Notification for {self.profile.name}: {self.message[:50]}"

    def dismiss(self):
        """Mark notification as dismissed"""
        self.is_dismissed = True
        self.dismissed_at = timezone.now()
        self.save()

    @classmethod
    def create_notification(cls, profile, message, message_type=MessageChoices.INFO, related_post=None, related_url=None, sender=None):
        """
        Helper method to create a notification.

        Args:
            profile: Profile object
            message: Notification message text
            message_type: Type of message (success, warning, danger, info)
            related_post: Optional Post object to link to
            related_url: Optional URL to link to
            sender: Optional Profile of the admin/user sending the notification
        """
        notification = cls.objects.create(
            profile=profile,
            sender=sender,
            message=message,
            message_type=message_type,
            related_post_id=related_post.id if related_post else None,
            related_url=related_url
        )
        return notification


# Stub function for old migrations that reference survey models
# Survey models have been moved to the_tavern app
def get_default_ta_days():
    """Returns default enabled days for TIME_AVAILABILITY questions (all 7 days)"""
    return ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']