"""Models owned by the Discord bot.

Everything here exists only because the bot exists: LFG threads and their
seats/rolls/drafts, schedule proposals, per-guild LFG role config, and the
bot's own usage/blacklist bookkeeping.

DiscordGuild and DiscordGuildJoinRequest deliberately live in the_gatehouse
instead. Those are *website* concerns -- guild membership, join requests and
moderation -- that happen to be keyed on Discord IDs; the bot reads them but
doesn't own them. They also carry inbound FKs from the_warroom.Tournament,
the_tavern.Survey and Website, so moving them here would make the app
dependency mutual. Imports flow one way: the_databot -> the_gatehouse.

Note the "the_gatehouse.Profile" string references below are app-qualified on
purpose -- a bare "Profile" would resolve against THIS app and silently break.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from the_gatehouse.models import DiscordGuild, validate_discord_snowflake


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
    # Profile from the_gatehouse.models, which this module also imports, so importing
    # Tournament here would be circular.
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
    players = models.ManyToManyField("the_gatehouse.Profile", blank=True, related_name="lfg_threads")
    # Who ran /lfg. Cannot be derived from `players`: Profile.Meta.ordering is
    # ['display_name'], so players.all() comes back alphabetically and .first()
    # is not the host. The host's snowflake otherwise survives only in the
    # lfg_start/lfg_cancel custom_ids, which are stripped the moment the thread
    # is created -- so it is recorded here or lost. NULL on threads created
    # before this field existed, and on tournament group threads (no host).
    host = models.ForeignKey(
        "the_gatehouse.Profile", on_delete=models.SET_NULL, null=True, blank=True,
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
        "the_gatehouse.Profile", on_delete=models.SET_NULL, null=True, blank=True,
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
        "the_gatehouse.Profile", on_delete=models.SET_NULL, null=True, blank=True,
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
        'the_gatehouse.Profile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name="schedule_proposals_made")

    # Roster SNAPSHOT taken at proposal time, so a mid-flight roster change can't
    # strand a proposal that everyone present already confirmed.
    roster = models.ManyToManyField(
        'the_gatehouse.Profile', blank=True, related_name="schedule_proposals_on_roster")
    confirmed_by = models.ManyToManyField(
        'the_gatehouse.Profile', blank=True, related_name="schedule_proposals_confirmed",
        help_text="Roster players who pressed Confirm. The proposer is seeded here "
                  "at creation — proposing IS confirming.")
    # A LIST, not a single rejecter: a "No" is now a vote rather than a
    # termination, so several players can decline the same time and the poll stays
    # open until everyone has answered. Was a nullable FK when one rejection
    # closed the proposal outright; the migration copies those rows in.
    rejected_by = models.ManyToManyField(
        'the_gatehouse.Profile', blank=True, related_name="schedule_proposals_rejected",
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


