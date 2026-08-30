import io
import json
import shutil
import tempfile
from unittest import mock, skipUnless
from PIL import Image
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth import login as auth_login
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.db.models.signals import post_save
from django.http import HttpResponse
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from kombu.exceptions import OperationalError as KombuOperationalError
from the_warroom.models import (
    Effort, Game, Match, MatchSeat, MatchSeries, PlayerGroup, Round, Stage,
    StageParticipant, Tournament, TournamentPlayer, CompetitionStatus,
)
from the_gatehouse.tasks import update_post_status
from the_keep.models import (
    StatusChoices, Faction, Map, Deck, Vagabond, Language, Law, LawGroup,
)
from the_gatehouse.models import (
    DiscordGuild, GuildLFGRole, LFGThread, Profile, ScheduleProposal,
    LFGRoll, LFGDraft, LFGDraftPick, LFGSeat, DEFAULT_PROFILE_IMAGE,
    UserNotification, MessageChoices,
)
from the_gatehouse import views
from the_gatehouse.signals import user_logged_in_handler, handle_image_resize
from the_gatehouse.services import discord_commands as dc
from the_gatehouse.services.discordservice import update_discord_avatar
from the_gatehouse.services.lfg_game import (
    rolled_components, seated_profiles, player_group_for_channel,
    picked_factions_by_profile, captains_by_seat, undrafted_pick,
    FULL_CAPTAIN_COMPLEMENT,
)
from the_gatehouse.tasks import (
    record_lfg_components_task, create_lfg_thread_task, ensure_profile_from_discord,
)
from the_gatehouse.services.time_parsing import (
    NEED_TIMEZONE, parse_user_datetime, format_discord_timestamp,
    search_timezones, valid_timezone,
    TIMEZONE_REGIONS, timezone_regions, zones_for_region, timezone_label,
    region_for_timezone, describe_timezone, format_utc_offset,
)
from the_gatehouse.services.discordservice import (build_upcoming_embed,
                                                   build_lfg_help_embed, _LFG_LINK_RE)
from the_gatehouse.services import discordservice as ds
from the_gatehouse import discord_interactions as di
from the_gatehouse.templatetags.databot_filters import lfg_body

class UpdatePostStatusTaskTest(TestCase):
    def setUp(self):
        self.six_months_ago = timezone.now() - timedelta(days=180)

        self.designer_profile = Profile.objects.create(
            discord="mirz"
        )

        # Create a faction in TESTING with no recent efforts
        self.old_faction = Faction.objects.create(
            title="Old Faction",
            animal="Fox",
            designer=self.designer_profile,
            status=StatusChoices.TESTING,
            date_updated=self.six_months_ago - timedelta(days=10)
        )

        # Create a faction in TESTING with a recent effort
        self.active_faction = Faction.objects.create(
            title="Active Faction",
            animal="Fox",
            designer=self.designer_profile,
            status=StatusChoices.TESTING,
            type=Faction.TypeChoices.MILITANT,
            date_updated=timezone.now()
        )
        self.new_game=Game.objects.create(

        )
        Effort.objects.create(
            game=self.new_game,
            faction=self.active_faction,
            date_posted=timezone.now()
        )

    def test_status_updated_to_inactive(self):
        update_post_status()  # Moves from TESTING -> DEVELOPMENT
        update_post_status()  # Moves from DEVELOPMENT -> INACTIVE

        self.old_faction.refresh_from_db()
        print("Old faction status:", self.old_faction.status)
        self.assertEqual(self.old_faction.status, StatusChoices.INACTIVE.value)

    def test_status_stays_testing_if_recent_effort(self):
        update_post_status()

        self.active_faction.refresh_from_db()
        self.assertEqual(self.active_faction.status, StatusChoices.TESTING.value)


# ── /schedule Discord command ────────────────────────────────────────────────
# Time parsing, thread->match resolution, the Match.can_schedule permission
# tiers, and the set/clear handler replies.

TZ = "America/New_York"
# A fixed "now" so date-rollover and range assertions don't drift with the clock.
NOW = datetime(2026, 8, 17, 15, 0, tzinfo=dt_timezone.utc)


class ParseUserDatetimeTests(TestCase):
    """The parser accepts absolute date+time and epoch forms, resolves dateless
    inputs to their next occurrence, and refuses what it can't pin down.

    NOW is a Monday, 11:00 EDT — so 9am has passed and 4pm hasn't, which is what
    the roll-forward cases below turn on."""

    def test_discord_timestamp_paste(self):
        when, err = parse_user_datetime("<t:1789000000:F>", None, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(int(when.timestamp()), 1789000000)

    def test_discord_timestamp_without_style_suffix(self):
        when, err = parse_user_datetime("<t:1789000000>", None, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(int(when.timestamp()), 1789000000)

    def test_bare_epoch(self):
        when, err = parse_user_datetime("1789000000", None, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(int(when.timestamp()), 1789000000)

    def test_epoch_needs_no_timezone(self):
        """An epoch is absolute, so it parses with no timezone known."""
        _when, err = parse_user_datetime("<t:1789000000:F>", None, now=NOW)
        self.assertIsNone(err)

    def test_iso_like_input(self):
        when, err = parse_user_datetime("2026-09-15 20:00", TZ, now=NOW)
        self.assertIsNone(err)
        # 20:00 EDT == 00:00 UTC the next day
        self.assertEqual(when, datetime(2026, 9, 16, 0, 0, tzinfo=dt_timezone.utc))

    def test_friendly_input(self):
        when, err = parse_user_datetime("Sep 15 2026 8pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(when, datetime(2026, 9, 16, 0, 0, tzinfo=dt_timezone.utc))

    def test_missing_year_rolls_forward(self):
        """"Mar 15" in August means next March, not one five months gone."""
        when, err = parse_user_datetime("Mar 15 8pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(when.year, 2027)

    def test_date_without_time_rejected(self):
        """dateutil would silently return midnight; that's almost always an error."""
        when, err = parse_user_datetime("Mar 15", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("time of day", err)

    def _local(self, when):
        """The parsed instant as wall-clock time in the user's own zone, which is
        what the roll-forward rules are actually expressed in."""
        return when.astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M")

    # ── dateless inputs ──────────────────────────────────────────────────────
    # A bare time, a day word and a weekday all resolve to the next occurrence of
    # what was typed. The /schedule confirm step shows the resolved date back
    # before anything is written, which is what makes the guess safe.

    def test_bare_time_still_ahead_today_stays_today(self):
        when, err = parse_user_datetime("4pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-17 16:00")

    def test_bare_time_already_past_rolls_to_tomorrow(self):
        """The gating case. A bare time also satisfies the year-rollforward's
        condition ("year" not supplied, instant is past), so if the two rolls
        aren't mutually exclusive this comes back a year out instead of a day."""
        when, err = parse_user_datetime("9am", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-18 09:00")

    def test_bare_time_keeps_minutes(self):
        when, err = parse_user_datetime("4:30pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-17 16:30")

    def test_bare_24_hour_time(self):
        when, err = parse_user_datetime("16:00", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-17 16:00")

    def test_bare_time_rolls_across_month_end(self):
        """timedelta, not .replace(day=...) — 31 Aug + 1 day has to reach September."""
        end_of_month = datetime(2026, 9, 1, 3, 0, tzinfo=dt_timezone.utc)  # 31 Aug 11pm EDT
        when, err = parse_user_datetime("9am", TZ, now=end_of_month)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-09-01 09:00")

    def test_bare_time_keeps_wall_clock_across_dst(self):
        """Rolling over the spring-forward boundary must preserve the hour the user
        typed, not shift it — ZoneInfo recomputes the offset from the wall clock."""
        before_dst = datetime(2026, 3, 7, 15, 0, tzinfo=dt_timezone.utc)  # 10am EST
        when, err = parse_user_datetime("9am", TZ, now=before_dst)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-03-08 09:00")

    def test_weekday_resolves_forward(self):
        """NOW is a Monday, so "friday" is four days out — NOT tomorrow. Weekdays
        and bare times are indistinguishable to _supplied_fields (both report just
        {"hour"}), so this pins the lexical weekday check."""
        when, err = parse_user_datetime("friday 4pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-21 16:00")

    def test_weekday_naming_today_still_ahead_stays_today(self):
        when, err = parse_user_datetime("monday 4pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-17 16:00")

    def test_weekday_naming_today_already_past_rolls_a_week(self):
        """A past weekday means the one next week, not next year — the same gating
        trap as the bare-time case, one level deeper."""
        when, err = parse_user_datetime("monday 9am", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-24 09:00")

    def test_tomorrow_keyword(self):
        when, err = parse_user_datetime("tomorrow 4pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-18 16:00")

    def test_today_keyword(self):
        when, err = parse_user_datetime("today 4pm", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(self._local(when), "2026-08-17 16:00")

    def test_today_with_past_time_rejected(self):
        """"today" pins the date, so there's nothing to roll to. _MAX_PAST is a 24h
        grace, so without an explicit check this would be accepted as valid."""
        when, err = parse_user_datetime("today 9am", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("already passed today", err)

    def test_day_word_with_explicit_date_rejected(self):
        for text in ("tomorrow Mar 15 8pm", "tomorrow friday 4pm"):
            with self.subTest(text=text):
                when, err = parse_user_datetime(text, TZ, now=NOW)
                self.assertIsNone(when)
                self.assertIn("not both", err)

    def test_missing_timezone_returns_sentinel(self):
        when, err = parse_user_datetime("Sep 15 2026 8pm", None, now=NOW)
        self.assertIsNone(when)
        self.assertEqual(err, NEED_TIMEZONE)

    def test_unknown_timezone_returns_sentinel(self):
        when, err = parse_user_datetime("Sep 15 2026 8pm", "Not/AZone", now=NOW)
        self.assertIsNone(when)
        self.assertEqual(err, NEED_TIMEZONE)

    def test_unparseable_input(self):
        when, err = parse_user_datetime("sometime next week-ish", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("couldn't read", err)

    def test_empty_input(self):
        when, err = parse_user_datetime("", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertTrue(err)

    def test_past_rejected(self):
        when, err = parse_user_datetime("2020-01-01 20:00", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("past", err)

    def test_far_future_rejected(self):
        when, err = parse_user_datetime("2099-01-01 20:00", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("two years", err)

    def test_dst_offset_applied(self):
        """A winter date uses EST (-05:00), not the summer EDT offset."""
        when, err = parse_user_datetime("2027-01-15 20:00", TZ, now=NOW)
        self.assertIsNone(err)
        self.assertEqual(when, datetime(2027, 1, 16, 1, 0, tzinfo=dt_timezone.utc))

    def test_year_digits_not_read_as_epoch(self):
        """A bare "2026" must not be mistaken for a unix timestamp."""
        when, err = parse_user_datetime("2026", None, now=NOW)
        self.assertIsNone(when)
        self.assertEqual(err, NEED_TIMEZONE)

    def test_format_discord_timestamp(self):
        when = datetime(2026, 9, 16, 0, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(
            format_discord_timestamp(when),
            f"<t:{int(when.timestamp())}:F> (<t:{int(when.timestamp())}:R>)",
        )

    def test_timezone_helpers(self):
        self.assertTrue(valid_timezone(TZ))
        self.assertFalse(valid_timezone("Not/AZone"))
        self.assertFalse(valid_timezone(""))
        self.assertIn(TZ, search_timezones("new york"))
        self.assertLessEqual(len(search_timezones("")), 25)
        # An empty query should still be useful, not alphabetical noise.
        self.assertTrue(search_timezones(""))


class TimezoneRegionTests(TestCase):
    """The curated region/city lists behind the /schedule timezone picker."""

    def test_every_curated_zone_is_a_real_iana_zone(self):
        """A typo here would be a Discord 400 at runtime, not a visible error."""
        for region in TIMEZONE_REGIONS:
            for zone, _label in region["zones"]:
                self.assertTrue(valid_timezone(zone), zone)

    def test_no_region_exceeds_the_select_cap(self):
        for region in TIMEZONE_REGIONS:
            self.assertLessEqual(len(region["zones"]), 25, region["key"])

    def test_no_duplicate_zones_within_or_across_regions(self):
        zones = [z for r in TIMEZONE_REGIONS for z, _l in r["zones"]]
        self.assertEqual(len(zones), len(set(zones)))

    def test_region_keys_are_short_enough_for_a_custom_id(self):
        for region in TIMEZONE_REGIONS:
            self.assertLessEqual(len(region["key"]), 2)

    def test_region_for_timezone_finds_curated_zones(self):
        self.assertEqual(region_for_timezone("America/New_York"), "AM")
        self.assertEqual(region_for_timezone("Europe/Paris"), "EU")
        self.assertEqual(region_for_timezone("Asia/Tokyo"), "AP")
        self.assertEqual(region_for_timezone("UTC"), "UT")

    def test_region_for_timezone_falls_back_by_prefix(self):
        """A zone set via the `timezone` option still pre-selects a region."""
        self.assertEqual(region_for_timezone("Africa/Nairobi"), "EU")
        self.assertEqual(region_for_timezone("Pacific/Chatham"), "AP")
        self.assertEqual(region_for_timezone("Not/AZone"), "UT")

    def test_region_for_timezone_handles_none(self):
        self.assertIsNone(region_for_timezone(None))
        self.assertIsNone(region_for_timezone(""))

    def test_every_region_is_reachable(self):
        """No region may become unselectable — each must own at least one zone
        that resolves back to it."""
        reachable = {region_for_timezone(z)
                     for r in TIMEZONE_REGIONS for z, _l in r["zones"]}
        self.assertEqual(reachable, {r["key"] for r in TIMEZONE_REGIONS})

    def test_zones_for_region_unknown_key(self):
        self.assertEqual(zones_for_region("XX"), [])
        self.assertTrue(zones_for_region("AM"))

    def test_timezone_regions_matches_the_underlying_list(self):
        self.assertEqual(timezone_regions(), TIMEZONE_REGIONS)

    def test_format_utc_offset_reflects_dst(self):
        winter = datetime(2027, 1, 15, 20, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(format_utc_offset(TZ, NOW), "UTC-4")
        self.assertEqual(format_utc_offset(TZ, winter), "UTC-5")
        # Southern hemisphere moves the other way.
        self.assertEqual(format_utc_offset("Australia/Sydney", NOW), "UTC+10")
        self.assertEqual(format_utc_offset("Australia/Sydney", winter), "UTC+11")

    def test_format_utc_offset_handles_partial_hours(self):
        self.assertEqual(format_utc_offset("Asia/Kolkata", NOW), "UTC+5:30")
        self.assertEqual(format_utc_offset("Asia/Kathmandu", NOW), "UTC+5:45")
        self.assertEqual(format_utc_offset("America/St_Johns", NOW), "UTC-2:30")

    def test_format_utc_offset_of_utc_is_bare(self):
        self.assertEqual(format_utc_offset("UTC", NOW), "UTC")

    def test_format_utc_offset_of_invalid_zone_is_empty(self):
        self.assertEqual(format_utc_offset("Not/AZone", NOW), "")

    def test_describe_timezone_uses_the_friendly_label(self):
        self.assertEqual(describe_timezone(TZ, at=NOW), "New York (US Eastern) — UTC-4")

    def test_describe_timezone_of_uncurated_zone_uses_the_iana_name(self):
        self.assertEqual(describe_timezone("Asia/Kathmandu", at=NOW),
                         "Kathmandu — UTC+5:45")
        self.assertEqual(describe_timezone("Africa/Nairobi", at=NOW),
                         "Africa/Nairobi — UTC+3")

    def test_describe_timezone_does_not_stutter_on_utc(self):
        self.assertEqual(describe_timezone("UTC", at=NOW),
                         "UTC (Coordinated Universal Time)")

    def test_describe_timezone_of_invalid_zone_is_empty(self):
        self.assertEqual(describe_timezone("Not/AZone"), "")
        self.assertEqual(describe_timezone(None), "")

    def test_timezone_label_falls_back_to_the_iana_name(self):
        self.assertEqual(timezone_label(TZ), "New York (US Eastern)")
        self.assertEqual(timezone_label("Africa/Nairobi"), "Africa/Nairobi")
        self.assertEqual(timezone_label(None), "")

    def test_autocomplete_common_zones_stay_in_the_curated_lists(self):
        """search_timezones floats a hardcoded `common` list; if a zone is dropped
        from the picker the two would silently disagree."""
        curated = {z for r in TIMEZONE_REGIONS for z, _l in r["zones"]}
        for zone in search_timezones(""):
            self.assertIn(zone, curated)


class ScheduleInputMarkerTests(TestCase):
    """The subtext line that carries the user's typed time between interactions."""

    def _roundtrip(self, text):
        content = "Pick a region.\n" + di._schedule_input_line(text)
        return di._schedule_input_text({"message": {"content": content}})

    def test_plain_text_roundtrips(self):
        self.assertEqual(self._roundtrip("Mar 15 8pm"), "Mar 15 8pm")

    def test_colons_survive(self):
        """The custom_id codec is ':'-delimited; this carrier must not be."""
        self.assertEqual(self._roundtrip("2026-03-15 20:00"), "2026-03-15 20:00")

    def test_discord_timestamp_paste_survives(self):
        self.assertEqual(self._roundtrip("<t:1789000000:F>"), "<t:1789000000:F>")

    def test_backticks_are_stripped(self):
        self.assertEqual(self._roundtrip("Mar 15 `8pm`"), "Mar 15 8pm")

    def test_long_input_is_truncated(self):
        self.assertLessEqual(len(self._roundtrip("a" * 300)), 200)

    def test_a_forged_marker_line_cannot_hijack_the_value(self):
        """Newlines are collapsed, so a fake marker ends up inside the real one
        rather than replacing it."""
        forged = "Mar 15 8pm\n-# From your input: `HACKED`"
        self.assertNotEqual(self._roundtrip(forged), "HACKED")

    def test_missing_marker_returns_empty(self):
        self.assertEqual(di._schedule_input_text({"message": {"content": "nothing"}}), "")
        self.assertEqual(di._schedule_input_text({}), "")


class ScheduleFixtureMixin:
    """A tournament with one stage, round, group, series and match, plus a guild
    and a seated player."""

    def build(self, recording_access=Tournament.RecordingAccessTypes.SCHEDULED,
              thread_id="555000111", group_name="Group A", populate_group=False):
        self.guild = DiscordGuild.objects.create(guild_id="900100", name="Sched Guild")
        self.designer = Profile.objects.create(discord="designer", discord_id="1")
        self.player = Profile.objects.create(discord="player", discord_id="2")
        self.outsider = Profile.objects.create(discord="outsider", discord_id="3")
        self.group_mod = Profile.objects.create(discord="groupmod", discord_id="4")

        self.tournament = Tournament.objects.create(
            name="Sched Tournament", guild=self.guild, designer=self.designer,
            recording_access=recording_access,
        )
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name=group_name,
            discord_thread=f"https://discord.com/channels/{self.guild.guild_id}/{thread_id}",
            group_moderator=self.group_mod,
        )
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)
        self.match = Match.objects.create(round=self.round, series=self.series)

        # Seat the player: TournamentPlayer -> StageParticipant -> MatchSeat.
        self.tournament_player = TournamentPlayer.objects.create(
            tournament=self.tournament, profile=self.player)
        self.participant = StageParticipant.objects.create(
            stage=self.stage, tournament_player=self.tournament_player)
        MatchSeat.objects.create(
            series=self.series, stage_participant=self.participant, seat_number=1)

        # The consensus flow polls PlayerGroup.tournament_players, which the legacy
        # tests deliberately leave empty — an empty roster keeps /schedule on its
        # original single-confirm path. Opt in to get a real roster.
        if populate_group:
            self.teammate = Profile.objects.create(discord="teammate", discord_id="5")
            self.teammate_tp = TournamentPlayer.objects.create(
                tournament=self.tournament, profile=self.teammate)
            self.group.tournament_players.add(self.tournament_player, self.teammate_tp)


class MatchCanScheduleTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def test_seated_player_allowed(self):
        permission = self.match.can_schedule(self.player)
        self.assertTrue(permission)
        self.assertEqual(permission.reason, 'participant')

    def test_group_moderator_allowed(self):
        permission = self.match.can_schedule(self.group_mod)
        self.assertTrue(permission)
        self.assertEqual(permission.reason, 'group_moderator')

    def test_designer_allowed(self):
        permission = self.match.can_schedule(self.designer)
        self.assertTrue(permission)
        self.assertEqual(permission.reason, 'organizer')

    def test_tournament_moderator_allowed(self):
        moderator = Profile.objects.create(discord="mod", discord_id="5")
        self.tournament.moderators.add(moderator)
        permission = self.match.can_schedule(moderator)
        self.assertTrue(permission)
        self.assertEqual(permission.reason, 'organizer')

    def test_admin_allowed(self):
        # Profile.admin is derived from the group code ("A" = admin).
        admin = Profile.objects.create(discord="admin", discord_id="6", group="A")
        permission = self.match.can_schedule(admin)
        self.assertTrue(permission)
        self.assertEqual(permission.reason, 'admin')

    def test_outsider_denied(self):
        self.assertFalse(self.match.can_schedule(self.outsider))

    def test_seated_player_denied_by_recording_access(self):
        """The MODERATORS tier means players can't schedule their own match."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        self.match.refresh_from_db()
        self.assertFalse(self.match.can_schedule(self.player))

    def test_group_moderator_allowed_despite_restrictive_tier(self):
        """Group moderators bypass the recording_access tier, as in Game.can_edit."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        self.match.refresh_from_db()
        self.assertTrue(self.match.can_schedule(self.group_mod))

    def test_unsaved_profile_denied(self):
        self.assertFalse(self.match.can_schedule(Profile()))


class MatchForThreadTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.thread_id = "555000111"

    def test_resolves_by_thread_id(self):
        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_other_guild_cannot_reach_match(self):
        """The same thread id in a different guild must not resolve."""
        other = DiscordGuild.objects.create(guild_id="900999", name="Other")
        match, err = di._match_for_thread(self.thread_id, other.guild_id)
        self.assertIsNone(match)
        self.assertTrue(err)

    def test_match_with_game_excluded(self):
        self.match.game = Game.objects.create()
        self.match.save(update_fields=["game"])
        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(match)
        self.assertTrue(err)

    def test_completed_match_excluded(self):
        self.match.status = CompetitionStatus.COMPLETED
        self.match.save(update_fields=["status"])
        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(match)
        self.assertTrue(err)

    def test_picks_first_unscheduled_match_in_series(self):
        self.series.number_of_games = 3
        self.series.save(update_fields=["number_of_games"])
        second = Match.objects.create(round=self.round, series=self.series)
        self.match.scheduled_time = timezone.now() + timedelta(days=1)
        self.match.save(update_fields=["scheduled_time"])

        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(err)
        self.assertEqual(match, second)

    def test_all_scheduled_falls_back_to_first_as_reschedule(self):
        self.match.scheduled_time = timezone.now() + timedelta(days=1)
        self.match.save(update_fields=["scheduled_time"])
        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_unlinked_thread_without_title_errors(self):
        match, err = di._match_for_thread("999888777", self.guild.guild_id)
        self.assertIsNone(match)
        self.assertIn("couldn't find", err.lower())

    def test_title_fallback_matches_group_name(self):
        """With no thread URL saved, the thread's title matches the GROUP name."""
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_title_fallback_is_case_and_space_insensitive(self):
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="  group   a  ")
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_title_fallback_strips_emoji_prefix(self):
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="🏆 Group A")
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_title_fallback_ignores_series_name(self):
        """MatchSeries.name is not a lookup key — only the group name is."""
        self.group.discord_thread = ""
        self.group.name = "Different Group"
        self.group.save(update_fields=["discord_thread", "name"])
        self.series.name = "Group A"
        self.series.save(update_fields=["name"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.assertIsNone(match)
        self.assertTrue(err)

    def test_title_fallback_links_the_thread_to_the_group(self):
        """A title match is remembered, so later lookups (and /seating, /pick and
        the capture tasks, which have no title fallback of their own) resolve by
        id instead of re-running the guess."""
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])
        di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.group.refresh_from_db()
        self.assertEqual(
            self.group.discord_thread,
            f"https://discord.com/channels/{self.guild.guild_id}/999888777")

    def test_ambiguous_title_refuses_to_guess(self):
        """Group names are unique per round, so a title can match several groups."""
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])
        round2 = Round.objects.create(stage=self.stage, round_number=2)
        group2 = PlayerGroup.objects.create(
            round=round2, group_number=1, name="Group A")
        series2 = MatchSeries.objects.create(round=round2, player_group=group2)
        Match.objects.create(round=round2, series=series2)

        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.assertIsNone(match)
        self.assertIn("several", err.lower())
        # Nothing was linked: an ambiguous title must not pick a winner by
        # writing one, which would cement the wrong group permanently.
        self.group.refresh_from_db()
        group2.refresh_from_db()
        self.assertEqual(self.group.discord_thread, "")
        self.assertEqual(group2.discord_thread, "")

    def test_title_fallback_skips_groups_that_are_already_linked(self):
        """A group linked to a DIFFERENT thread must not be reachable by name.

        Otherwise a second thread sharing the group's title — a duplicate, a
        rename, a scratch thread — schedules that group's match, and the
        several-groups guard doesn't fire because only one group matches."""
        self.group.discord_thread = (
            f"https://discord.com/channels/{self.guild.guild_id}/111222333")
        self.group.save(update_fields=["discord_thread"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.assertIsNone(match)
        self.assertIn("couldn't find", err.lower())

    def test_title_fallback_ignores_a_linked_namesake(self):
        """With one linked and one unlinked group of the same name, the unlinked
        one is matched instead of tripping the ambiguity guard."""
        self.group.discord_thread = (
            f"https://discord.com/channels/{self.guild.guild_id}/111222333")
        self.group.save(update_fields=["discord_thread"])
        round2 = Round.objects.create(stage=self.stage, round_number=2)
        unlinked = PlayerGroup.objects.create(
            round=round2, group_number=1, name="Group A", discord_thread="")
        series2 = MatchSeries.objects.create(round=round2, player_group=unlinked)
        expected = Match.objects.create(round=round2, series=series2)

        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="Group A")
        self.assertIsNone(err)
        self.assertEqual(match, expected)

    def test_blank_group_name_not_matched_by_blank_title(self):
        self.group.discord_thread = ""
        self.group.name = ""
        self.group.save(update_fields=["discord_thread", "name"])
        match, err = di._match_for_thread(
            "999888777", self.guild.guild_id, channel_name="   ")
        self.assertIsNone(match)
        self.assertTrue(err)

    def test_non_numeric_channel_id_rejected(self):
        match, err = di._match_for_thread("not-an-id", self.guild.guild_id)
        self.assertIsNone(match)
        self.assertTrue(err)


class EnsureProfileFromDiscordTests(TestCase):
    """Resolving a Discord user to a Profile.

    Most profiles here were created manually and carry no discord_id; the point of
    this helper is that their owner CLAIMS them on first use, exactly as the site
    login does. The order matters: an unlinked handle is claimable, a linked one is
    somebody's account."""

    LINKED_ID = "111111111111111111"
    OTHER_ID = "222222222222222222"

    # ── order: the verified id wins ──
    def test_an_existing_discord_id_matches_first(self):
        mine = Profile.objects.create(discord="mine", discord_id=self.LINKED_ID)
        Profile.objects.create(discord="someoneelse")  # unlinked, different handle
        got = ensure_profile_from_discord(self.LINKED_ID, "someoneelse", None)
        self.assertEqual(got.pk, mine.pk)

    def test_someone_elses_handle_does_not_beat_your_own_id(self):
        """Otherwise a user with a profile is handed a different one every call, and
        it isn't even claimed — so it recurs forever."""
        mine = Profile.objects.create(discord="mine", discord_id=self.LINKED_ID)
        other = Profile.objects.create(discord="otherhandle")
        got = ensure_profile_from_discord(self.LINKED_ID, "otherhandle", None)
        self.assertEqual(got.pk, mine.pk)
        other.refresh_from_db()
        self.assertIsNone(other.discord_id)

    # ── the impersonation case ──
    def test_a_linked_profile_is_never_returned_for_another_snowflake(self):
        victim = Profile.objects.create(discord="victim", discord_id=self.LINKED_ID)
        got = ensure_profile_from_discord(self.OTHER_ID, "victim", None)
        self.assertNotEqual(got.pk, victim.pk)
        self.assertEqual(got.discord_id, self.OTHER_ID)
        victim.refresh_from_db()
        self.assertEqual(victim.discord_id, self.LINKED_ID)

    def test_that_case_creates_a_profile_with_a_suffixed_handle(self):
        """`discord` is unique, so the new row can't reuse the taken handle."""
        Profile.objects.create(discord="victim", discord_id=self.LINKED_ID)
        got = ensure_profile_from_discord(self.OTHER_ID, "victim", None)
        self.assertNotEqual(got.discord, "victim")
        self.assertIn(self.OTHER_ID, got.discord)

    # ── the claim, which must keep working ──
    def test_an_unlinked_handle_is_claimed(self):
        unclaimed = Profile.objects.create(discord="claimme")
        got = ensure_profile_from_discord(self.LINKED_ID, "claimme", None)
        self.assertEqual(got.pk, unclaimed.pk)
        unclaimed.refresh_from_db()
        self.assertEqual(unclaimed.discord_id, self.LINKED_ID)

    def test_the_claim_is_case_insensitive(self):
        unclaimed = Profile.objects.create(discord="MixedCase")
        got = ensure_profile_from_discord(self.LINKED_ID, "mixedcase", None)
        self.assertEqual(got.pk, unclaimed.pk)

    def test_the_claim_does_not_disturb_display_name_or_image(self):
        """The claim writes with update_fields; a bare save() would re-derive
        display_name and can delete the profile's avatar."""
        unclaimed = Profile.objects.create(discord="claimme2", display_name="Real Name")
        ensure_profile_from_discord(self.LINKED_ID, "claimme2", "Discord Name")
        unclaimed.refresh_from_db()
        self.assertEqual(unclaimed.display_name, "Real Name")

    # ── no username ──
    def test_no_username_matches_by_id(self):
        mine = Profile.objects.create(discord="mine", discord_id=self.LINKED_ID)
        self.assertEqual(
            ensure_profile_from_discord(self.LINKED_ID, None, None).pk, mine.pk)

    def test_no_username_creates_with_an_id_derived_handle(self):
        got = ensure_profile_from_discord(self.LINKED_ID, None, "Someone")
        self.assertEqual(got.discord_id, self.LINKED_ID)
        self.assertIn(self.LINKED_ID, got.discord)

    def test_a_missing_discord_id_returns_none(self):
        self.assertIsNone(ensure_profile_from_discord(None, "anything", None))

    # ── the race fallback ──
    def test_the_integrity_error_fallback_recovers_by_id(self):
        existing = Profile.objects.create(discord="raced", discord_id=self.LINKED_ID)
        with mock.patch.object(Profile.objects, "create",
                               side_effect=IntegrityError("raced")):
            got = ensure_profile_from_discord(self.LINKED_ID, "brandnew", None)
        self.assertEqual(got.pk, existing.pk)

    def test_the_integrity_error_fallback_will_not_return_a_linked_profile(self):
        """Same unlinked-only rule as the main path — this fallback is the second
        door into the impersonation bug."""
        Profile.objects.create(discord="taken", discord_id=self.LINKED_ID)
        with mock.patch.object(Profile.objects, "create",
                               side_effect=IntegrityError("raced")):
            got = ensure_profile_from_discord(self.OTHER_ID, "taken", None)
        self.assertIsNone(got)


class ScheduleHandlerTests(ScheduleFixtureMixin, TestCase):
    """The handler is called directly (rather than through the signed endpoint),
    matching how the interaction code is structured: handlers take `data` with the
    dispatcher's underscore-prefixed context keys already stashed."""

    def setUp(self):
        self.build()
        self.thread_id = "555000111"

    # Distinct from None, so a test can explicitly pass guild=None (a DM) without
    # it being read as "use the default".
    UNSET = object()

    def _data(self, time="Sep 15 2026 8pm", tz=None, author=UNSET, channel=UNSET,
              guild=UNSET, channel_name=None):
        # time=None omits the option entirely, which is how Discord sends an
        # unfilled optional option — i.e. the clear flow.
        options = [] if time is None else [{"name": "time", "value": time}]
        if tz:
            options.append({"name": "timezone", "value": tz})
        return {
            "name": "schedule",
            "options": options,
            "_guild_id": self.guild.guild_id if guild is self.UNSET else guild,
            "_channel_id": self.thread_id if channel is self.UNSET else channel,
            "_channel_name": channel_name,
            "_author_id": self.player.discord_id if author is self.UNSET else author,
            "_author_username": "player",
        }

    def _body(self, response):
        return json.loads(response.content)

    def assertEphemeral(self, response):
        body = self._body(response)
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        return body

    def test_confirm_prompt_returned(self):
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        body = self.assertEphemeral(di._handle_schedule_command(self._data()))
        self.assertIn("Group A", body["data"]["content"])
        self.assertIn("<t:", body["data"]["content"])
        # Nothing is written until Confirm.
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_bare_time_reaches_the_confirm_prompt(self):
        """A bare time used to be refused outright. It now resolves to the next
        occurrence and gets the normal confirm prompt — the resolved date is shown
        back as a <t:...> stamp, which is what makes the guess safe."""
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(time="4pm")))
        self.assertIn("<t:", body["data"]["content"])
        self.assertNotIn("Please include a date", body["data"]["content"])
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_confirm_button_carries_owner_last(self):
        """The dispatcher owner-lock keys off the LAST custom_id arg."""
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        body = self._body(di._handle_schedule_command(self._data()))
        confirm = body["data"]["components"][0]["components"][0]
        self.assertTrue(confirm["custom_id"].endswith(f":{self.player.discord_id}"))
        self.assertLessEqual(len(confirm["custom_id"]), 100)

    def test_timezone_option_is_saved(self):
        di._handle_schedule_command(self._data(tz=TZ))
        self.player.refresh_from_db()
        self.assertEqual(self.player.timezone, TZ)

    def test_missing_timezone_prompts(self):
        """Asking must mean the region picker, not an error telling them to
        re-type the command with an option."""
        body = self.assertEphemeral(di._handle_schedule_command(self._data()))
        select = body["data"]["components"][0]["components"][0]
        self.assertEqual(select["type"], 3)  # string select
        self.assertTrue(select["custom_id"].startswith("schedule_tz_region:"))
        # The typed time has to survive to the next interaction.
        self.assertEqual(
            di._schedule_input_text({"message": {"content": body["data"]["content"]}}),
            "Sep 15 2026 8pm")

    def test_timezone_option_not_saved_when_time_is_bad(self):
        """The option and the time are one command: a bad time saves neither."""
        di._handle_schedule_command(self._data(time="whenever", tz=TZ))
        self.player.refresh_from_db()
        self.assertIsNone(self.player.timezone)

    def test_invalid_timezone_rejected(self):
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(tz="Not/AZone")))
        self.assertIn("timezone", body["data"]["content"].lower())

    def test_bad_time_rejected(self):
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(time="whenever", tz=TZ)))
        self.assertIn("couldn't read", body["data"]["content"])

    def test_unauthorized_user_rejected(self):
        self.outsider.timezone = TZ
        self.outsider.save(update_fields=["timezone"])
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(author=self.outsider.discord_id)))
        self.assertIn("not able to schedule", body["data"]["content"])
        self.assertIn("series admin", body["data"]["content"])

    def test_unknown_discord_user_gets_a_profile_and_a_permission_error(self):
        """/schedule get-or-creates so the timezone can always be saved. A brand-new
        Profile is on no roster, so the match path still refuses -- but with the
        message that says what to do about it."""
        self.assertFalse(Profile.objects.filter(discord_id="99999999").exists())
        self.outsider.timezone = TZ  # so we reach can_schedule, not the tz picker
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(author="99999999")))
        self.assertIn("not able to schedule", body["data"]["content"])
        self.assertTrue(Profile.objects.filter(discord_id="99999999").exists())

    def test_an_existing_profile_is_reused_not_duplicated(self):
        before = Profile.objects.count()
        di._handle_schedule_command(self._data())
        self.assertEqual(Profile.objects.count(), before)

    def test_a_username_cannot_take_over_a_linked_profile(self):
        """A handle matching an ALREADY-LINKED profile must never hand over that
        account — on this path it would carry their scheduling permission with it.
        (An unlinked profile IS claimable; that's the intended login-style flow.)"""
        data = self._data(author="99999999")
        data["_author_username"] = "player"  # self.player's handle, already linked
        di._handle_schedule_command(data)
        self.player.refresh_from_db()
        self.assertEqual(self.player.discord_id, "2")
        self.assertTrue(Profile.objects.filter(discord_id="99999999").exists())

    def test_an_unlinked_profile_is_claimed_by_a_matching_handle(self):
        """The flow the claim exists for: manually-created profiles are taken over
        by their owner on first use."""
        unclaimed = Profile.objects.create(discord="unclaimedhandle")
        data = self._data(author="99999999")
        data["_author_username"] = "unclaimedhandle"
        di._handle_schedule_command(data)
        unclaimed.refresh_from_db()
        self.assertEqual(unclaimed.discord_id, "99999999")

    def test_no_match_for_thread_offers_an_unlinked_suggestion(self):
        """The old dead end. Now it suggests a time that isn't linked to a game."""
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(channel="123123123")))
        content = body["data"]["content"]
        self.assertIn("isn't linked to a game on the site", content)
        self.assertNotIn("couldn't find", content.lower())

    def test_used_in_plain_channel_also_offers_a_suggestion(self):
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        data = self._data(channel="123123123")
        data["_channel_type"] = 0  # GUILD_TEXT, not a thread
        body = self.assertEphemeral(di._handle_schedule_command(data))
        self.assertIn("isn't linked to a game on the site", body["data"]["content"])

    def test_outside_guild_rejected(self):
        body = self.assertEphemeral(di._handle_schedule_command(self._data(guild=None)))
        self.assertIn("server", body["data"]["content"])

    def test_epoch_input_needs_no_timezone(self):
        future = int((timezone.now() + timedelta(days=10)).timestamp())
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(time=f"<t:{future}:F>")))
        self.assertIn("<t:", body["data"]["content"])
        self.assertNotIn("timezone", body["data"]["content"].lower())


class ScheduleConfirmTests(ScheduleFixtureMixin, TestCase):
    """The DIRECT-write Confirm path: no roster to poll, so the invoker's click
    sets the time. The consensus path (a roster exists) is covered by
    ScheduleProposalFlowTests -- see ScheduleConsensusGateTests for the gate."""

    def setUp(self):
        self.build()
        # Drop the seat so _match_roster finds nobody: with either group members
        # or seats present, Confirm now opens a proposal instead of writing.
        # That also un-seats self.player, so these payloads are owned by the GROUP
        # MODERATOR, whose can_schedule permission doesn't depend on a seat.
        MatchSeat.objects.filter(series=self.series).delete()
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.ts = int(self.when.timestamp())

    UNSET = object()

    def _payload(self, match_id=UNSET, ts=UNSET, owner=UNSET, guild=UNSET):
        return {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_confirm",
                self.match.id if match_id is self.UNSET else match_id,
                self.ts if ts is self.UNSET else ts,
                self.group_mod.discord_id if owner is self.UNSET else owner,
            )},
            "guild_id": self.guild.guild_id if guild is self.UNSET else guild,
            "token": None,
        }

    def test_confirm_writes_scheduled_time(self):
        response = di._handle_schedule_confirm(self._payload())
        body = json.loads(response.content)
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.match.refresh_from_db()
        self.assertEqual(int(self.match.scheduled_time.timestamp()), self.ts)

    def test_confirm_preserves_derived_name(self):
        """save(update_fields=...) must not re-run Match.save()'s name derivation."""
        original_name = self.match.name
        original_number = self.match.match_number
        di._handle_schedule_confirm(self._payload())
        self.match.refresh_from_db()
        self.assertEqual(self.match.name, original_name)
        self.assertEqual(self.match.match_number, original_number)

    def test_confirm_rechecks_permission(self):
        response = di._handle_schedule_confirm(
            self._payload(owner=self.outsider.discord_id))
        body = json.loads(response.content)
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_confirm_rejects_match_played_since_prompt(self):
        self.match.game = Game.objects.create()
        self.match.save(update_fields=["game"])
        response = di._handle_schedule_confirm(self._payload())
        body = json.loads(response.content)
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)

    def test_confirm_rejects_other_guild(self):
        other = DiscordGuild.objects.create(guild_id="900999", name="Other")
        response = di._handle_schedule_confirm(self._payload(guild=other.guild_id))
        body = json.loads(response.content)
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_stale_custom_id_handled(self):
        payload = {"data": {"custom_id": "schedule_confirm:1"},
                   "guild_id": self.guild.guild_id}
        response = di._handle_schedule_confirm(payload)
        self.assertEqual(json.loads(response.content)["data"].get("flags"), di.EPHEMERAL)

    def test_cancel_writes_nothing(self):
        response = di._handle_schedule_cancel(
            {"data": {"custom_id": di.encode_custom_id(
                "schedule_cancel", self.player.discord_id)}})
        body = json.loads(response.content)
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.assertEqual(body["data"]["components"], [])
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)


class MatchForThreadPreferTests(ScheduleFixtureMixin, TestCase):
    """`prefer` decides which match of a multi-game series is acted on: setting
    wants the first one still missing a time, clearing the last one that has one."""

    def setUp(self):
        self.build()
        self.thread_id = "555000111"
        self.series.number_of_games = 3
        self.series.save(update_fields=["number_of_games"])
        self.second = Match.objects.create(round=self.round, series=self.series)
        self.third = Match.objects.create(round=self.round, series=self.series)

    def test_clearing_picks_last_scheduled_match(self):
        soon = timezone.now() + timedelta(days=1)
        self.match.scheduled_time = soon
        self.match.save(update_fields=["scheduled_time"])
        self.second.scheduled_time = soon + timedelta(days=7)
        self.second.save(update_fields=["scheduled_time"])
        # self.third has no time — the set flow would target it, the clear flow
        # must not.
        match, err = di._match_for_thread(
            self.thread_id, self.guild.guild_id, prefer="scheduled")
        self.assertIsNone(err)
        self.assertEqual(match, self.second)

    def test_clearing_with_nothing_scheduled_returns_first(self):
        """Falls back to a real match so the caller can name it in the error."""
        match, err = di._match_for_thread(
            self.thread_id, self.guild.guild_id, prefer="scheduled")
        self.assertIsNone(err)
        self.assertEqual(match, self.match)

    def test_setting_still_picks_first_unscheduled(self):
        self.match.scheduled_time = timezone.now() + timedelta(days=1)
        self.match.save(update_fields=["scheduled_time"])
        match, err = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertIsNone(err)
        self.assertEqual(match, self.second)

    def test_default_prefer_is_unscheduled(self):
        """The set path's behavior must be unchanged by the new argument."""
        explicit, _ = di._match_for_thread(
            self.thread_id, self.guild.guild_id, prefer="unscheduled")
        default, _ = di._match_for_thread(self.thread_id, self.guild.guild_id)
        self.assertEqual(explicit, default)


class ScheduleTimezoneSelectTests(ScheduleFixtureMixin, TestCase):
    """The region/city selects that ask for a timezone, and the re-parse that
    follows. Component handlers take the raw payload — none of the dispatcher's
    underscore keys — and recover the typed time from the message itself."""

    TIME = "Sep 15 2026 8pm"

    def setUp(self):
        self.build()

    UNSET = object()

    def _payload(self, action, *args, values=None, time_text=UNSET, owner=UNSET,
                 guild=UNSET):
        owner = self.player.discord_id if owner is self.UNSET else owner
        text = self.TIME if time_text is self.UNSET else time_text
        content = "Pick a region."
        if text:
            content += "\n" + di._schedule_input_line(text)
        return {
            "data": {
                "custom_id": di.encode_custom_id(action, *args, owner),
                "values": values or [],
            },
            "guild_id": self.guild.guild_id if guild is self.UNSET else guild,
            "message": {"content": content},
            "token": None,
        }

    def _body(self, response):
        return json.loads(response.content)

    def _confirm_ts(self, body):
        confirm = body["data"]["components"][0]["components"][0]
        return int(di.decode_custom_id(confirm["custom_id"])[1][1])

    # ── region select ────────────────────────────────────────────────────────
    def test_region_select_renders_the_city_select(self):
        body = self._body(di._handle_schedule_tz_region(
            self._payload("schedule_tz_region", self.match.id, values=["AM"])))
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        select = body["data"]["components"][0]["components"][0]
        self.assertTrue(select["custom_id"].startswith(
            f"schedule_tz_zone:{self.match.id}:AM:"))
        self.assertLessEqual(len(select["options"]), 25)
        for option in select["options"]:
            self.assertTrue(valid_timezone(option["value"]), option["value"])

    def test_city_labels_carry_an_offset(self):
        body = self._body(di._handle_schedule_tz_region(
            self._payload("schedule_tz_region", self.match.id, values=["AM"])))
        options = body["data"]["components"][0]["components"][0]["options"]
        for option in options:
            self.assertIn("UTC", option["label"])

    def test_region_select_carries_the_time_forward(self):
        body = self._body(di._handle_schedule_tz_region(
            self._payload("schedule_tz_region", self.match.id, values=["AM"])))
        self.assertEqual(
            di._schedule_input_text({"message": {"content": body["data"]["content"]}}),
            self.TIME)

    def test_unknown_region_is_rejected(self):
        body = self._body(di._handle_schedule_tz_region(
            self._payload("schedule_tz_region", self.match.id, values=["ZZ"])))
        self.assertIn("region", body["data"]["content"].lower())

    # ── city select ──────────────────────────────────────────────────────────
    def test_city_select_saves_the_timezone(self):
        di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ]))
        self.player.refresh_from_db()
        self.assertEqual(self.player.timezone, TZ)

    def test_city_select_returns_a_confirmation(self):
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ])))
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        confirm = body["data"]["components"][0]["components"][0]
        self.assertTrue(confirm["custom_id"].startswith("schedule_confirm:"))
        expected, _err = parse_user_datetime(self.TIME, TZ)
        self.assertEqual(self._confirm_ts(body), int(expected.timestamp()))

    def test_bare_time_is_resolved_in_the_chosen_timezone(self):
        """A bare time survives the picker and lands on 4pm local in whichever zone
        was chosen.

        Deliberately does NOT assert a fixed offset between the two coasts, the way
        the dated case below can. A bare time is resolved relative to "now" in each
        zone, so for the three UTC hours where 4pm has passed in New York but not in
        Los Angeles the two roll to different days — asserting 3h here would fail
        every evening."""
        for zone in ("America/New_York", "America/Los_Angeles"):
            with self.subTest(zone=zone):
                body = self._body(di._handle_schedule_tz_zone(self._payload(
                    "schedule_tz_zone", self.match.id, "AM", values=[zone],
                    time_text="4pm")))
                confirm = body["data"]["components"][0]["components"][0]
                self.assertTrue(confirm["custom_id"].startswith("schedule_confirm:"))
                local = datetime.fromtimestamp(
                    self._confirm_ts(body), tz=ZoneInfo(zone))
                self.assertEqual((local.hour, local.minute), (16, 0))
                self.assertGreater(self._confirm_ts(body), 0)

    def test_changing_timezone_reparses_the_same_wall_clock_time(self):
        """The headline behavior: 8pm means 8pm wherever you actually are, so
        two zones three hours apart give two instants three hours apart."""
        east = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=["America/New_York"])))
        west = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=["America/Los_Angeles"])))
        self.assertEqual(self._confirm_ts(west) - self._confirm_ts(east), 3 * 3600)

    def test_city_select_acknowledges_the_change(self):
        """Discord renders <t:> in the viewer's own zone, so without a note the
        re-shown prompt can look identical after a change."""
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ])))
        self.assertIn("Saved your timezone", body["data"]["content"])

    def test_city_select_rejects_a_forged_timezone(self):
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=["Not/AZone"])))
        self.player.refresh_from_db()
        self.assertIsNone(self.player.timezone)
        self.assertIn("recognize", body["data"]["content"])

    def test_city_select_without_the_marker_still_saves(self):
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ], time_text=None)))
        self.player.refresh_from_db()
        self.assertEqual(self.player.timezone, TZ)
        self.assertIn("/schedule again", body["data"]["content"])
        self.assertEqual(body["data"]["components"], [])

    def test_city_select_surfaces_a_parse_error_but_keeps_the_timezone(self):
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ],
            time_text="whenever")))
        self.player.refresh_from_db()
        self.assertEqual(self.player.timezone, TZ)
        self.assertIn("couldn't read", body["data"]["content"])
        self.assertEqual(body["data"]["components"], [])

    def test_city_select_rechecks_permission(self):
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", self.match.id, "AM", values=[TZ],
            owner=self.outsider.discord_id)))
        self.outsider.refresh_from_db()
        self.assertIsNone(self.outsider.timezone)
        self.assertIn("can't set the time", body["data"]["content"])

    def test_city_select_rejects_a_match_played_since_the_prompt(self):
        self.match.delete()
        body = self._body(di._handle_schedule_tz_zone(self._payload(
            "schedule_tz_zone", 999999, "AM", values=[TZ])))
        self.assertIn("no longer", body["data"]["content"])

    # ── back / change ────────────────────────────────────────────────────────
    def test_back_returns_to_the_region_select(self):
        body = self._body(di._handle_schedule_tz_back(
            self._payload("schedule_tz_back", self.match.id)))
        select = body["data"]["components"][0]["components"][0]
        self.assertTrue(select["custom_id"].startswith("schedule_tz_region:"))
        self.assertEqual(
            di._schedule_input_text({"message": {"content": body["data"]["content"]}}),
            self.TIME)

    def test_change_timezone_preselects_the_current_region(self):
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])
        body = self._body(di._handle_schedule_tz_back(
            self._payload("schedule_tz_change", self.match.id)))
        options = body["data"]["components"][0]["components"][0]["options"]
        selected = [o["value"] for o in options if o.get("default")]
        self.assertEqual(selected, ["AM"])

    def test_change_timezone_preselects_a_region_for_an_uncurated_zone(self):
        """A zone set via the `timezone` option isn't in any city list, but must
        still land the user somewhere sensible."""
        self.player.timezone = "Asia/Kathmandu"
        self.player.save(update_fields=["timezone"])
        body = self._body(di._handle_schedule_tz_back(
            self._payload("schedule_tz_change", self.match.id)))
        options = body["data"]["components"][0]["components"][0]["options"]
        self.assertEqual([o["value"] for o in options if o.get("default")], ["AP"])

    def test_stale_custom_id_handled(self):
        payload = self._payload("schedule_tz_back", self.match.id)
        payload["data"]["custom_id"] = "schedule_tz_back"
        body = self._body(di._handle_schedule_tz_back(payload))
        self.assertIn("out of date", body["data"]["content"])

    # ── owner lock ───────────────────────────────────────────────────────────
    def test_every_prompt_custom_id_ends_in_the_owner_and_fits(self):
        """The dispatcher owner-lock keys off the LAST custom_id arg."""
        bodies = [
            self._body(di._handle_schedule_tz_back(
                self._payload("schedule_tz_back", self.match.id))),
            self._body(di._handle_schedule_tz_region(
                self._payload("schedule_tz_region", self.match.id, values=["AM"]))),
            self._body(di._handle_schedule_tz_zone(
                self._payload("schedule_tz_zone", self.match.id, "AM", values=[TZ]))),
        ]
        for body in bodies:
            for row in body["data"]["components"]:
                for component in row["components"]:
                    custom_id = component["custom_id"]
                    self.assertTrue(
                        custom_id.endswith(f":{self.player.discord_id}"), custom_id)
                    self.assertLessEqual(len(custom_id), 100, custom_id)


class ScheduleConfirmDisplayTests(ScheduleFixtureMixin, TestCase):
    """What the confirmation says about the timezone it used."""

    def setUp(self):
        self.build()
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)

    def _buttons(self, **kwargs):
        data = di._schedule_confirm_data(
            self.match, self.when, self.player.discord_id, **kwargs)
        return data, [c["custom_id"] for c in data["components"][0]["components"]]

    def test_confirmation_shows_the_timezone(self):
        data, _ = self._buttons(tz_name=TZ, time_text="Sep 15 2026 8pm")
        self.assertIn("New York", data["content"])
        self.assertIn("UTC-", data["content"])

    def test_confirmation_shows_an_uncurated_timezone(self):
        data, _ = self._buttons(tz_name="Asia/Kathmandu", time_text="Sep 15 2026 8pm")
        self.assertIn("Kathmandu", data["content"])
        self.assertIn("UTC+5:45", data["content"])

    def test_confirmation_offers_a_change_timezone_button(self):
        _data, ids = self._buttons(tz_name=TZ, time_text="Sep 15 2026 8pm")
        self.assertEqual(len(ids), 3)
        self.assertTrue(ids[0].startswith("schedule_confirm:"))
        self.assertTrue(ids[1].startswith("schedule_tz_change:"))
        self.assertTrue(ids[2].startswith("schedule_cancel:"))

    def test_epoch_confirmation_has_no_timezone_line_or_button(self):
        """An epoch is absolute — there's no zone to show and nothing to change."""
        data, ids = self._buttons(tz_name=None, time_text="<t:1789000000:F>")
        self.assertEqual(len(ids), 2)
        self.assertNotIn("timezone", data["content"].lower())

    def test_confirmation_offset_reflects_dst_at_the_scheduled_time(self):
        """Not the offset in effect today — the one that will actually apply."""
        january = datetime(2027, 1, 15, 20, 0, tzinfo=dt_timezone.utc)
        data = di._schedule_confirm_data(
            self.match, january, self.player.discord_id, tz_name=TZ)
        self.assertIn("UTC-5", data["content"])

    def test_confirmation_carries_the_time_text_forward(self):
        data, _ = self._buttons(tz_name=TZ, time_text="Sep 15 2026 8pm")
        self.assertEqual(
            di._schedule_input_text({"message": {"content": data["content"]}}),
            "Sep 15 2026 8pm")


class ScheduleCommandShapeTests(TestCase):
    """The `timezone` option is the only route to a zone the picker doesn't
    curate — guard it against a well-meaning cleanup."""

    def test_schedule_offers_time_and_timezone(self):
        names = [o["name"] for o in dc.SCHEDULE_COMMAND["options"]]
        self.assertEqual(names, ["time", "timezone"])

    def test_timezone_option_still_autocompletes(self):
        option = next(o for o in dc.SCHEDULE_COMMAND["options"]
                      if o["name"] == "timezone")
        self.assertTrue(option["autocomplete"])
        self.assertIn(("schedule", "timezone"), di.AUTOCOMPLETE_HANDLERS)

    def test_timezone_components_are_registered(self):
        for action in ("schedule_tz_region", "schedule_tz_zone",
                       "schedule_tz_back", "schedule_tz_change"):
            self.assertIn(action, di.COMPONENT_HANDLERS)


class ScheduleClearHandlerTests(ScheduleFixtureMixin, TestCase):
    """Omitting the `time` option means "clear the current time"."""

    def setUp(self):
        self.build()
        self.thread_id = "555000111"
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)

    def _schedule(self):
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])

    def _data(self, author=None, channel=None):
        return {
            "name": "schedule",
            "options": [],  # no time option = clear
            "_guild_id": self.guild.guild_id,
            "_channel_id": channel or self.thread_id,
            "_channel_name": None,
            "_author_id": author or self.player.discord_id,
            "_author_username": "player",
        }

    def _body(self, response):
        return json.loads(response.content)

    def test_clear_prompt_returned(self):
        self._schedule()
        body = self._body(di._handle_schedule_command(self._data()))
        content = body["data"]["content"]
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        self.assertIn("Remove the scheduled time", content)
        self.assertIn("Group A", content)
        self.assertIn(f"<t:{int(self.when.timestamp())}", content)

    def test_clear_prompt_writes_nothing(self):
        self._schedule()
        di._handle_schedule_command(self._data())
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)

    def test_clear_button_is_danger_styled_and_owner_locked(self):
        self._schedule()
        body = self._body(di._handle_schedule_command(self._data()))
        clear = body["data"]["components"][0]["components"][0]
        self.assertEqual(clear["style"], di.STYLE_DANGER)
        self.assertTrue(clear["custom_id"].startswith("schedule_clear_confirm:"))
        self.assertTrue(clear["custom_id"].endswith(f":{self.player.discord_id}"))
        self.assertLessEqual(len(clear["custom_id"]), 100)

    def test_nothing_to_clear_errors(self):
        body = self._body(di._handle_schedule_command(self._data()))
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        self.assertIn("doesn't have a scheduled time", body["data"]["content"])

    def test_unauthorized_user_gets_permission_error_not_prompt(self):
        self._schedule()
        body = self._body(
            di._handle_schedule_command(self._data(author=self.outsider.discord_id)))
        self.assertIn("not able to schedule", body["data"]["content"])
        self.assertNotIn("Remove the scheduled time", body["data"]["content"])

    def test_clear_works_without_a_profile_timezone(self):
        """Clearing needs no timezone, so it must not hit the NEED_TIMEZONE prompt."""
        self._schedule()
        self.assertFalse(self.player.timezone)
        body = self._body(di._handle_schedule_command(self._data()))
        self.assertIn("Remove the scheduled time", body["data"]["content"])

    def test_clearing_without_a_match_says_there_is_nothing_to_clear(self):
        """Nothing is stored for an unlinked thread, so "clear" has nothing to act
        on. Say what to do instead rather than reporting a missing match."""
        body = self._body(di._handle_schedule_command(self._data(channel="123123123")))
        content = body["data"]["content"]
        self.assertIn("isn't linked to a match", content)
        self.assertIn("`time`", content)
        self.assertNotIn("couldn't find", content.lower())
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)


class ScheduleUnlinkedTests(ScheduleFixtureMixin, TestCase):
    """/schedule where no tournament match resolves. Suggests a time that is
    explicitly NOT scheduled on the site, and writes nothing."""

    UNLINKED_CHANNEL = "123123123"

    def setUp(self):
        self.build()
        self.player.timezone = TZ
        self.player.save(update_fields=["timezone"])

    def _data(self, time="Sep 15 2026 8pm", channel=None, author=None):
        options = [] if time is None else [{"name": "time", "value": time}]
        return {
            "name": "schedule", "options": options,
            "_guild_id": self.guild.guild_id,
            "_channel_id": channel or self.UNLINKED_CHANNEL,
            "_channel_name": None,
            "_author_id": author or self.player.discord_id,
            "_author_username": "player",
        }

    def _body(self, response):
        return json.loads(response.content)

    def _lfg_thread(self):
        thread = LFGThread.objects.create(thread_id=self.UNLINKED_CHANNEL)
        thread.players.set([self.player, self.outsider])
        return thread

    # ── the ephemeral preview ──
    def test_a_bare_channel_offers_an_unlinked_suggestion(self):
        data = self._body(di._handle_schedule_command(self._data()))["data"]
        self.assertEqual(data.get("flags"), di.EPHEMERAL)
        self.assertIn("isn't linked to a game on the site", data["content"])
        self.assertNotIn("confirm", data["content"].lower())

    def test_an_lfg_thread_says_players_will_confirm(self):
        self._lfg_thread()
        data = self._body(di._handle_schedule_command(self._data()))["data"]
        self.assertIn("isn't linked to a game on the site", data["content"])
        self.assertIn("confirm", data["content"].lower())

    def test_the_confirm_button_is_sched_free_not_schedule_confirm(self):
        """schedule_confirm looks the match up by id and would answer 'that match
        can no longer be scheduled'."""
        data = self._body(di._handle_schedule_command(self._data()))["data"]
        ids = [c["custom_id"] for r in data["components"] for c in r["components"]]
        self.assertTrue(any(i.startswith("sched_free:") for i in ids))
        self.assertFalse(any(i.startswith("schedule_confirm:") for i in ids))

    def test_nothing_is_written_by_the_preview(self):
        di._handle_schedule_command(self._data())
        self.assertEqual(ScheduleProposal.objects.count(), 0)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    # ── clearing ──
    def test_clearing_is_refused_with_guidance(self):
        data = self._body(di._handle_schedule_command(self._data(time=None)))["data"]
        self.assertEqual(data.get("flags"), di.EPHEMERAL)
        self.assertIn("isn't linked to a match", data["content"])
        self.assertNotIn("couldn't find", data["content"].lower())

    # ── the public post ──
    def _confirm(self, kind="bare", when=None):
        when = when or (timezone.now() + timedelta(days=5)).replace(microsecond=0)
        payload = {
            "channel_id": self.UNLINKED_CHANNEL, "token": "tok",
            "member": {"user": {"id": self.player.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "sched_free", kind, int(when.timestamp()), self.player.discord_id)},
            "message": {"id": "m", "components": []},
        }
        with mock.patch.object(di.post_interaction_followup_task,
                               "apply_async") as enqueue:
            response = di._handle_schedule_free(payload)
        return self._body(response)["data"], enqueue

    def test_confirming_posts_publicly_with_the_disclaimer(self):
        _data, enqueue = self._confirm()
        message = enqueue.call_args.args[0][1]
        # No EPHEMERAL flag: an ephemeral "public" post would be visible only to the
        # proposer, which looks like the feature silently doing nothing.
        self.assertNotIn("flags", message)
        embed = message["embeds"][0]
        self.assertIn("isn't linked to a game on the site", embed["description"])
        self.assertIn("🕐", embed["title"])

    def test_the_bare_post_has_no_confirm_buttons(self):
        _data, enqueue = self._confirm(kind="bare")
        self.assertNotIn("components", enqueue.call_args.args[0][1])

    def test_the_lfg_post_carries_open_confirm_buttons(self):
        _data, enqueue = self._confirm(kind="lfg")
        rows = enqueue.call_args.args[0][1]["components"]
        ids = [c["custom_id"] for r in rows for c in r["components"]]
        self.assertTrue(ids)
        for custom_id in ids:
            # "g", not a snowflake, so the dispatcher's owner-lock stays off.
            self.assertEqual(di.decode_custom_id(custom_id)[1][-1], "g")

    def test_a_broker_outage_does_not_replace_the_confirmation_with_an_error(self):
        when = (timezone.now() + timedelta(days=5)).replace(microsecond=0)
        payload = {
            "channel_id": self.UNLINKED_CHANNEL, "token": "tok",
            "member": {"user": {"id": self.player.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "sched_free", "bare", int(when.timestamp()), self.player.discord_id)},
            "message": {"id": "m", "components": []},
        }
        with mock.patch.object(di.post_interaction_followup_task, "apply_async",
                               side_effect=KombuOperationalError("redis down")):
            data = self._body(di._handle_schedule_free(payload))["data"]
        self.assertIn("try again", data["content"].lower())

    def test_nothing_is_written_by_confirming(self):
        self._confirm()
        self.assertEqual(ScheduleProposal.objects.count(), 0)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    # ── responding to an LFG suggestion ──
    def _respond(self, action, clicker, confirmed=None, unavailable=None):
        embed = {"title": "🕐 Proposed time (not scheduled)", "description": "x"}
        fields = []
        if confirmed:
            fields.append({"name": di.SCHEDULE_FREE_CONFIRMED_FIELD,
                           "value": "\n".join(confirmed), "inline": False})
        if unavailable:
            fields.append({"name": di.SCHEDULE_FREE_UNAVAILABLE_FIELD,
                           "value": "\n".join(unavailable), "inline": False})
        if fields:
            embed["fields"] = fields
        payload = {
            "channel_id": self.UNLINKED_CHANNEL,
            "member": {"user": {"id": clicker, "username": "someone"}},
            "data": {"custom_id": di.encode_custom_id(action, "123", "g")},
            "message": {"id": "m", "embeds": [embed], "components": []},
        }
        handler = di.COMPONENT_HANDLERS[action]
        return self._body(handler(payload))["data"]

    def test_a_thread_player_can_confirm(self):
        self._lfg_thread()
        data = self._respond("sched_lfg_ok", self.player.discord_id)
        field = data["embeds"][0]["fields"][0]
        self.assertEqual(field["name"], di.SCHEDULE_FREE_CONFIRMED_FIELD)
        self.assertIn(f"<@{self.player.discord_id}>", field["value"])

    def test_a_non_player_is_refused(self):
        self._lfg_thread()
        data = self._respond("sched_lfg_ok", "88888888")
        self.assertIn("players in this thread", data["content"])

    def test_confirming_twice_does_not_double_count(self):
        """Identity is the id, so a rename can't add a second line either."""
        self._lfg_thread()
        first = self._respond("sched_lfg_ok", self.player.discord_id)
        lines = first["embeds"][0]["fields"][0]["value"].splitlines()
        again = self._respond("sched_lfg_ok", self.player.discord_id,
                              confirmed=lines)
        self.assertEqual(len(again["embeds"][0]["fields"][0]["value"].splitlines()), 1)

    def test_declining_moves_a_confirmation_to_unavailable(self):
        """Declining no longer just erases the confirmation -- it records that the
        player answered, under ❌ Unavailable."""
        self._lfg_thread()
        first = self._respond("sched_lfg_ok", self.player.discord_id)
        lines = first["embeds"][0]["fields"][0]["value"].splitlines()
        after = self._respond("sched_lfg_no", self.player.discord_id, confirmed=lines)
        fields = after["embeds"][0].get("fields", [])
        self.assertEqual([f["name"] for f in fields],
                         [di.SCHEDULE_FREE_UNAVAILABLE_FIELD])
        self.assertIn(f"<@{self.player.discord_id}>", fields[0]["value"])

    def test_confirming_after_declining_moves_them_back(self):
        self._lfg_thread()
        declined = self._respond("sched_lfg_no", self.player.discord_id)
        unavailable = declined["embeds"][0]["fields"][0]["value"].splitlines()
        after = self._respond("sched_lfg_ok", self.player.discord_id,
                              unavailable=unavailable)
        fields = after["embeds"][0].get("fields", [])
        self.assertEqual([f["name"] for f in fields],
                         [di.SCHEDULE_FREE_CONFIRMED_FIELD])
        self.assertIn(f"<@{self.player.discord_id}>", fields[0]["value"])

    def test_a_player_is_never_in_both_lists(self):
        self._lfg_thread()
        data = self._respond("sched_lfg_ok", self.player.discord_id)
        for _ in range(3):
            lines = {f["name"]: f["value"].splitlines()
                     for f in data["embeds"][0].get("fields", [])}
            data = self._respond(
                "sched_lfg_no", self.player.discord_id,
                confirmed=lines.get(di.SCHEDULE_FREE_CONFIRMED_FIELD),
                unavailable=lines.get(di.SCHEDULE_FREE_UNAVAILABLE_FIELD))
            data = self._respond(
                "sched_lfg_ok", self.player.discord_id,
                confirmed=None,
                unavailable=[f["value"] for f in data["embeds"][0].get("fields", [])
                             if f["name"] == di.SCHEDULE_FREE_UNAVAILABLE_FIELD])
        mention = f"<@{self.player.discord_id}>"
        appearances = sum(
            f["value"].count(mention) for f in data["embeds"][0].get("fields", []))
        self.assertEqual(appearances, 1)

    def test_responding_writes_nothing(self):
        self._lfg_thread()
        self._respond("sched_lfg_ok", self.player.discord_id)
        self.assertEqual(ScheduleProposal.objects.count(), 0)


class ScheduleUnlinkedTimezoneTests(ScheduleFixtureMixin, TestCase):
    """The sentinel path through the timezone picker — the likeliest way this
    feature breaks, since only users with no saved timezone ever see it."""

    UNLINKED_CHANNEL = "123123123"

    def setUp(self):
        self.build()   # self.player has NO timezone

    def _body(self, response):
        return json.loads(response.content)

    def test_no_timezone_opens_the_region_picker_with_the_sentinel(self):
        data = self._body(di._handle_schedule_command({
            "name": "schedule",
            "options": [{"name": "time", "value": "Sep 15 2026 8pm"}],
            "_guild_id": self.guild.guild_id,
            "_channel_id": self.UNLINKED_CHANNEL,
            "_channel_name": None,
            "_author_id": self.player.discord_id,
            "_author_username": "player",
        }))["data"]
        select = data["components"][0]["components"][0]
        args = di.decode_custom_id(select["custom_id"])[1]
        self.assertEqual(args[0], di.SCHEDULE_NO_MATCH)

    def test_the_sentinel_does_not_report_a_missing_match(self):
        payload = {
            "guild_id": self.guild.guild_id, "channel_id": self.UNLINKED_CHANNEL,
            "member": {"user": {"id": self.player.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "schedule_tz_region", di.SCHEDULE_NO_MATCH, self.player.discord_id),
                "values": ["america"]},
            "message": {"id": "m", "content": "-# From your input: `Sep 15 2026 8pm`",
                        "components": []},
        }
        data = self._body(di._handle_schedule_tz_region(payload))["data"]
        self.assertNotIn("can no longer be scheduled", data.get("content", ""))

    def test_a_real_match_id_still_enforces_permission(self):
        """The sentinel branch must not become a way to skip authorization."""
        payload = {
            "guild_id": self.guild.guild_id, "channel_id": "555000111",
            "member": {"user": {"id": self.outsider.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "schedule_tz_region", self.match.id, self.outsider.discord_id),
                "values": ["america"]},
            "message": {"id": "m", "content": "-# From your input: `Sep 15 2026 8pm`",
                        "components": []},
        }
        data = self._body(di._handle_schedule_tz_region(payload))["data"]
        self.assertIn("can't set the time", data["content"])

    def test_the_picker_returns_a_sched_free_button_not_schedule_confirm(self):
        """The break this test exists for: finishing the picker on an unlinked path
        must not hand back a match-path button carrying the sentinel."""
        payload = {
            "guild_id": self.guild.guild_id, "channel_id": self.UNLINKED_CHANNEL,
            "member": {"user": {"id": self.player.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "schedule_tz_zone", di.SCHEDULE_NO_MATCH, "america",
                self.player.discord_id),
                "values": [TZ]},
            "message": {"id": "m", "content": "-# From your input: `Sep 15 2026 8pm`",
                        "components": []},
        }
        data = self._body(di._handle_schedule_tz_zone(payload))["data"]
        ids = [c["custom_id"] for r in data["components"] for c in r["components"]]
        self.assertTrue(any(i.startswith("sched_free:") for i in ids))
        self.assertFalse(any(i.startswith("schedule_confirm:") for i in ids))

    def test_the_timezone_is_saved_from_an_unlinked_thread(self):
        """The payoff of get-or-create: a zone set here is reused everywhere."""
        payload = {
            "guild_id": self.guild.guild_id, "channel_id": self.UNLINKED_CHANNEL,
            "member": {"user": {"id": self.player.discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "schedule_tz_zone", di.SCHEDULE_NO_MATCH, "america",
                self.player.discord_id),
                "values": [TZ]},
            "message": {"id": "m", "content": "-# From your input: `Sep 15 2026 8pm`",
                        "components": []},
        }
        di._handle_schedule_tz_zone(payload)
        self.player.refresh_from_db()
        self.assertEqual(self.player.timezone, TZ)


class ScheduleClearConfirmTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])

    UNSET = object()

    def _payload(self, match_id=UNSET, owner=UNSET, guild=UNSET):
        return {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_clear_confirm",
                self.match.id if match_id is self.UNSET else match_id,
                self.player.discord_id if owner is self.UNSET else owner,
            )},
            "guild_id": self.guild.guild_id if guild is self.UNSET else guild,
            "token": None,
        }

    def test_clear_writes_null(self):
        response = di._handle_schedule_clear_confirm(self._payload())
        body = json.loads(response.content)
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.assertIn("removed", body["data"]["content"])
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_clear_preserves_derived_name(self):
        """save(update_fields=...) must not re-run Match.save()'s derivations."""
        original_name = self.match.name
        original_number = self.match.match_number
        di._handle_schedule_clear_confirm(self._payload())
        self.match.refresh_from_db()
        self.assertEqual(self.match.name, original_name)
        self.assertEqual(self.match.match_number, original_number)

    def test_clear_rechecks_permission(self):
        response = di._handle_schedule_clear_confirm(
            self._payload(owner=self.outsider.discord_id))
        self.assertEqual(
            json.loads(response.content)["data"].get("flags"), di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)

    def test_clear_rejects_other_guild(self):
        other = DiscordGuild.objects.create(guild_id="900999", name="Other")
        response = di._handle_schedule_clear_confirm(self._payload(guild=other.guild_id))
        self.assertEqual(
            json.loads(response.content)["data"].get("flags"), di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)

    def test_clear_rejects_match_played_since_prompt(self):
        self.match.game = Game.objects.create()
        self.match.save(update_fields=["game"])
        response = di._handle_schedule_clear_confirm(self._payload())
        self.assertEqual(
            json.loads(response.content)["data"].get("flags"), di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)

    def test_clear_when_already_cleared(self):
        """Double-clicking the button shouldn't look like a fresh success."""
        self.match.scheduled_time = None
        self.match.save(update_fields=["scheduled_time"])
        response = di._handle_schedule_clear_confirm(self._payload())
        body = json.loads(response.content)
        self.assertEqual(body["data"].get("flags"), di.EPHEMERAL)
        self.assertIn("no longer has", body["data"]["content"])

    def test_stale_custom_id_handled(self):
        payload = {"data": {"custom_id": "schedule_clear_confirm"},
                   "guild_id": self.guild.guild_id}
        response = di._handle_schedule_clear_confirm(payload)
        self.assertEqual(
            json.loads(response.content)["data"].get("flags"), di.EPHEMERAL)

    def test_cancel_leaves_time_intact(self):
        response = di._handle_schedule_cancel(
            {"data": {"custom_id": di.encode_custom_id(
                "schedule_cancel", self.player.discord_id)}})
        self.assertEqual(
            json.loads(response.content)["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)


class UpcomingEmbedTimestampTests(ScheduleFixtureMixin, TestCase):
    """Pins build_upcoming_embed's Scheduled field, which now delegates to
    format_discord_timestamp instead of building the markup inline."""

    def setUp(self):
        self.build()

    def _scheduled_field(self, embed):
        return next(
            (f for f in embed.get("fields", []) if f["name"] == "Scheduled"), None)

    def test_scheduled_field_matches_helper(self):
        when = (timezone.now() + timedelta(days=3)).replace(microsecond=0)
        self.match.scheduled_time = when
        self.match.save(update_fields=["scheduled_time"])

        field = self._scheduled_field(build_upcoming_embed(self.match))
        self.assertIsNotNone(field)
        self.assertEqual(field["value"], format_discord_timestamp(when))
        # The exact wire format, independent of the helper.
        ts = int(when.timestamp())
        self.assertEqual(field["value"], f"<t:{ts}:F> (<t:{ts}:R>)")

    def test_no_scheduled_field_when_cleared(self):
        self.assertIsNone(self.match.scheduled_time)
        self.assertIsNone(self._scheduled_field(build_upcoming_embed(self.match)))


class UpcomingEmbedSummaryTests(ScheduleFixtureMixin, TestCase):
    """build_upcoming_embed's description. /upcoming keeps its "The next scheduled
    game" wording; /schedule overrides it, since the match it just wrote isn't
    necessarily the tournament's next one."""

    def setUp(self):
        self.build()

    def test_upcoming_wording_is_the_default(self):
        embed = build_upcoming_embed(self.match)
        self.assertEqual(embed["description"], "The next scheduled game")

    def test_filters_still_name_themselves(self):
        embed = build_upcoming_embed(
            self.match, series=self.tournament, player=self.player)
        self.assertEqual(
            embed["description"],
            f"The next scheduled {self.tournament.name} game for {self.player.discord}")

    def test_summary_override_replaces_description(self):
        embed = build_upcoming_embed(self.match, summary="Scheduled by player")
        self.assertEqual(embed["description"], "Scheduled by player")

    def test_summary_none_drops_description(self):
        # None is distinct from "not passed" — the key is omitted entirely rather
        # than falling back to the /upcoming wording.
        self.assertNotIn("description", build_upcoming_embed(self.match, summary=None))


class ScheduleAnnouncementDescriptionTests(ScheduleFixtureMixin, TestCase):
    """Neither /schedule announcement path may reuse /upcoming's "next scheduled
    game" line — the title already says the match was just scheduled."""

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=2)).replace(microsecond=0)

    def test_direct_write_names_the_scheduler_without_pinging(self):
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])
        payload = {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_confirm", self.match.pk, int(self.when.timestamp()),
                self.player.discord_id)},
            "guild_id": self.guild.guild_id,
            "token": "tok",
        }
        with mock.patch.object(di, "_consensus_required", return_value=(False, [])), \
             mock.patch.object(di.post_interaction_followup_task, "apply_async") as followup:
            di._handle_schedule_confirm(payload)

        (_token, data), _kwargs = followup.call_args[0][0], followup.call_args[1]
        description = data["embeds"][0]["description"]
        self.assertEqual(description, f"Scheduled by {self.player.discord}")
        self.assertNotIn("next scheduled", description)
        # A raw mention would ping the clicker: this followup sets no
        # allowed_mentions, so the name must stay plain text.
        self.assertNotIn("<@", description)

    def test_consensus_finalized_view_has_no_description(self):
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])
        proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player)
        proposal.confirmed_by.add(self.player)

        embed = di._schedule_finalized_data(proposal, self.match)["embeds"][0]
        self.assertNotIn("description", embed)
        self.assertIn("scheduled", embed["title"])
        # The roster field is what reports who agreed; it survives the override.
        self.assertTrue(
            any(f["name"] == "✅ Confirmed by" for f in embed["fields"]))

    def test_builder_failure_falls_back_without_doubling_the_title(self):
        """The fallback title is prefixed and suffixed by the caller below it, so it
        must not carry its own '🗓️'/'scheduled' — that read '🗓️ 🗓️ Scheduled
        scheduled'."""
        proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player)
        with mock.patch.object(di, "build_upcoming_embed", side_effect=ValueError):
            embed = di._schedule_finalized_data(proposal, self.match)["embeds"][0]

        self.assertEqual(embed["title"].count("🗓️"), 1)
        self.assertEqual(embed["title"].lower().count("scheduled"), 1)
        self.assertTrue(
            any(f["name"] == "✅ Confirmed by" for f in embed["fields"]))


class ScheduleAutocompleteTests(TestCase):

    def test_timezone_autocomplete_shape(self):
        choices = di._ac_schedule_timezone("new york", {})
        self.assertTrue(choices)
        self.assertEqual(choices[0]["name"], choices[0]["value"])
        self.assertIn(TZ, [c["value"] for c in choices])

    def test_timezone_autocomplete_capped(self):
        self.assertLessEqual(len(di._ac_schedule_timezone("a", {})), 25)

    def test_empty_query_returns_common_zones(self):
        choices = di._ac_schedule_timezone("", {})
        self.assertTrue(choices)
        self.assertIn(TZ, [c["value"] for c in choices])


class EditChannelMessageBodyTests(TestCase):
    """The PATCH body must send only the parts it was given. components=[] is
    meaningful (it clears the button row), so it must be distinguished from
    components=None ("leave them alone") by an `is not None` test, not truthiness."""

    def _patch_body(self, **kwargs):
        """Call edit_channel_message with requests.patch mocked; return the JSON body."""
        with mock.patch.object(ds.requests, "patch") as patched:
            patched.return_value.raise_for_status.return_value = None
            ds.edit_channel_message("chan", "msg", **kwargs)
            if not patched.called:
                return None
            return patched.call_args.kwargs["json"]

    def test_components_empty_list_is_sent(self):
        body = self._patch_body(embeds=[{"title": "x"}], components=[])
        self.assertEqual(body["components"], [])
        self.assertEqual(body["embeds"], [{"title": "x"}])

    def test_components_omitted_when_none(self):
        body = self._patch_body(embeds=[{"title": "x"}])
        self.assertNotIn("components", body)

    def test_embeds_omitted_when_none(self):
        body = self._patch_body(components=[])
        self.assertNotIn("embeds", body)

    def test_no_request_when_nothing_to_change(self):
        self.assertIsNone(self._patch_body())

    def test_permanent_failure_is_blocked_not_error(self):
        """403/404 must not be retried; 5xx must be."""
        for status, expected in ((403, ds.THREAD_BLOCKED), (404, ds.THREAD_BLOCKED),
                                 (500, ds.THREAD_ERROR), (429, ds.THREAD_ERROR)):
            with self.subTest(status=status):
                response = mock.Mock(status_code=status, text="boom")
                err = ds.requests.RequestException("failed")
                err.response = response
                with mock.patch.object(ds.requests, "patch", side_effect=err):
                    result = ds.edit_channel_message("chan", "msg", embeds=[{}])
                self.assertEqual(result, expected)


class LFGStartGuardTests(TestCase):
    """✔ Start must not create a thread for a game with no parsed players."""

    def _payload(self, players_value, custom_id=None):
        payload = {
            "channel_id": "chan",
            "guild_id": "guild",
            "token": "tok",
            "message": {
                "id": "msg",
                "content": "",
                "embeds": [{
                    "title": "Looking for Game",
                    "description": "a game",
                    "fields": [{"name": di.LFG_PLAYERS_FIELD,
                                "value": players_value, "inline": False}],
                }],
            },
        }
        if custom_id is not None:
            payload["data"] = {"custom_id": custom_id}
        return payload

    def test_empty_players_is_rejected(self):
        with mock.patch.object(di.create_lfg_thread_task, "delay") as delay:
            response = di._handle_lfg_start(self._payload("—"))
        delay.assert_not_called()
        self.assertIn("no players", json.loads(response.content)["data"]["content"])

    def test_start_with_players_enqueues_thread_creation(self):
        with mock.patch.object(di.create_lfg_thread_task, "delay") as delay:
            response = di._handle_lfg_start(self._payload("Bob (<@123>)"))
        delay.assert_called_once()
        self.assertEqual(delay.call_args.args[5], [{"name": "Bob", "id": "123"}])
        data = json.loads(response.content)["data"]
        self.assertEqual(data["components"], [])
        self.assertEqual(data["embeds"][0]["footer"]["text"], "✔ Game has started.")

    def test_a_missing_custom_id_still_starts_the_game(self):
        """The host is read off the button's custom_id, but losing it must cost
        only the host attribution — never the thread the player asked for."""
        with mock.patch.object(di.create_lfg_thread_task, "delay") as delay:
            di._handle_lfg_start(self._payload("Bob (<@123>)"))
        delay.assert_called_once()
        self.assertIsNone(delay.call_args.kwargs["host_id"])


class LFGThreadNameTests(TestCase):
    """What the game thread is called. The host's description wins; with none,
    the thread takes the LFG message's own title (the tag's description or name)
    rather than a bare "Game"."""

    def _create(self, description, embed):
        """Run create_lfg_thread_task far enough to capture the thread name."""
        with mock.patch("the_gatehouse.services.discordservice.create_message_thread",
                        return_value=None) as create, \
                mock.patch("the_gatehouse.services.discordservice.create_forum_thread"), \
                mock.patch("the_gatehouse.services.discordservice.post_channel_message"):
            create_lfg_thread_task(
                "chan", "msg", None, None, description,
                [{"id": "1", "name": "Bob"}], embed,
            )
        return create.call_args.args[2]

    def test_the_description_is_used_when_given(self):
        name = self._create("Quick 4p game", {"title": "Casual Game"})
        self.assertEqual(name, "Quick 4p game")

    def test_a_blank_description_falls_back_to_the_message_title(self):
        name = self._create("", {"title": "Casual Game"})
        self.assertEqual(name, "Casual Game")

    def test_the_default_lfg_title_is_used_when_that_is_all_there_is(self):
        name = self._create("", {"title": di.LFG_DEFAULT_TITLE})
        self.assertEqual(name, di.LFG_DEFAULT_TITLE)

    def test_it_still_falls_back_to_game_with_no_embed(self):
        """An older enqueued task (or a malformed embed) carries no title."""
        self.assertEqual(self._create("", None), "Game")
        self.assertEqual(self._create("", {}), "Game")

    def test_the_name_is_capped_at_discords_limit(self):
        self.assertEqual(len(self._create("", {"title": "x" * 200})), 100)


class RenameCommandTests(TestCase):
    """/rename: the /lfg host retitles their game thread. Ephemeral either way."""

    THREAD_ID = "1303834523347456077"
    HOST_ID = "820000000000000001"
    OTHER_ID = "820000000000000002"

    def setUp(self):
        self.host = Profile.objects.create(discord="renamehost",
                                           discord_id=self.HOST_ID)
        self.other = Profile.objects.create(discord="renameother",
                                            discord_id=self.OTHER_ID)
        self.thread = LFGThread.objects.create(
            thread_id=self.THREAD_ID, host=self.host, description="original")

    def _command(self, title="New Title", author=None, channel_id=None,
                 channel_type=11, result=None):
        """Run /rename with rename_channel patched. Returns (content, mock)."""
        result = result if result is not None else (ds.THREAD_OK, None)
        data = {
            "_channel_id": channel_id or self.THREAD_ID,
            "_author_id": author or self.HOST_ID,
            "_channel_type": channel_type,
            "options": [{"name": "title", "value": title}],
        }
        with mock.patch.object(di, "rename_channel", return_value=result) as renamer:
            response = di._handle_rename_command(data)
        content = json.loads(response.content)["data"]["content"]
        return content, renamer

    def _reload(self):
        return LFGThread.objects.get(pk=self.thread.pk)

    # ── the happy path ──
    def test_the_host_renames_the_thread(self):
        content, renamer = self._command("Chaos Game")
        self.assertIn("Chaos Game", content)
        renamer.assert_called_once_with(self.THREAD_ID, "Chaos Game")

    def test_the_reply_is_ephemeral(self):
        data = {
            "_channel_id": self.THREAD_ID, "_author_id": self.HOST_ID,
            "_channel_type": 11,
            "options": [{"name": "title", "value": "Quiet"}],
        }
        with mock.patch.object(di, "rename_channel", return_value=(ds.THREAD_OK, None)):
            response = di._handle_rename_command(data)
        self.assertEqual(json.loads(response.content)["data"]["flags"], di.EPHEMERAL)

    def test_the_nickname_is_saved(self):
        self._command("Chaos Game")
        self.assertEqual(self._reload().nickname, "Chaos Game")

    # ── permission ──
    def test_a_non_host_is_refused_and_discord_is_never_called(self):
        content, renamer = self._command(author=self.OTHER_ID)
        self.assertIn("started this game", content)
        renamer.assert_not_called()
        self.assertEqual(self._reload().nickname, "")

    def test_a_thread_with_no_host_is_refused(self):
        self.thread.host = None
        self.thread.save(update_fields=["host"])
        content, renamer = self._command()
        self.assertIn("who started this game", content)
        renamer.assert_not_called()

    def test_an_unlinked_user_is_refused(self):
        """A Discord id with no site Profile can't be the host."""
        content, renamer = self._command(author="820000000000000009")
        self.assertIn("started this game", content)
        renamer.assert_not_called()

    # ── scope ──
    def test_a_tournament_group_thread_is_refused(self):
        guild = DiscordGuild.objects.create(guild_id="9001", name="G")
        designer = Profile.objects.create(discord="rndesign", discord_id="8203")
        tournament = Tournament.objects.create(name="T", guild=guild, designer=designer)
        stage = Stage.objects.create(tournament=tournament, name="S", order=1)
        rnd = Round.objects.create(stage=stage, round_number=1)
        group = PlayerGroup.objects.create(round=rnd, group_number=1, name="A")
        series = MatchSeries.objects.create(round=rnd, player_group=group,
                                            number_of_games=1)
        self.thread.series = series
        self.thread.save(update_fields=["series"])
        content, renamer = self._command()
        self.assertIn("tournament group thread", content)
        renamer.assert_not_called()

    def test_a_non_thread_channel_says_to_use_a_thread(self):
        content, renamer = self._command(channel_id="999999999999999999",
                                         channel_type=0)
        self.assertIn("inside your game's thread", content)
        renamer.assert_not_called()

    def test_an_unknown_thread_is_refused(self):
        content, renamer = self._command(channel_id="999999999999999999",
                                         channel_type=11)
        self.assertIn("game thread I know about", content)
        renamer.assert_not_called()

    def test_a_blank_title_is_refused(self):
        content, renamer = self._command("   ")
        self.assertIn("Give the thread a title", content)
        renamer.assert_not_called()

    # ── failures ──
    def test_a_rate_limit_reports_the_wait_in_minutes(self):
        content, _ = self._command(result=(ds.THREAD_ERROR, 421.5))
        self.assertIn("about 8 minutes", content)

    def test_a_short_rate_limit_reports_seconds(self):
        content, _ = self._command(result=(ds.THREAD_ERROR, 12.0))
        self.assertIn("about 12 seconds", content)

    def test_a_permission_failure_says_so(self):
        content, _ = self._command(result=(ds.THREAD_BLOCKED, None))
        self.assertIn("permission", content)

    def test_a_generic_failure_says_try_again(self):
        content, _ = self._command(result=(ds.THREAD_ERROR, None))
        self.assertIn("try again", content)

    def test_a_failed_rename_leaves_the_nickname_unchanged(self):
        """The model must never claim a name the thread doesn't have."""
        self.thread.nickname = "before"
        self.thread.save(update_fields=["nickname"])
        self._command("after", result=(ds.THREAD_ERROR, None))
        self.assertEqual(self._reload().nickname, "before")

    # ── truncation ──
    def test_a_long_title_is_truncated_for_the_nickname(self):
        """nickname is max_length=50; Postgres RAISES on overflow rather than
        truncating, so this must be explicit."""
        self._command("x" * 200)
        self.assertEqual(len(self._reload().nickname), 50)

    def test_the_confirmation_shows_the_capped_title(self):
        """rename_channel caps the name at 100 itself, so the reply must not
        promise more than Discord accepted."""
        content, _renamer = self._command("y" * 200)
        self.assertIn("y" * 100, content)
        self.assertNotIn("y" * 101, content)


class RenameChannelHelperTests(TestCase):
    """rename_channel: the only PATCH /channels/{id} in the codebase."""

    def test_success_returns_ok_and_no_wait(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        with mock.patch.object(ds.requests, "patch", return_value=response) as patched:
            result, retry_after = ds.rename_channel("chan", "New Name")
        self.assertEqual(result, ds.THREAD_OK)
        self.assertIsNone(retry_after)
        self.assertEqual(patched.call_args.kwargs["json"], {"name": "New Name"})

    def test_the_name_is_capped_at_100(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        with mock.patch.object(ds.requests, "patch", return_value=response) as patched:
            ds.rename_channel("chan", "z" * 200)
        self.assertEqual(len(patched.call_args.kwargs["json"]["name"]), 100)

    def test_status_classification(self):
        for status, expected in ((403, ds.THREAD_BLOCKED), (404, ds.THREAD_BLOCKED),
                                 (500, ds.THREAD_ERROR)):
            with self.subTest(status=status):
                resp = mock.Mock(status_code=status, text="boom")
                resp.json.return_value = {}
                resp.headers = {}
                err = ds.requests.RequestException("failed")
                err.response = resp
                with mock.patch.object(ds.requests, "patch", side_effect=err):
                    result, retry_after = ds.rename_channel("chan", "n")
                self.assertEqual(result, expected)
                self.assertIsNone(retry_after)

    def test_a_429_body_yields_its_retry_after(self):
        resp = mock.Mock(status_code=429, text="rate limited")
        resp.json.return_value = {"retry_after": 421.5, "global": False}
        resp.headers = {}
        err = ds.requests.RequestException("429")
        err.response = resp
        with mock.patch.object(ds.requests, "patch", side_effect=err):
            result, retry_after = ds.rename_channel("chan", "n")
        self.assertEqual(result, ds.THREAD_ERROR)
        self.assertEqual(retry_after, 421.5)

    def test_a_429_falls_back_to_the_header(self):
        resp = mock.Mock(status_code=429, text="rate limited")
        resp.json.return_value = {}
        resp.headers = {"Retry-After": "30"}
        err = ds.requests.RequestException("429")
        err.response = resp
        with mock.patch.object(ds.requests, "patch", side_effect=err):
            _result, retry_after = ds.rename_channel("chan", "n")
        self.assertEqual(retry_after, 30.0)

    def test_a_malformed_body_degrades_to_none(self):
        """A non-JSON body must not turn a rate limit into an exception."""
        resp = mock.Mock(status_code=429, text="<html>nope</html>")
        resp.json.side_effect = ValueError("no json")
        resp.headers = {}
        err = ds.requests.RequestException("429")
        err.response = resp
        with mock.patch.object(ds.requests, "patch", side_effect=err):
            result, retry_after = ds.rename_channel("chan", "n")
        self.assertEqual(result, ds.THREAD_ERROR)
        self.assertIsNone(retry_after)

    def test_a_network_error_with_no_response_is_handled(self):
        err = ds.requests.RequestException("timeout")
        with mock.patch.object(ds.requests, "patch", side_effect=err):
            result, retry_after = ds.rename_channel("chan", "n")
        self.assertEqual(result, ds.THREAD_ERROR)
        self.assertIsNone(retry_after)


class LFGHostRecordingTests(TestCase):
    """The host must be recorded at thread creation — it is unrecoverable after."""

    def _start(self, owner="830000000000000001"):
        payload = {
            "channel_id": "chan", "guild_id": "guild", "token": "tok",
            "member": {"user": {"id": owner}},
            "data": {"custom_id": di.encode_custom_id("lfg_start", owner)},
            "message": {
                "id": "msg", "content": "",
                "embeds": [{
                    "title": "Looking for Game", "description": "a game",
                    "fields": [{"name": di.LFG_PLAYERS_FIELD,
                                "value": f"Bob (<@{owner}>)", "inline": False}],
                }],
            },
        }
        with mock.patch.object(di.create_lfg_thread_task, "delay") as delay:
            di._handle_lfg_start(payload)
        return delay

    def test_start_forwards_the_host_from_its_custom_id(self):
        delay = self._start("830000000000000001")
        self.assertEqual(delay.call_args.kwargs["host_id"], "830000000000000001")

    def _run_task(self, host_id=..., thread_id="770000000000000001"):
        kwargs = {} if host_id is ... else {"host_id": host_id}
        with mock.patch("the_gatehouse.services.discordservice.create_message_thread",
                        return_value=thread_id), \
                mock.patch("the_gatehouse.services.discordservice.create_forum_thread"), \
                mock.patch("the_gatehouse.services.discordservice.post_channel_message"), \
                mock.patch("the_gatehouse.tasks.link_lfg_message_task.apply_async"):
            create_lfg_thread_task(
                "chan", "msg", None, None, "a game",
                [{"id": "840000000000000001", "name": "Bob"}], {}, **kwargs,
            )
        return LFGThread.objects.get(thread_id=thread_id)

    def test_the_task_records_the_host(self):
        thread = self._run_task(host_id="840000000000000001")
        self.assertIsNotNone(thread.host)
        self.assertEqual(thread.host.discord_id, "840000000000000001")

    def test_the_host_is_resolved_even_for_a_brand_new_profile(self):
        """The host Profile is CREATED by the players loop, so the host lookup
        has to run after it — not before."""
        self.assertFalse(Profile.objects.filter(discord_id="840000000000000001").exists())
        thread = self._run_task(host_id="840000000000000001")
        self.assertEqual(thread.host.discord_id, "840000000000000001")

    def test_the_task_still_works_without_a_host_id(self):
        """A task enqueued before this argument existed must still deserialize."""
        thread = self._run_task()
        self.assertIsNone(thread.host)

    def test_an_existing_thread_still_gets_its_host(self):
        """defaults= only applies on CREATE, so a pre-existing row (a retry, or a
        thread that captured a roll first) must still have its host set."""
        LFGThread.objects.create(thread_id="770000000000000002")
        thread = self._run_task(host_id="840000000000000001",
                                thread_id="770000000000000002")
        self.assertEqual(thread.host.discord_id, "840000000000000001")

    def test_a_retry_does_not_reassign_an_existing_host(self):
        existing = Profile.objects.create(discord="firsthost", discord_id="850000000000000001")
        LFGThread.objects.create(thread_id="770000000000000003", host=existing)
        thread = self._run_task(host_id="840000000000000001",
                                thread_id="770000000000000003")
        self.assertEqual(thread.host, existing)


class _NoLoginSignalMixin:
    """force_login fires user_logged_in, whose handler builds absolute URLs from the
    request and enqueues Discord work — neither of which a bare test request supports.
    None of that is under test here, so disconnect it for the duration."""

    def setUp(self):
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        super().setUp()


class LFGRoleModalViewTests(_NoLoginSignalMixin, TestCase):
    """LFG roles are managed through modals: each add/edit/delete commits on its own,
    independent of the main guild form."""

    def setUp(self):
        super().setUp()
        self.guild = DiscordGuild.objects.create(guild_id="700100", name="LFG Guild",
                                                 bot_member=True)
        self.user = User.objects.create_user(username="lfgmod", password="pw")
        self.profile = self.user.profile
        self.profile.group = "P"          # plain player; moderates via guild_moderators
        self.profile.player_onboard = True
        self.profile.save()
        self.guild.guild_moderators.add(self.profile)
        self.client.force_login(self.user)

        # Two roles exist on Discord; only one is claimed by default.
        self.discord_roles = [{"id": "100000000000000011", "name": "Nightfall"},
                              {"id": "100000000000000022", "name": "Dawn"}]

    def _mock_discord(self):
        """Patch the Discord reads on the views module (views.py imports them by name,
        so patching the source module would not intercept the bound reference)."""
        return [
            mock.patch("the_gatehouse.views.get_guild_roles", return_value=self.discord_roles),
            mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=[]),
            mock.patch("the_gatehouse.views.get_forum_channel_info", return_value=None),
            mock.patch("the_gatehouse.views.refresh_guild_commands"),
        ]

    def _with_discord(self, fn):
        patches = self._mock_discord()
        started = [p.start() for p in patches]
        try:
            return fn(started[-1])   # the refresh_guild_commands mock
        finally:
            for p in patches:
                p.stop()

    def _add_url(self):
        return reverse("guild-lfg-role-add", args=[self.guild.guild_id])

    def _edit_url(self, pk):
        return reverse("guild-lfg-role-edit", args=[self.guild.guild_id, pk])

    def _delete_url(self, pk):
        return reverse("guild-lfg-role-delete", args=[self.guild.guild_id, pk])

    def test_get_add_returns_the_form(self):
        """The Add button hx-gets the blank form; this used to be a 405."""
        response = self._with_discord(lambda _: self.client.get(self._add_url()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="lfg-role-form"')
        self.assertContains(response, self._add_url())

    def test_get_edit_returns_bound_form_and_display_returns_row(self):
        role = GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")

        response = self._with_discord(lambda _: self.client.get(self._edit_url(role.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="lfg-role-form"')
        self.assertContains(response, self._edit_url(role.pk))

        # ?display=1 still swaps back to the read-only row.
        response = self._with_discord(
            lambda _: self.client.get(self._edit_url(role.pk) + "?display=1"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="lfg-role-row-%d"' % role.pk)

    def test_valid_add_creates_role_and_signals_success(self):
        response = self._with_discord(
            lambda _: self.client.post(self._add_url(), {"role_id": "100000000000000011"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "lfgRoleSaved")

        role = GuildLFGRole.objects.get(guild=self.guild)
        # `name` is derived from the picked role's real Discord name.
        self.assertEqual(role.name, "Nightfall")
        # The row, the Add button and the tag map all refresh out-of-band.
        self.assertContains(response, 'id="lfg-role-row-%d"' % role.pk)
        self.assertContains(response, 'id="lfg-add-controls"')
        self.assertContains(response, 'id="lfg-forum-tags-wrap"')

    def test_valid_edit_updates_row_out_of_band(self):
        role = GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")
        response = self._with_discord(
            lambda _: self.client.post(self._edit_url(role.pk),
                                       {"role_id": "100000000000000011", "description": "evening games"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "lfgRoleSaved")
        role.refresh_from_db()
        self.assertEqual(role.description, "evening games")
        # An edit replaces the row in place rather than appending a second one.
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertNotContains(response, 'hx-swap-oob="beforeend"')

    def test_invalid_add_returns_422_so_the_modal_stays_open(self):
        """422 is what lfg_role_modal.js opts into swapping; a 200 here would let the
        modal close on an unsaved form."""
        response = self._with_discord(lambda _: self.client.post(self._add_url(), {}))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'id="lfg-role-form"', status_code=422)
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(GuildLFGRole.objects.exists())

    def test_duplicate_role_is_rejected(self):
        GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")
        response = self._with_discord(
            lambda _: self.client.post(self._add_url(), {"role_id": "100000000000000011"}))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "already has an LFG entry", status_code=422)
        self.assertEqual(GuildLFGRole.objects.count(), 1)

    def test_add_button_disables_at_the_tag_limit(self):
        from the_gatehouse.services.discord_commands import LFG_TAG_LIMIT
        for i in range(LFG_TAG_LIMIT):
            GuildLFGRole.objects.create(guild=self.guild, name="Role %d" % i,
                                        role_id=str(100000000000001000 + i))
        response = self._with_discord(
            lambda _: self.client.get(reverse("edit-guild", args=[self.guild.guild_id])))
        self.assertContains(response, "at most %d LFG tags" % LFG_TAG_LIMIT)

    def test_delete_removes_role_and_signals_success(self):
        role = GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")
        response = self._with_discord(
            lambda _: self.client.post(self._delete_url(role.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "lfgRoleSaved")
        self.assertFalse(GuildLFGRole.objects.filter(pk=role.pk).exists())
        self.assertContains(response, 'id="lfg-add-controls"')

    def test_delete_requires_post(self):
        role = GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")
        response = self._with_discord(lambda _: self.client.get(self._delete_url(role.pk)))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(GuildLFGRole.objects.filter(pk=role.pk).exists())

    def test_non_moderator_is_denied(self):
        role = GuildLFGRole.objects.create(guild=self.guild, name="Nightfall", role_id="100000000000000011")
        outsider = User.objects.create_user(username="outsider", password="pw")
        outsider.profile.group = "P"
        outsider.profile.player_onboard = True
        outsider.profile.save()
        self.client.force_login(outsider)

        for method, url in (("get", self._add_url()),
                            ("get", self._edit_url(role.pk)),
                            ("post", self._add_url()),
                            ("post", self._delete_url(role.pk))):
            with self.subTest(method=method, url=url):
                response = self._with_discord(
                    lambda _: getattr(self.client, method)(url))
                self.assertEqual(response.status_code, 403)
        self.assertTrue(GuildLFGRole.objects.filter(pk=role.pk).exists())


class TournamentChannelModalViewTests(_NoLoginSignalMixin, TestCase):
    """A series' Discord channels are edited from the Edit Guild page, in a modal that
    commits on its own. results/schedule are TEXT channels and game_threads is a FORUM
    channel, validated against separate lists."""

    TEXT = [{"id": "200000000000000011", "name": "results"},
            {"id": "200000000000000022", "name": "schedule"}]
    FORUM = [{"id": "300000000000000033", "name": "matches"}]

    def setUp(self):
        super().setUp()
        self.guild = DiscordGuild.objects.create(guild_id="700200", name="Channel Guild",
                                                 bot_member=True)
        self.user = User.objects.create_user(username="chanmod", password="pw")
        self.profile = self.user.profile
        self.profile.group = "P"          # plain player; moderates via guild_moderators
        self.profile.player_onboard = True
        self.profile.save()
        self.guild.guild_moderators.add(self.profile)
        self.client.force_login(self.user)
        self.tournament = Tournament.objects.create(name="Channel Cup", guild=self.guild)

    def _mock_discord(self, text=None, forum=None):
        """Patch the Discord reads on the views AND forms modules: views.py imports them
        by name, and the form does a function-local import from the service module."""
        text = self.TEXT if text is None else text
        forum = self.FORUM if forum is None else forum
        return [
            mock.patch("the_gatehouse.views.get_guild_text_channels", return_value=text),
            mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=forum),
            mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                       return_value=text),
            mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                       return_value=forum),
        ]

    def _with_discord(self, fn, text=None, forum=None):
        patches = self._mock_discord(text=text, forum=forum)
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in patches:
                p.stop()

    def _url(self, pk=None):
        return reverse("guild-tournament-channels",
                       args=[self.guild.guild_id, pk or self.tournament.pk])

    def test_get_returns_form_with_both_lists_kept_separate(self):
        response = self._with_discord(lambda: self.client.get(self._url()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="tournament-channels-form"')
        # The forum channel is offered for game_threads, the text ones for the others.
        self.assertContains(response, "#results")
        self.assertContains(response, "matches")

    def test_valid_post_saves_all_three_and_signals_success(self):
        response = self._with_discord(lambda: self.client.post(self._url(), {
            "results_channel": self.TEXT[0]["id"],
            "schedule_channel": self.TEXT[1]["id"],
            "game_threads_channel": self.FORUM[0]["id"],
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "tournamentChannelsSaved")

        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.results_channel, self.TEXT[0]["id"])
        self.assertEqual(self.tournament.schedule_channel, self.TEXT[1]["id"])
        self.assertEqual(self.tournament.game_threads_channel, self.FORUM[0]["id"])
        # The row refreshes out-of-band, showing names rather than raw snowflakes.
        self.assertContains(response, 'id="tournament-channel-row-%d"' % self.tournament.pk)
        self.assertContains(response, "#results")

    def test_channel_outside_the_guild_is_rejected(self):
        response = self._with_discord(lambda: self.client.post(self._url(), {
            "results_channel": "999000000000000099",
        }))
        self.assertEqual(response.status_code, 422)
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.results_channel)

    def test_text_channel_cannot_be_saved_as_the_game_threads_forum(self):
        """The two lists really are kept separate — a text channel in the forum field
        would break thread creation at runtime, so it must fail at save time."""
        response = self._with_discord(lambda: self.client.post(self._url(), {
            "game_threads_channel": self.TEXT[0]["id"],
        }))
        self.assertEqual(response.status_code, 422)
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.game_threads_channel)

    def test_unreachable_discord_does_not_block_a_save(self):
        """A failed fetch (None) skips that field's check rather than hard-blocking a
        legitimate save — the same degrade-don't-block guard the LFG form uses."""
        patches = [
            mock.patch("the_gatehouse.views.get_guild_text_channels", return_value=None),
            mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=None),
            mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                       return_value=None),
            mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                       return_value=None),
        ]
        for p in patches:
            p.start()
        try:
            response = self.client.post(self._url(), {
                "results_channel": "999000000000000099",
            })
            self.assertEqual(response.status_code, 200)
            self.tournament.refresh_from_db()
            self.assertEqual(self.tournament.results_channel, "999000000000000099")
        finally:
            for p in patches:
                p.stop()

    def test_an_empty_channel_list_still_rejects(self):
        """An empty list is a SUCCESSFUL fetch of a guild with no such channels — it
        must reject, unlike None. Guards against a falsy-vs-None mixup in clean()."""
        response = self._with_discord(lambda: self.client.post(self._url(), {
            "results_channel": "999000000000000099",
        }), text=[], forum=[])
        self.assertEqual(response.status_code, 422)

    def test_mixed_fetch_failure_checks_only_the_list_that_loaded(self):
        """One list failing must not degrade the other's validation."""
        patches = [
            mock.patch("the_gatehouse.views.get_guild_text_channels", return_value=None),
            mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=self.FORUM),
            mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                       return_value=None),
            mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                       return_value=self.FORUM),
        ]
        for p in patches:
            p.start()
        try:
            # Text fetch failed -> an unknown text channel is accepted.
            # Forum fetch worked -> a bad forum channel is still rejected.
            response = self.client.post(self._url(), {
                "results_channel": "999000000000000099",
                "game_threads_channel": self.TEXT[0]["id"],
            })
            self.assertEqual(response.status_code, 422)

            response = self.client.post(self._url(), {
                "results_channel": "999000000000000099",
                "game_threads_channel": self.FORUM[0]["id"],
            })
            self.assertEqual(response.status_code, 200)
            self.tournament.refresh_from_db()
            self.assertEqual(self.tournament.results_channel, "999000000000000099")
        finally:
            for p in patches:
                p.stop()

    def test_blank_values_clear_the_fields(self):
        self.tournament.results_channel = self.TEXT[0]["id"]
        self.tournament.game_threads_channel = self.FORUM[0]["id"]
        self.tournament.save()

        response = self._with_discord(lambda: self.client.post(self._url(), {
            "results_channel": "", "schedule_channel": "", "game_threads_channel": "",
        }))
        self.assertEqual(response.status_code, 200)
        self.tournament.refresh_from_db()
        # Normalized to NULL, not an empty string.
        self.assertIsNone(self.tournament.results_channel)
        self.assertIsNone(self.tournament.game_threads_channel)

    def test_a_series_in_another_guild_is_not_reachable(self):
        other_guild = DiscordGuild.objects.create(guild_id="700999", name="Other",
                                                  bot_member=True)
        other = Tournament.objects.create(name="Not Yours", guild=other_guild)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self._with_discord(
                    lambda: getattr(self.client, method)(self._url(other.pk)))
                self.assertEqual(response.status_code, 404)

    def test_non_moderator_is_denied(self):
        other = User.objects.create_user(username="outsider", password="pw")
        other.profile.player_onboard = True
        other.profile.save()
        self.client.force_login(other)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = self._with_discord(
                    lambda: getattr(self.client, method)(self._url()))
                self.assertEqual(response.status_code, 403)

    def test_edit_guild_page_lists_linked_series(self):
        response = self._with_discord(
            lambda: self.client.get(reverse("edit-guild", args=[self.guild.guild_id])))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Channel Cup")
        self.assertContains(response, 'id="tournament-channels-modal"')


class ScheduleChannelAnnounceTests(ScheduleFixtureMixin, TestCase):
    """A confirmed time is announced in the tournament's schedule_channel. Both
    /schedule write paths post: the consensus commit point and the direct write."""

    CHANNEL = "200000000000000011"
    TEXT = [{"id": CHANNEL, "name": "schedule"}]

    def setUp(self):
        self.build(populate_group=True)
        self.guild.bot_member = True
        self.guild.save(update_fields=["bot_member"])
        Tournament.objects.filter(pk=self.tournament.pk).update(
            schedule_channel=self.CHANNEL)
        self.tournament.refresh_from_db()
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)

    def _capture(self, fn):
        """Run fn with Discord reads stubbed, returning the posted content or None."""
        from the_gatehouse import tasks
        with mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                        return_value=self.TEXT), \
             mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"), \
             mock.patch.object(tasks.post_channel_message_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                fn()
        # The thread post uses the same task, so pick out the schedule-channel call.
        for call in delay.call_args_list:
            if call.args[0] == self.CHANNEL:
                return call.args[1]
        return None

    def _proposal(self, when):
        proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=when, proposed_by=self.player,
            channel_id="555000111", message_id="111",
            guild_id=self.guild.guild_id)
        proposal.roster.set([self.player, self.teammate])
        proposal.confirmed_by.set([self.player, self.teammate])
        return proposal

    def test_consensus_path_announces_scheduled(self):
        proposal = self._proposal(self.when)
        content = self._capture(lambda: di._finalize_proposal(proposal))
        self.assertIsNotNone(content)
        self.assertIn("is scheduled for", content)
        self.assertIn(self.group.name, content)
        self.assertIn(f"<t:{int(self.when.timestamp())}:F>", content)

    def test_a_changed_time_says_rescheduled(self):
        self.match.scheduled_time = self.when - timedelta(days=1)
        self.match.save(update_fields=["scheduled_time"])
        proposal = self._proposal(self.when)
        content = self._capture(lambda: di._finalize_proposal(proposal))
        self.assertIn("is rescheduled for", content)

    def test_confirming_the_same_time_announces_nothing(self):
        """Re-confirming a slot that didn't move isn't news."""
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])
        proposal = self._proposal(self.when)
        content = self._capture(lambda: di._finalize_proposal(proposal))
        self.assertIsNone(content)

    def test_no_schedule_channel_means_no_post(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(schedule_channel=None)
        proposal = self._proposal(self.when)
        content = self._capture(lambda: di._finalize_proposal(proposal))
        self.assertIsNone(content)

    def test_stale_channel_from_another_guild_is_refused(self):
        other = DiscordGuild.objects.create(guild_id="900999", name="Other",
                                            bot_member=True)
        Tournament.objects.filter(pk=self.tournament.pk).update(guild=other)
        proposal = self._proposal(self.when)
        # The stubbed list belongs to the tournament's *current* guild and doesn't
        # contain the stored id, so the post is refused.
        from the_gatehouse import tasks
        with mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                        return_value=[]), \
             mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"), \
             mock.patch.object(tasks.post_channel_message_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                di._finalize_proposal(proposal)
        for call in delay.call_args_list:
            self.assertNotEqual(call.args[0], self.CHANNEL)


class TournamentChannelSecurityTests(_NoLoginSignalMixin, TestCase):
    """The channel ids are bare snowflakes with no guild in them, so a tournament that
    moves to another guild must not keep announcing into the old one."""

    TEXT = [{"id": "200000000000000011", "name": "results"}]
    FORUM = [{"id": "300000000000000033", "name": "matches"}]

    def setUp(self):
        super().setUp()
        self.guild_a = DiscordGuild.objects.create(guild_id="800100", name="Guild A",
                                                   bot_member=True)
        self.guild_b = DiscordGuild.objects.create(guild_id="800200", name="Guild B",
                                                   bot_member=True)
        self.tournament = Tournament.objects.create(
            name="Sec Cup", guild=self.guild_a,
            results_channel=self.TEXT[0]["id"],
            schedule_channel=self.TEXT[0]["id"],
            game_threads_channel=self.FORUM[0]["id"])

    # ---- Tournament.save() clears the ids on a guild change --------------------

    def test_repointing_the_guild_clears_every_channel(self):
        self.tournament.guild = self.guild_b
        self.tournament.save()
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.results_channel)
        self.assertIsNone(self.tournament.schedule_channel)
        self.assertIsNone(self.tournament.game_threads_channel)

    def test_clearing_the_guild_clears_every_channel(self):
        self.tournament.guild = None
        self.tournament.save()
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.results_channel)
        self.assertIsNone(self.tournament.game_threads_channel)

    def test_unrelated_save_keeps_the_channels(self):
        self.tournament.name = "Renamed"
        self.tournament.save()
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.results_channel, self.TEXT[0]["id"])

    def test_update_fields_including_guild_still_persists_the_clear(self):
        """update_fields would otherwise drop the nulled columns from the UPDATE and
        silently keep the stale ids -- the exact failure the clearing exists to stop."""
        self.tournament.guild = self.guild_b
        self.tournament.save(update_fields=['guild'])
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.results_channel)
        self.assertIsNone(self.tournament.schedule_channel)
        self.assertIsNone(self.tournament.game_threads_channel)

    def test_unrelated_partial_save_is_unaffected(self):
        self.tournament.use_stages = True
        self.tournament.save(update_fields=['use_stages'])
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.use_stages)
        self.assertEqual(self.tournament.results_channel, self.TEXT[0]["id"])

    # ---- Send-time verification -----------------------------------------------

    def _post(self, field='results_channel', text=None, forum=None):
        from the_gatehouse import tasks
        from the_warroom.services.channel_posts import post_to_tournament_channel
        with mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                        return_value=self.TEXT if text is None else text), \
             mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                        return_value=self.FORUM if forum is None else forum), \
             mock.patch.object(tasks.post_channel_message_task, "delay") as delay:
            sent = post_to_tournament_channel(self.tournament, field, "hi")
        return sent, delay

    def test_posts_when_the_channel_belongs_to_the_current_guild(self):
        sent, delay = self._post()
        self.assertTrue(sent)
        delay.assert_called_once_with(self.TEXT[0]["id"], "hi")

    def test_refuses_a_channel_that_is_not_in_the_current_guild(self):
        """The leak: the tournament moved to guild B but still holds A's channel id.
        (save() clears it now; this covers rows that drifted before that existed.)"""
        Tournament.objects.filter(pk=self.tournament.pk).update(guild=self.guild_b)
        self.tournament.refresh_from_db()
        sent, delay = self._post(text=[])   # guild B has no such channel
        self.assertFalse(sent)
        delay.assert_not_called()

    def test_fails_closed_when_discord_is_unreachable(self):
        """A None list means the fetch failed -> unverified -> refuse. This is the
        deliberate inverse of the settings form, which lets an outage through."""
        from the_gatehouse import tasks
        from the_warroom.services.channel_posts import post_to_tournament_channel
        with mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                        return_value=None), \
             mock.patch.object(tasks.post_channel_message_task, "delay") as delay:
            sent = post_to_tournament_channel(self.tournament, 'results_channel', "hi")
        self.assertFalse(sent)
        delay.assert_not_called()

    def test_empty_list_also_refuses(self):
        """An empty list is a SUCCESSFUL fetch of a guild with no text channels."""
        sent, delay = self._post(text=[])
        self.assertFalse(sent)
        delay.assert_not_called()

    def test_no_guild_means_no_post(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(guild=None)
        self.tournament.refresh_from_db()
        sent, delay = self._post()
        self.assertFalse(sent)
        delay.assert_not_called()

    def test_game_threads_is_checked_against_the_forum_list(self):
        """A text channel must never satisfy the forum-only field."""
        sent, delay = self._post(field='game_threads_channel')
        self.assertTrue(sent)

        Tournament.objects.filter(pk=self.tournament.pk).update(
            game_threads_channel=self.TEXT[0]["id"])
        self.tournament.refresh_from_db()
        sent, delay = self._post(field='game_threads_channel')
        self.assertFalse(sent)
        delay.assert_not_called()


class RefreshGuildCacheTests(_NoLoginSignalMixin, TestCase):
    """Discord reads are cached ~5 min with no invalidation, so a channel created
    moments ago is invisible (and unpostable) until the TTL lapses. The Refresh button
    is the escape hatch."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.guild = DiscordGuild.objects.create(guild_id="960100", name="Cache Guild",
                                                 bot_member=True)
        self.user = User.objects.create_user(username="cachemod", password="pw")
        self.profile = self.user.profile
        self.profile.group = "P"
        self.profile.player_onboard = True
        self.profile.save()
        self.guild.guild_moderators.add(self.profile)
        self.client.force_login(self.user)

    def _url(self, guild_id=None):
        return reverse("guild-refresh-discord", args=[guild_id or self.guild.guild_id])

    def test_clears_every_guild_scoped_key(self):
        from the_gatehouse.services.discordservice import refresh_guild_cache
        gid = self.guild.guild_id
        for key in (f"bot_in_guild:{gid}", f"guild_roles:{gid}",
                    f"guild_role_perms:{gid}", f"guild_object:{gid}",
                    f"guild_forum_channels:{gid}", f"guild_text_channels:{gid}"):
            cache.set(key, ["stale"], 300)
        refresh_guild_cache(gid)
        for key in (f"bot_in_guild:{gid}", f"guild_roles:{gid}",
                    f"guild_role_perms:{gid}", f"guild_object:{gid}",
                    f"guild_forum_channels:{gid}", f"guild_text_channels:{gid}"):
            self.assertIsNone(cache.get(key), key)

    def test_clears_per_forum_tag_entries(self):
        """forum_channel_info is keyed by CHANNEL id, not guild id — clearing only the
        guild keys would leave newly-added forum tags stale."""
        from the_gatehouse.services.discordservice import refresh_guild_cache
        gid = self.guild.guild_id
        cache.set(f"guild_forum_channels:{gid}", [{"id": "555", "name": "f"}], 300)
        cache.set("forum_channel_info:555", {"is_forum": True, "tags": []}, 300)
        refresh_guild_cache(gid)
        self.assertIsNone(cache.get("forum_channel_info:555"))

    def test_leaves_another_guilds_cache_alone(self):
        from the_gatehouse.services.discordservice import refresh_guild_cache
        cache.set("guild_roles:999999", ["keep"], 300)
        refresh_guild_cache(self.guild.guild_id)
        self.assertEqual(cache.get("guild_roles:999999"), ["keep"])

    def test_post_refreshes_and_redirects(self):
        gid = self.guild.guild_id
        cache.set(f"guild_roles:{gid}", ["stale"], 300)
        with mock.patch("the_gatehouse.views.bot_in_guild", return_value=True):
            response = self.client.post(self._url())
        self.assertRedirects(response, reverse("edit-guild", args=[gid]),
                             fetch_redirect_response=False)
        self.assertIsNone(cache.get(f"guild_roles:{gid}"))

    def test_reprobes_bot_membership(self):
        """bot_member is a DB column, not a cache entry — refresh re-probes it so a
        just-invited bot stops forcing the manual-entry fallbacks."""
        self.guild.bot_member = False
        self.guild.save(update_fields=["bot_member"])
        with mock.patch("the_gatehouse.views.bot_in_guild", return_value=True):
            self.client.post(self._url())
        self.guild.refresh_from_db()
        self.assertTrue(self.guild.bot_member)

    def test_get_is_not_allowed(self):
        with mock.patch("the_gatehouse.views.bot_in_guild", return_value=True):
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 405)

    def test_non_moderator_is_forbidden(self):
        other = User.objects.create_user(username="cacheoutsider", password="pw")
        other.profile.player_onboard = True
        other.profile.save()
        self.client.force_login(other)
        gid = self.guild.guild_id
        cache.set(f"guild_roles:{gid}", ["stale"], 300)
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 403)
        # And nothing was cleared.
        self.assertEqual(cache.get(f"guild_roles:{gid}"), ["stale"])


class LFGCommandRefreshTests(_NoLoginSignalMixin, TestCase):
    """Every LFG-role change re-registers the guild's slash commands promptly, and a
    broker outage falls back to an inline PUT instead of silently dropping it."""

    def setUp(self):
        super().setUp()
        self.guild = DiscordGuild.objects.create(guild_id="700200", name="Refresh Guild",
                                                 bot_member=True)

    def test_enqueues_immediately_with_no_countdown(self):
        """The old countdown=10 debounce meant the refresh was never prompt."""
        with mock.patch("the_gatehouse.views.register_guild_commands_task") as task:
            self.assertTrue(views.refresh_guild_commands(self.guild))
        task.apply_async.assert_called_once_with((self.guild.id,))

    def test_broker_outage_falls_back_to_inline_registration(self):
        with mock.patch("the_gatehouse.views.register_guild_commands_task") as task, \
             mock.patch("the_gatehouse.views.register_guild_commands",
                        return_value=True) as inline:
            task.apply_async.side_effect = KombuOperationalError("redis down")
            self.assertTrue(views.refresh_guild_commands(self.guild))
        inline.assert_called_once_with(self.guild)

    def test_add_edit_and_delete_each_refresh_once(self):
        user = User.objects.create_user(username="refreshmod", password="pw")
        user.profile.group = "A"          # admin moderates every guild
        user.profile.player_onboard = True
        user.profile.save()
        self.client.force_login(user)

        roles = [{"id": "100000000000000011", "name": "Nightfall"}]
        with mock.patch("the_gatehouse.views.get_guild_roles", return_value=roles), \
             mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=[]), \
             mock.patch("the_gatehouse.views.get_forum_channel_info", return_value=None), \
             mock.patch("the_gatehouse.views.refresh_guild_commands") as refresh:
            self.client.post(reverse("guild-lfg-role-add", args=[self.guild.guild_id]),
                             {"role_id": "100000000000000011"})
            self.assertEqual(refresh.call_count, 1)

            role = GuildLFGRole.objects.get(guild=self.guild)
            self.client.post(reverse("guild-lfg-role-edit",
                                     args=[self.guild.guild_id, role.pk]),
                             {"role_id": "100000000000000011", "description": "x"})
            self.assertEqual(refresh.call_count, 2)

            self.client.post(reverse("guild-lfg-role-delete",
                                     args=[self.guild.guild_id, role.pk]))
            self.assertEqual(refresh.call_count, 3)


class LFGCommandShapeTests(TestCase):
    """/lfg is registered as SINGLE below 2 roles and MULTI at 2+ — the shape flip that
    makes a dropped re-registration obvious."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="700300", name="Shape Guild")

    def _roles(self, count, name="Role"):
        return [GuildLFGRole.objects.create(guild=self.guild, name="%s %d" % (name, i),
                                            role_id=str(100000000000000500 + i))
                for i in range(count)]

    def _type_option(self, cmd):
        return next((o for o in cmd["options"] if o["name"] == "type"), None)

    def test_zero_or_one_role_registers_the_single_variant(self):
        for count in (0, 1):
            with self.subTest(roles=count):
                GuildLFGRole.objects.all().delete()
                cmd = dc.lfg_command_for_roles(self._roles(count))
                self.assertIsNone(self._type_option(cmd))

    def test_two_roles_register_the_multi_variant_with_choices(self):
        roles = self._roles(2)
        cmd = dc.lfg_command_for_roles(roles)
        type_opt = self._type_option(cmd)
        self.assertIsNotNone(type_opt)
        self.assertTrue(type_opt["required"])
        self.assertEqual(type_opt["choices"],
                         [{"name": r.name, "value": str(r.pk)} for r in roles])

    def test_choices_truncate_to_the_discord_limit_without_mutating_the_singleton(self):
        roles = self._roles(dc.LFG_TAG_LIMIT + 1)
        cmd = dc.lfg_command_for_roles(roles)
        self.assertEqual(len(self._type_option(cmd)["choices"]), dc.LFG_TAG_LIMIT)
        # lfg_command_for_roles deep-copies; the shared module dict must stay pristine.
        self.assertEqual(self._type_option(dc.LFG_COMMAND_MULTI)["choices"], [])

    def test_long_role_names_are_truncated_for_discord(self):
        GuildLFGRole.objects.create(guild=self.guild, name="x" * 150, role_id="100000000000000999")
        roles = list(self.guild.lfg_roles.all()) + self._roles(1)
        cmd = dc.lfg_command_for_roles(roles)
        self.assertTrue(all(len(c["name"]) <= 100
                            for c in self._type_option(cmd)["choices"]))


class HelpCommandShapeTests(TestCase):
    """/help gains a `category` dropdown only where /lfg is enabled, mirroring the
    SINGLE/MULTI flip /lfg itself uses."""

    def _category_option(self, cmd):
        return next((o for o in cmd["options"] if o["name"] == "category"), None)

    def test_without_lfg_the_option_less_variant_is_registered(self):
        for enabled in ([], ["stats"], ["stats", "record"]):
            with self.subTest(enabled=enabled):
                cmd = dc.help_command_for_guild(enabled)
                self.assertIsNone(self._category_option(cmd))

    def test_with_lfg_an_optional_category_dropdown_is_registered(self):
        cmd = dc.help_command_for_guild(["lfg", "stats"])
        opt = self._category_option(cmd)
        self.assertIsNotNone(opt)
        # Optional so a bare /help keeps returning the command list, as in every other
        # server.
        self.assertFalse(opt["required"])

    def test_commands_is_listed_first(self):
        """Discord can't preselect a choice, so first-in-list is how Commands stays the
        obvious default."""
        opt = self._category_option(dc.help_command_for_guild(["lfg"]))
        self.assertEqual([c["value"] for c in opt["choices"]],
                         [dc.HELP_CATEGORY_COMMANDS, dc.HELP_CATEGORY_LFG])

    def test_mutating_a_returned_definition_leaves_the_singleton_pristine(self):
        cmd = dc.help_command_for_guild(["lfg"])
        self._category_option(cmd)["choices"].append({"name": "X", "value": "x"})
        self.assertEqual(len(self._category_option(dc.HELP_COMMAND_LFG)["choices"]), 2)

    def test_help_is_never_whitelistable(self):
        self.assertNotIn("help", dc.WHITELISTABLE)


class LFGHelpContentTests(TestCase):
    """The LFG walkthrough is shared by the Databot page and /help category:LFG, so the
    copy has to stay renderable by both."""

    def _bodies(self):
        return [dc.LFG_HELP_INTRO] + [s["body"] for s in dc.LFG_HELP_STEPS]

    def test_every_link_target_resolves(self):
        """A renamed URL would raise NoReverseMatch inside a slash-command response —
        a dead bot, not just a dead link."""
        for body in self._bodies():
            for _, url_name in _LFG_LINK_RE.findall(body):
                with self.subTest(url_name=url_name):
                    reverse(url_name)

    def test_every_referenced_command_exists(self):
        for step in dc.LFG_HELP_STEPS:
            for name, _ in step.get("commands", []):
                with self.subTest(command=name):
                    self.assertIn(name, dc.WHITELISTABLE)

    def test_embed_stays_within_discord_limits_without_truncating(self):
        embed = build_lfg_help_embed()
        total = len(embed["title"]) + len(embed["description"])
        for field in embed["fields"]:
            self.assertLess(len(field["name"]), 256)
            self.assertLess(len(field["value"]), 1024)
            # _enforce_embed_limits would clip with an ellipsis; nothing should hit it.
            self.assertFalse(field["value"].endswith("…"))
            total += len(field["name"]) + len(field["value"])
        self.assertLess(total, 6000)

    def test_embed_expands_every_link_to_a_real_url(self):
        embed = build_lfg_help_embed()
        rendered = embed["description"] + "".join(f["value"] for f in embed["fields"])
        self.assertFalse(_LFG_LINK_RE.search(rendered),
                         "an unexpanded [label](url-name) reached Discord")

    def test_filter_expands_every_markup_rule(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                html = lfg_body(body)
                self.assertNotIn("`", html)
                self.assertNotIn("](", html)
                self.assertNotIn("*", html)

    def test_filter_escapes_before_expanding_markup(self):
        html = lfg_body("<script>alert(1)</script> and `/record`")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn('<code class="databot-inline-cmd">/record</code>', html)

    def test_step_commands_render_as_chips_after_the_body(self):
        """Step 3's body ends in a colon introducing its chips."""
        step = next(s for s in dc.LFG_HELP_STEPS if s.get("commands"))
        value = build_lfg_help_embed()["fields"][dc.LFG_HELP_STEPS.index(step)]["value"]
        self.assertIn(step["body"].rstrip()[-20:], value)
        for name, _ in step["commands"]:
            self.assertIn(f"`/{name}`", value)


class HelpCommandHandlerTests(TestCase):
    """/help dispatches on `category`, defaulting to the command list."""

    def _help(self, **data):
        return json.loads(di._handle_help_command(data).content)["data"]

    def test_lfg_category_returns_the_walkthrough(self):
        data = self._help(options=[{"name": "category", "value": dc.HELP_CATEGORY_LFG}])
        self.assertEqual(data["embeds"][0]["title"], "How to Use LFG")

    def test_bare_help_still_returns_the_command_list(self):
        self.assertEqual(self._help()["embeds"][0]["title"], "Bot Commands")

    def test_commands_category_returns_the_command_list(self):
        data = self._help(options=[{"name": "category",
                                    "value": dc.HELP_CATEGORY_COMMANDS}])
        self.assertEqual(data["embeds"][0]["title"], "Bot Commands")

    def test_both_categories_stay_ephemeral(self):
        for options in ([], [{"name": "category", "value": dc.HELP_CATEGORY_LFG}]):
            with self.subTest(options=options):
                self.assertEqual(self._help(options=options)["flags"], di.EPHEMERAL)


class RegisterGuildCommandsBodyTests(TestCase):
    """The PUT body carries the per-guild variants of BOTH /help and /lfg — the two
    substitutions must not clobber each other."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="700500", name="Body Guild")

    def _body(self):
        captured = {}

        def fake_put(url, headers=None, json=None, timeout=None):
            captured["body"] = json
            return mock.Mock(raise_for_status=mock.Mock())

        with mock.patch.object(ds.requests, "put", side_effect=fake_put), \
             mock.patch.object(ds, "_bot_headers", return_value={}):
            self.assertTrue(ds.register_guild_commands(self.guild))
        return {c["name"]: c for c in captured["body"]}

    def _opts(self, cmd):
        return [o["name"] for o in cmd["options"]]

    def test_without_lfg_help_has_no_category(self):
        self.guild.enabled_commands = ["stats"]
        self.guild.save()
        body = self._body()
        self.assertEqual(self._opts(body["help"]), [])
        self.assertNotIn("lfg", body)

    def test_with_lfg_both_help_and_lfg_get_their_variants(self):
        """Regression: substituting /lfg off the pre-substitution list silently dropped
        the /help swap, so LFG guilds never got the category dropdown."""
        self.guild.enabled_commands = ["lfg"]
        self.guild.save()
        for _ in range(2):
            GuildLFGRole.objects.create(guild=self.guild, name="Tag %d" % _,
                                        role_id=str(100000000000000700 + _))
        body = self._body()
        self.assertIn("category", self._opts(body["help"]))
        self.assertIn("type", self._opts(body["lfg"]))


class EditGuildCommandSyncTests(_NoLoginSignalMixin, TestCase):
    """The guild form re-registers commands only when the enabled set actually changed."""

    def setUp(self):
        super().setUp()
        self.guild = DiscordGuild.objects.create(guild_id="700400", name="Sync Guild",
                                                 bot_member=True, enabled_commands=["lfg"])
        self.user = User.objects.create_user(username="syncadmin", password="pw")
        self.user.profile.group = "A"
        self.user.profile.player_onboard = True
        self.user.profile.save()
        self.client.force_login(self.user)
        self.url = reverse("edit-guild", args=[self.guild.guild_id])

    def _post(self, commands, register_ok=True):
        with mock.patch("the_gatehouse.views.get_guild_roles", return_value=[]), \
             mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=[]), \
             mock.patch("the_gatehouse.views.get_forum_channel_info", return_value=None), \
             mock.patch("the_gatehouse.views.register_guild_commands",
                        return_value=register_ok) as register:
            response = self.client.post(self.url, {"enabled_commands": commands},
                                        follow=True)
        return response, register

    def test_unchanged_command_set_does_not_re_register(self):
        _, register = self._post(["lfg"])
        register.assert_not_called()

    def test_changed_command_set_re_registers_once(self):
        _, register = self._post([])
        register.assert_called_once()
        self.guild.refresh_from_db()
        self.assertEqual(self.guild.enabled_commands, [])

    def test_discord_rejection_warns_the_user(self):
        response, register = self._post([], register_ok=False)
        register.assert_called_once()
        self.assertTrue(any("didn't accept" in str(m) for m in response.context["messages"]))


class DraftLFGSeatingTests(TestCase):
    """/draft inside an LFG game thread: the player count defaults to the thread's
    roster, and the drafter is offered a random seating for it."""

    THREAD_ID = "thread-900"

    def setUp(self):
        # Saving a Faction fires handle_image_resize, which rewrites the animal's
        # image IN PLACE under media/ — for a stock animal that's the shared
        # default_images file, which repeated test runs then truncate. Nothing here
        # tests image handling, so disconnect it for the duration.
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        designer = Profile.objects.create(discord="draftdesigner", discord_id="800")
        # Enough official/Stable factions for a 6-player draft (needs players + 1),
        # all Militant so 2-player drafts (Militant-only) work from the same pool.
        for i in range(8):
            Faction.objects.create(
                title=f"Draft Faction {i}", animal="Fox", designer=designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT,
            )
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)

    def _roster(self, count):
        profiles = [
            Profile.objects.create(discord=f"player{i}", discord_id=f"90{i}",
                                   display_name=f"Player {i}")
            for i in range(1, count + 1)
        ]
        self.thread.players.set(profiles)
        return profiles

    # ── player-count default ────────────────────────────────────────────────
    def _command(self, channel_id, players=None):
        data = {"_channel_id": channel_id, "_author_id": "111"}
        if players is not None:
            data["options"] = [{"name": "players", "value": players}]
        response = di._handle_draft_command(data)
        return json.loads(response.content)["data"]["content"]

    def test_count_defaults_to_the_threads_roster(self):
        self._roster(5)
        self.assertIn("**5 Player Draft**", self._command(self.THREAD_ID))

    def test_explicit_option_beats_the_roster(self):
        self._roster(5)
        self.assertIn("**3 Player Draft**", self._command(self.THREAD_ID, players=3))

    def test_non_lfg_channel_keeps_the_old_default(self):
        self._roster(5)
        self.assertIn("**4 Player Draft**", self._command("some-other-channel"))

    def test_roster_outside_the_supported_range_is_clamped(self):
        self._roster(7)
        self.assertIn("**6 Player Draft**", self._command(self.THREAD_ID))
        self.thread.players.set(self.thread.players.all()[:1])
        self.assertIn("**2 Player Draft**", self._command(self.THREAD_ID))

    def test_empty_roster_falls_back_to_four(self):
        self.assertIn("**4 Player Draft**", self._command(self.THREAD_ID))

    def test_group_thread_counts_the_groups_roster(self):
        """Regression: a tournament group thread's LFGThread has an EMPTY players
        M2M (players.set only runs on the /lfg path), so /draft read 0 and silently
        opened a 4-player draft for a table of 3."""
        guild = DiscordGuild.objects.create(guild_id="1093259831470735599",
                                            name="Draft Guild")
        tournament = Tournament.objects.create(
            name="Draft Tournament", guild=guild,
            designer=Profile.objects.create(discord="dgd", discord_id="8001"))
        stage = Stage.objects.create(tournament=tournament, name="S1", order=1)
        rnd = Round.objects.create(stage=stage, round_number=1)
        thread_id = "1303834523347456099"
        group = PlayerGroup.objects.create(
            round=rnd, group_number=1, name="Draft Group",
            discord_thread=(
                f"https://discord.com/channels/{guild.guild_id}/{thread_id}"))
        series = MatchSeries.objects.create(
            round=rnd, player_group=group, number_of_games=1)
        # A group thread's LFGThread: series set, players empty.
        LFGThread.objects.create(thread_id=thread_id, series=series)
        group.tournament_players.set([
            TournamentPlayer.objects.create(
                tournament=tournament,
                profile=Profile.objects.create(
                    discord=f"dgp{i}", discord_id=f"81{i}"))
            for i in range(3)
        ])

        data = {"_channel_id": thread_id, "_author_id": "111",
                "_guild_id": guild.guild_id}
        content = json.loads(
            di._handle_draft_command(data).content)["data"]["content"]
        self.assertIn("**3 Player Draft**", content)

    # ── the ephemeral seating offer ─────────────────────────────────────────
    def _build_payload(self, channel_id, guild_id=None):
        payload = {
            "channel_id": channel_id,
            "token": "tok",
            "data": {"custom_id": di.encode_custom_id("draft_build", 3, "tts", "111")},
            "message": {"id": "msg", "components": []},
        }
        if guild_id is not None:
            payload["guild_id"] = guild_id
        return payload

    def _offer(self, channel_id):
        with mock.patch.object(di.post_interaction_followup_task, "apply_async") as enqueue:
            di._handle_draft_build(self._build_payload(channel_id))
        return enqueue

    def test_lfg_thread_offers_seating(self):
        self._roster(3)
        enqueue = self._offer(self.THREAD_ID)
        enqueue.assert_called_once()
        message = enqueue.call_args.args[0][1]
        self.assertEqual(message["flags"], di.EPHEMERAL)
        self.assertEqual(message["content"], "Seat the players for this game?")
        buttons = message["components"][0]["components"]
        self.assertEqual(buttons[0]["label"], "Yes")
        self.assertTrue(buttons[0]["custom_id"].startswith("draft_seat:"))

    def test_draft_result_itself_is_unchanged(self):
        self._roster(3)
        with mock.patch.object(di.post_interaction_followup_task, "apply_async"):
            response = di._handle_draft_build(self._build_payload(self.THREAD_ID))
        data = json.loads(response.content)["data"]
        self.assertEqual(data["components"], [])
        self.assertEqual(data["content"], "")
        self.assertEqual(len(data["embeds"]), 1)

    def test_no_offer_outside_an_lfg_thread(self):
        self._roster(3)
        self._offer("some-other-channel").assert_not_called()

    def test_broker_outage_costs_the_prompt_not_the_draft(self):
        """The prompt is an optional extra on an already-successful draft."""
        self._roster(3)
        with mock.patch.object(di.post_interaction_followup_task, "apply_async",
                               side_effect=KombuOperationalError("redis down")):
            response = di._handle_draft_build(self._build_payload(self.THREAD_ID))
        data = json.loads(response.content)["data"]
        self.assertEqual(len(data["embeds"]), 1)  # draft still delivered

    def test_no_offer_when_the_roster_is_too_small_to_seat(self):
        self._roster(1)
        self._offer(self.THREAD_ID).assert_not_called()

    def test_the_offer_is_skipped_when_the_guild_lacks_seating(self):
        """Confirming would run seating the guild deliberately turned off."""
        self._roster(3)
        guild = DiscordGuild.objects.create(guild_id="950000000000000001", name="G",
                                            enabled_commands=["draft"])
        with mock.patch.object(di.post_interaction_followup_task,
                               "apply_async") as enqueue:
            response = di._handle_draft_build(
                self._build_payload(self.THREAD_ID, guild_id=guild.guild_id))
        enqueue.assert_not_called()
        # The draft itself is unaffected.
        self.assertTrue(json.loads(response.content)["data"]["embeds"])

    def test_the_offer_is_sent_when_the_guild_has_seating(self):
        self._roster(3)
        guild = DiscordGuild.objects.create(guild_id="950000000000000002", name="G",
                                            enabled_commands=["draft", "seating"])
        with mock.patch.object(di.post_interaction_followup_task,
                               "apply_async") as enqueue:
            di._handle_draft_build(
                self._build_payload(self.THREAD_ID, guild_id=guild.guild_id))
        enqueue.assert_called_once()

    def test_no_offer_when_the_thread_is_already_seated(self):
        """A reroll must not assume the drafter wants to reseat the table: the
        prompt's confirm button REPLACES the current order. Reseating stays
        available through /seating, which keeps its Overwrite warning."""
        self._roster(3)
        LFGSeat.objects.create(
            thread=self.thread,
            profile=Profile.objects.get(discord_id="901"),
            seat_number=1)
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])
        self._offer(self.THREAD_ID).assert_not_called()

    def test_filler_seats_from_pick_still_get_the_offer(self):
        """/pick can assign factions without seating, leaving seat rows with filler
        numbers and seating_set False -- that table has never been seated, so the
        offer must still go out. Keying the skip on seats.exists() would break it."""
        profiles = self._roster(3)
        for i, profile in enumerate(profiles, 1):
            LFGSeat.objects.create(thread=self.thread, profile=profile, seat_number=i)
        self._offer(self.THREAD_ID).assert_called_once()

    # ── seating ─────────────────────────────────────────────────────────────
    def _seat(self, channel_id=None):
        payload = {"channel_id": channel_id or self.THREAD_ID,
                   "data": {"custom_id": di.encode_custom_id("draft_seat", "111")}}
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            response = di._handle_draft_seat(payload)
        return response, post

    def test_reseating_an_already_seated_thread_bumps_last_activity(self):
        """The seating_set flag is already True on a re-seat, so the save that
        keeps last_activity fresh must not be conditional on it -- otherwise an
        actively-reseated table ages toward the cleanup task."""
        self._roster(3)
        self._seat()
        stale = timezone.now() - timedelta(days=200)
        LFGThread.objects.filter(pk=self.thread.pk).update(last_activity=stale)

        self._seat()

        self.thread.refresh_from_db()
        self.assertTrue(self.thread.seating_set)
        self.assertGreater(self.thread.last_activity, stale)

    def test_seating_persists_and_posts(self):
        self._roster(3)
        response, post = self._seat()

        seats = list(self.thread.seats.select_related("profile"))
        self.assertEqual([s.seat_number for s in seats], [1, 2, 3])
        self.assertEqual({s.profile.discord_id for s in seats}, {"901", "902", "903"})

        channel_id, body = post.call_args.args
        self.assertEqual(channel_id, self.THREAD_ID)
        self.assertTrue(
            body.endswith(f"{seats[-1].profile.name} has first pick of the faction draft"))
        self.assertIn(f"1. {seats[0].profile.name}", body)
        self.assertNotIn("Re-seated", body)
        self.assertEqual(json.loads(response.content)["data"]["content"], "Seating posted.")

    def test_reseating_replaces_the_previous_order_and_says_so(self):
        self._roster(3)
        self._seat()
        # Materialize NOW: a queryset would re-evaluate after the delete below and
        # the "not appended" assertion would compare against the new rows.
        first_ids = list(self.thread.seats.values_list("pk", flat=True))

        _response, post = self._seat()
        seats = list(self.thread.seats.all())
        # Fully replaced, not appended.
        self.assertEqual(len(seats), 3)
        self.assertEqual([s.seat_number for s in seats], [1, 2, 3])
        self.assertFalse(set(first_ids) & {s.pk for s in seats})
        self.assertTrue(post.call_args.args[1].startswith("**Re-seated**"))

    def test_seating_is_randomised(self):
        """Seats must not simply follow roster order. With 4 players, 10 rounds all
        landing on the same order has probability (1/24)^9 — vanishingly small."""
        self._roster(4)
        orders = set()
        for _ in range(10):
            self._seat()
            seats = self.thread.seats.select_related("profile")
            # Re-seating replaces, so the count must stay at 4 -- never accumulate.
            self.assertEqual(len(seats), 4)
            orders.add(tuple(s.profile.discord_id for s in seats))
        self.assertGreater(len(orders), 1)

    def test_seat_declined_changes_nothing(self):
        self._roster(3)
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            response = di._handle_draft_seat_no({"channel_id": self.THREAD_ID})
        post.assert_not_called()
        # assertEqual(manager, []) would silently pass -- a RelatedManager is never
        # equal to a list. Assert on existence instead.
        self.assertFalse(self.thread.seats.exists())
        data = json.loads(response.content)["data"]
        self.assertEqual(data["components"], [])

    def test_seating_a_vanished_thread_is_refused(self):
        response, post = self._seat("gone")
        post.assert_not_called()
        self.assertIn("game thread", json.loads(response.content)["data"]["content"])


class SeatingCommandTests(TestCase):
    """/seating: the seating half of /draft on its own. Shares the prompt builder
    and the draft_seat handler, so these cover the command's own guards and that
    it returns the prompt directly rather than as a followup."""

    THREAD_ID = "seat-cmd-thread"

    def setUp(self):
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)

    def _roster(self, count):
        profiles = [
            Profile.objects.create(discord=f"seatp{i}", discord_id=f"95{i}",
                                   display_name=f"Seat Player {i}")
            for i in range(1, count + 1)
        ]
        self.thread.players.set(profiles)
        return profiles

    def _command(self, channel_id=None, author="111"):
        response = di._handle_seating_command({
            "_channel_id": channel_id or self.THREAD_ID,
            "_author_id": author,
        })
        return json.loads(response.content)["data"]

    def test_command_is_registered_with_a_handler(self):
        self.assertIn("seating", dc.WHITELISTABLE)
        self.assertIn("seating", di.COMMAND_HANDLERS)

    def test_grouped_under_games_not_other(self):
        """A command missing from COMMAND_GROUPS silently lands in "Other"."""
        groups = {g: [n for n, _ in rows] for g, rows in dc.grouped_commands()}
        self.assertIn("seating", groups.get("Games", []))

    def test_outside_a_game_thread_explains_itself(self):
        self._roster(3)
        data = self._command("some-other-channel")
        self.assertIn("game thread", data["content"])
        self.assertNotIn("components", data)

    def test_roster_too_small_to_seat_is_refused(self):
        self._roster(1)
        data = self._command()
        self.assertIn("Not enough players", data["content"])
        self.assertNotIn("components", data)

    def test_prompt_is_returned_directly_not_as_a_followup(self):
        """Unlike /draft's offer, there is no earlier message to sequence after,
        so the prompt rides the command's own response — no Celery hop."""
        self._roster(3)
        with mock.patch.object(di.post_interaction_followup_task, "apply_async") as enqueue:
            data = self._command()
        enqueue.assert_not_called()
        self.assertEqual(data["flags"], di.EPHEMERAL)
        self.assertEqual(data["content"], "Seat the players for this game?")

    def test_buttons_are_owner_locked_to_the_invoker(self):
        self._roster(3)
        buttons = self._command(author="4242")["components"][0]["components"]
        self.assertEqual([b["label"] for b in buttons], ["Yes", "No"])
        self.assertEqual(buttons[0]["custom_id"], "draft_seat:4242")
        self.assertEqual(buttons[1]["custom_id"], "draft_seat_no:4242")

    def test_existing_seating_warns_before_overwriting(self):
        profiles = self._roster(3)
        LFGSeat.objects.create(thread=self.thread, profile=profiles[0], seat_number=1)
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])
        data = self._command()
        self.assertIn("overwrite", data["content"].lower())
        confirm = data["components"][0]["components"][0]
        self.assertEqual(confirm["label"], "Overwrite")
        self.assertEqual(confirm["style"], di.STYLE_DANGER)

    def test_confirming_the_prompt_seats_the_roster(self):
        """End to end through the shared draft_seat handler."""
        self._roster(4)
        custom_id = self._command()["components"][0]["components"][0]["custom_id"]
        payload = {"channel_id": self.THREAD_ID, "data": {"custom_id": custom_id}}
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            di._handle_draft_seat(payload)

        seats = list(self.thread.seats.select_related("profile"))
        self.assertEqual([s.seat_number for s in seats], [1, 2, 3, 4])
        self.assertEqual({s.profile.discord for s in seats},
                         {"seatp1", "seatp2", "seatp3", "seatp4"})
        channel_id, _body = post.call_args.args
        self.assertEqual(channel_id, self.THREAD_ID)


class SeatingCommandPlayerGroupTests(TestCase):
    """/seating in a tournament player group's thread. The group's thread spans a
    whole series, so the order is posted for display only and never stored."""

    GUILD_ID = "1093259831470735512"
    THREAD_ID = "1303834523347456040"

    def setUp(self):
        self.guild = DiscordGuild.objects.create(
            guild_id=self.GUILD_ID, name="Seating Guild")
        designer = Profile.objects.create(discord="grpdesigner", discord_id="640")
        self.tournament = Tournament.objects.create(
            name="Seating Tournament", guild=self.guild, designer=designer)
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name="Group A",
            discord_thread=(
                f"https://discord.com/channels/{self.GUILD_ID}/{self.THREAD_ID}"),
        )

    def _members(self, count):
        profiles = [
            Profile.objects.create(discord=f"grpp{i}", discord_id=f"64{i}",
                                   display_name=f"Group Player {i}")
            for i in range(1, count + 1)
        ]
        self.group.tournament_players.set([
            TournamentPlayer.objects.create(
                tournament=self.tournament, profile=p)
            for p in profiles
        ])
        return profiles

    def _command(self, channel_id=None):
        response = di._handle_seating_command({
            "_channel_id": channel_id or self.THREAD_ID,
            "_author_id": "111",
        })
        return json.loads(response.content)["data"]

    def _command_in_unlinked_thread(self, title, channel_id=None, guild=None):
        """/seating in a thread the group is NOT linked to, matched by title."""
        response = di._handle_seating_command({
            "_channel_id": channel_id or self.THREAD_ID,
            "_channel_name": title,
            "_guild_id": self.GUILD_ID if guild is None else guild,
            "_author_id": "111",
        })
        return json.loads(response.content)["data"]

    def test_group_thread_is_resolved_from_its_url(self):
        self.assertEqual(player_group_for_channel(self.THREAD_ID), self.group)

    def test_thread_id_cannot_match_part_of_the_guild_id(self):
        """The lookup anchors on a leading slash, so a guild-id fragment misses."""
        self.assertIsNone(player_group_for_channel(self.GUILD_ID[2:]))

    def test_seats_the_groups_players(self):
        self._members(4)
        content = self._command()["content"]
        for i in range(1, 5):
            self.assertIn(f"Group Player {i}", content)
        # The list no longer starts the string: the message opens with a
        # "**Seating**" title. Still require it to begin on its own line.
        self.assertIn("**Seating**", content)
        self.assertIn("\n1. ", content)

    def test_seating_is_persisted_so_pick_can_reuse_it(self):
        """A group seating is SAVED. It used to be display-only, which meant /pick
        found no seating, shuffled its own, and announced a second order
        contradicting the one players had just been shown."""
        profiles = self._members(4)
        self._command()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertTrue(thread.seating_set)
        seats = list(thread.seats.select_related("profile").order_by("seat_number"))
        self.assertEqual([s.seat_number for s in seats], [1, 2, 3, 4])
        self.assertEqual({s.profile_id for s in seats},
                         {p.pk for p in profiles})

    def test_pick_reuses_the_seating_instead_of_reshuffling(self):
        """The reported bug: /seating then /pick posted two different orders."""
        self._members(4)
        content = self._command()["content"]
        order = [line for line in content.splitlines()
                 if line[:2] in ("1.", "2.", "3.", "4.")]

        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            di._handle_pick_command({
                "_channel_id": self.THREAD_ID, "_author_id": "111",
                "_guild_id": self.GUILD_ID,
            })
        # /pick must not announce a seating of its own.
        post.assert_not_called()

        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        seats = thread.seats.select_related("profile").order_by("seat_number")
        self.assertEqual([f"{s.seat_number}. {s.profile.name}" for s in seats],
                         order)

    def test_reseating_says_it_replaced_the_previous_order(self):
        self._members(4)
        self._command()
        self.assertIn("Re-seated", self._command()["content"])

    def test_order_is_posted_publicly_without_pinging(self):
        self._members(3)
        data = self._command()
        self.assertNotIn("flags", data)          # public, not ephemeral
        self.assertNotIn("components", data)     # nothing to confirm
        self.assertEqual(data["allowed_mentions"], {"parse": []})

    def test_order_is_shuffled(self):
        self._members(4)
        orders = {self._command()["content"] for _ in range(12)}
        self.assertGreater(len(orders), 1)

    def test_group_without_enough_players_is_refused(self):
        self._members(1)
        self.assertIn("enough players", self._command()["content"])

    def _seat_only_members(self, count):
        """Players reachable ONLY through MatchSeat: a series and seats, with the
        group's tournament_players M2M left empty. This is what a real group looks
        like when it was built through the bracket editor -- the site shows these
        players, so the bot must too."""
        series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)
        profiles = []
        for i in range(1, count + 1):
            profile = Profile.objects.create(
                discord=f"seatonly{i}", discord_id=f"66{i}",
                display_name=f"Seat Only {i}")
            tp = TournamentPlayer.objects.create(
                tournament=self.tournament, profile=profile)
            participant = StageParticipant.objects.create(
                stage=self.stage, tournament_player=tp)
            MatchSeat.objects.create(
                series=series, stage_participant=participant, seat_number=i)
            profiles.append(profile)
        return profiles

    def test_seats_a_group_populated_only_by_seats(self):
        """Regression: /seating read tournament_players directly, so a group with
        3 seated players on the site was told it had none."""
        self._seat_only_members(3)
        self.assertEqual(self.group.tournament_players.count(), 0)
        content = self._command()["content"]
        self.assertIn("**Seating**", content)
        for i in range(1, 4):
            self.assertIn(f"Seat Only {i}", content)

    def test_group_members_still_win_over_seats(self):
        """The M2M stays authoritative where it IS populated."""
        self._seat_only_members(3)
        self._members(2)
        content = self._command()["content"]
        self.assertIn("Group Player 1", content)
        self.assertNotIn("Seat Only", content)

    def test_an_lfg_thread_takes_precedence(self):
        """A channel is realistically one or the other, but if both resolve the
        LFG thread wins — its seating is the one that gets recorded."""
        self._members(4)
        lfg = LFGThread.objects.create(thread_id=self.THREAD_ID)
        lfg.players.set([
            Profile.objects.create(discord=f"lfgp{i}", discord_id=f"65{i}")
            for i in range(3)
        ])
        data = self._command()
        self.assertIn("components", data)        # the confirm prompt, not a post
        self.assertEqual(data["flags"], di.EPHEMERAL)

    def test_unrelated_channel_explains_both_options(self):
        data = self._command("777777777777777777")
        self.assertIn("game thread", data["content"])
        self.assertIn("player group", data["content"])

    # ── title fallback + auto-linking ────────────────────────────────────────
    # /schedule could always find a group by thread title; /seating could not,
    # so a thread /schedule scheduled would answer "use this in a game thread".

    def _unlink(self):
        self.group.discord_thread = ""
        self.group.save(update_fields=["discord_thread"])

    def test_seats_an_unlinked_group_matched_by_title(self):
        self._members(3)
        self._unlink()
        data = self._command_in_unlinked_thread("Group A")
        self.assertIn("**Seating**", data["content"])

    def test_title_match_ignores_emoji_and_whitespace_decoration(self):
        self._members(3)
        self._unlink()
        data = self._command_in_unlinked_thread("🏆  group   a  ")
        self.assertIn("**Seating**", data["content"])

    def test_title_match_links_the_thread_to_the_group(self):
        """The point of linking: every later lookup resolves by id instead of
        re-running the title guess."""
        self._members(3)
        self._unlink()
        self._command_in_unlinked_thread("Group A")
        self.group.refresh_from_db()
        self.assertEqual(
            self.group.discord_thread,
            f"https://discord.com/channels/{self.GUILD_ID}/{self.THREAD_ID}")
        # Resolvable by id alone now — no title needed.
        self.assertEqual(player_group_for_channel(self.THREAD_ID), self.group)

    def test_linked_url_matches_the_shape_the_site_parses(self):
        """the_warroom.views._DISCORD_THREAD_URL_RE parses this field back out to
        decide where to announce a recorded game, so the value we write must
        satisfy it: no trailing slash, no message-id suffix."""
        from the_warroom.views import _DISCORD_THREAD_URL_RE
        self._members(3)
        self._unlink()
        self._command_in_unlinked_thread("Group A")
        self.group.refresh_from_db()
        found = _DISCORD_THREAD_URL_RE.match(self.group.discord_thread)
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), self.GUILD_ID)
        self.assertEqual(found.group(2), self.THREAD_ID)

    def test_ambiguous_title_seats_nobody_and_links_nothing(self):
        """Group names are unique per ROUND, not per tournament, so a title can
        legitimately match several groups. Never guess -- and never link."""
        self._members(3)
        self._unlink()
        other_round = Round.objects.create(stage=self.stage, round_number=2)
        twin = PlayerGroup.objects.create(
            round=other_round, group_number=1, name="Group A")
        data = self._command_in_unlinked_thread("Group A")
        self.assertIn("game thread", data["content"])
        self.group.refresh_from_db()
        twin.refresh_from_db()
        self.assertEqual(self.group.discord_thread, "")
        self.assertEqual(twin.discord_thread, "")

    def test_a_linked_group_is_never_hijacked_by_a_same_named_thread(self):
        """self.group keeps its own thread URL, so a DIFFERENT thread with the
        same title must not resolve to it."""
        self._members(3)
        data = self._command_in_unlinked_thread(
            "Group A", channel_id="888777666555444333")
        self.assertIn("game thread", data["content"])

    def test_title_match_is_scoped_to_the_guild(self):
        self._members(3)
        self._unlink()
        other = DiscordGuild.objects.create(guild_id="999000111", name="Other")
        data = self._command_in_unlinked_thread("Group A", guild=other.guild_id)
        self.assertIn("game thread", data["content"])
        self.group.refresh_from_db()
        self.assertEqual(self.group.discord_thread, "")

    def test_without_a_title_the_lookup_stays_id_only(self):
        """Button payloads carry no channel name; they must behave as before."""
        self._members(3)
        self._unlink()
        self.assertIsNone(player_group_for_channel(self.THREAD_ID))

    def test_linking_does_not_clobber_a_concurrent_link(self):
        """The write is a compare-and-swap on discord_thread=""."""
        from the_gatehouse.services.lfg_game import link_group_thread
        self._unlink()
        existing = "https://discord.com/channels/1/2"
        PlayerGroup.objects.filter(pk=self.group.pk).update(
            discord_thread=existing)
        self.assertFalse(
            link_group_thread(self.group, self.GUILD_ID, self.THREAD_ID))
        self.group.refresh_from_db()
        self.assertEqual(self.group.discord_thread, existing)

    def test_a_series_linked_thread_still_seats_the_group(self):
        """A group thread gets its own LFGThread once it captures a roll, but it
        has no `players` -- without the series_id guard the LFG branch would
        swallow this and answer "not enough players"."""
        self._members(4)
        series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)
        LFGThread.objects.create(thread_id=self.THREAD_ID, series=series)
        data = self._command()
        self.assertNotIn("components", data)     # posted, not a confirm prompt
        for i in range(1, 5):
            self.assertIn(f"Group Player {i}", data["content"])


class MatchThreadCaptureTests(TestCase):
    """A tournament group thread captures rolls into an LFGThread of its own,
    created on first use, so match recording can narrow the same way LFG does."""

    GUILD_ID = "1093259831470735512"
    THREAD_ID = "1303834523347456040"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        self.guild = DiscordGuild.objects.create(
            guild_id=self.GUILD_ID, name="Capture Guild")
        designer = Profile.objects.create(discord="capdes", discord_id="660")
        self.tournament = Tournament.objects.create(
            name="Capture Tournament", guild=self.guild, designer=designer)
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name="Capture Group",
            discord_thread=(
                f"https://discord.com/channels/{self.GUILD_ID}/{self.THREAD_ID}"),
        )
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)
        self.match = Match.objects.create(round=self.round, series=self.series)
        self.map = Map.objects.create(
            title="Capture Lake", designer=designer, status=StatusChoices.STABLE,
            official=True)

    def _capture(self, channel_id=None):
        record_lfg_components_task(
            channel_id or self.THREAD_ID,
            [{"kind": "Map", "slug": self.map.slug, "title": self.map.title}],
            source="random")

    def test_first_capture_creates_a_thread_linked_to_the_series(self):
        self._capture()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertEqual(thread.series, self.series)
        self.assertEqual(thread.map, self.map)
        self.assertEqual(thread.roll_log.count(), 1)
        # MatchSeat is the roster for a match; players stays empty on purpose.
        self.assertEqual(thread.players.count(), 0)

    def test_second_capture_adds_a_roll_not_a_thread(self):
        self._capture()
        self._capture()
        self.assertEqual(LFGThread.objects.filter(thread_id=self.THREAD_ID).count(), 1)
        self.assertEqual(
            LFGThread.objects.get(thread_id=self.THREAD_ID).roll_log.count(), 2)

    def test_a_group_with_no_series_captures_nothing_and_does_not_raise(self):
        """MatchSeries.player_group is a OneToOne, so `group.series` RAISES rather
        than returning None when unset. This task has no autoretry, so a raise
        would silently lose the capture."""
        group = PlayerGroup.objects.create(
            round=self.round, group_number=2, name="No Series Group",
            discord_thread=f"https://discord.com/channels/{self.GUILD_ID}/555000111222",
        )
        self.assertFalse(MatchSeries.objects.filter(player_group=group).exists())
        self._capture("555000111222")            # must not raise
        self.assertFalse(LFGThread.objects.filter(thread_id="555000111222").exists())

    def test_an_unrelated_channel_is_still_a_no_op(self):
        self._capture("999999999999999999")
        self.assertFalse(
            LFGThread.objects.filter(thread_id="999999999999999999").exists())

    def test_record_stays_in_match_mode_for_a_series_thread(self):
        """The whole point of LFGThread.series: /record checks the LFG thread
        first, so without the guard a captured match thread would hand back
        ?lfg= and silently drop match mode."""
        self._capture()
        data = json.loads(di._handle_record_command({
            "_guild_id": self.GUILD_ID, "_channel_id": self.THREAD_ID,
            "_channel_name": None,
        }).content)["data"]
        self.assertIn(f"?match={self.match.id}", data["content"])
        self.assertNotIn("?lfg=", data["content"])

    def test_record_still_uses_lfg_mode_for_an_unlinked_thread(self):
        thread = LFGThread.objects.create(thread_id="880000111222333444")
        data = json.loads(di._handle_record_command({
            "_guild_id": self.GUILD_ID, "_channel_id": "880000111222333444",
            "_channel_name": None,
        }).content)["data"]
        self.assertIn(f"?lfg={thread.id}", data["content"])


class LFGCaptureTests(TestCase):
    """record_lfg_components_task and the readers over it. This path had no
    coverage while it stored JSON, so these are written against the relational
    tables directly."""

    THREAD_ID = "thread-cap-1"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        self.designer = Profile.objects.create(discord="capdesigner", discord_id="700")
        self.factions = [
            Faction.objects.create(
                title=f"Cap Faction {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(3)
        ]
        self.map = Map.objects.create(
            title="Cap Map", designer=self.designer,
            status=StatusChoices.STABLE, official=True)
        self.deck = Deck.objects.create(
            title="Cap Deck", designer=self.designer, card_total=54,
            status=StatusChoices.STABLE, official=True)
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)

    def _item(self, kind, post):
        return {"kind": kind, "slug": post.slug, "title": post.title}

    # ── capture ─────────────────────────────────────────────────────────────
    def test_capture_writes_rows_and_resolves_posts(self):
        record_lfg_components_task(
            self.THREAD_ID,
            [self._item("Faction", self.factions[0]), self._item("Map", self.map)],
            source="random")
        rolls = list(self.thread.roll_log.all())
        self.assertEqual([r.kind for r in rolls], ["Faction", "Map"])
        self.assertEqual([r.post_id for r in rolls],
                         [self.factions[0].pk, self.map.pk])
        self.assertTrue(all(r.source == "random" for r in rolls))

    def test_capture_updates_map_and_deck_fks(self):
        record_lfg_components_task(
            self.THREAD_ID,
            [self._item("Map", self.map), self._item("Deck", self.deck)])
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.map_id, self.map.pk)
        self.assertEqual(self.thread.deck_id, self.deck.pk)

    def test_unresolvable_slug_keeps_the_row_with_a_null_post(self):
        record_lfg_components_task(
            self.THREAD_ID, [{"kind": "Faction", "slug": "no-such-slug"}])
        roll = self.thread.roll_log.get()
        self.assertIsNone(roll.post_id)
        self.assertEqual(roll.slug, "no-such-slug")

    def test_capture_is_a_no_op_outside_an_lfg_thread(self):
        record_lfg_components_task("not-a-thread", [self._item("Map", self.map)])
        self.assertEqual(LFGRoll.objects.count(), 0)

    def test_capture_appends_rather_than_replacing(self):
        record_lfg_components_task(self.THREAD_ID, [self._item("Map", self.map)])
        record_lfg_components_task(self.THREAD_ID, [self._item("Deck", self.deck)])
        self.assertEqual(self.thread.roll_log.count(), 2)

    # ── draft ───────────────────────────────────────────────────────────────
    def _draft_payload(self, factions, **kw):
        payload = {"players": 2, "platform": "Tabletop Simulator",
                   "drafted_by": "700",
                   "picks": [{"faction": f.slug, "vagabond": None, "captains": [],
                              "order": i}
                             for i, f in enumerate(factions, 1)]}
        payload.update(kw)
        return payload

    def test_draft_is_recorded_with_its_picks(self):
        record_lfg_components_task(
            self.THREAD_ID, [], source="draft",
            draft=self._draft_payload(self.factions[:2]))
        draft = self.thread.draft
        self.assertEqual(draft.players, 2)
        self.assertEqual(draft.drafted_by, self.designer)
        self.assertEqual([p.faction_id for p in draft.picks.all()],
                         [self.factions[0].pk, self.factions[1].pk])

    def test_redrafting_replaces_rather_than_accumulating(self):
        record_lfg_components_task(
            self.THREAD_ID, [], source="draft",
            draft=self._draft_payload(self.factions[:2]))
        record_lfg_components_task(
            self.THREAD_ID, [], source="draft",
            draft=self._draft_payload(self.factions[2:]))

        self.assertEqual(LFGDraft.objects.filter(thread=self.thread).count(), 1)
        self.assertEqual([p.faction_id for p in self.thread.draft.picks.all()],
                         [self.factions[2].pk])

    def test_unknown_faction_slug_is_skipped_not_fatal(self):
        payload = self._draft_payload(self.factions[:1])
        payload["picks"].append(
            {"faction": "ghost-faction", "vagabond": None, "captains": [], "order": 2})
        record_lfg_components_task(self.THREAD_ID, [], source="draft", draft=payload)
        self.assertEqual(self.thread.draft.picks.count(), 1)

    # ── readers ─────────────────────────────────────────────────────────────
    def test_rolled_components_dedupes_and_keeps_first_seen_order(self):
        record_lfg_components_task(
            self.THREAD_ID,
            [self._item("Faction", self.factions[0]),
             self._item("Faction", self.factions[1]),
             self._item("Faction", self.factions[0])])
        self.assertEqual(rolled_components(self.thread),
                         {"Faction": [self.factions[0].slug, self.factions[1].slug]})

    def test_rolled_components_prefers_the_live_slug_over_the_snapshot(self):
        """A renamed Post changes its slug; the form filters on live slugs, so a
        stale snapshot would silently drop the component."""
        record_lfg_components_task(self.THREAD_ID, [self._item("Map", self.map)])
        LFGRoll.objects.filter(thread=self.thread).update(slug="stale-snapshot")
        self.assertEqual(rolled_components(self.thread), {"Map": [self.map.slug]})

    def test_seated_profiles_falls_back_to_players_when_unseated(self):
        players = [Profile.objects.create(discord=f"cap{i}", discord_id=f"71{i}")
                   for i in range(2)]
        self.thread.players.set(players)
        seats = seated_profiles(self.thread)
        self.assertEqual([n for n, _p, _f, _v in seats], [1, 2])
        self.assertEqual({p for _n, p, _f, _v in seats}, set(players))

    def test_seated_profiles_returns_a_faction_SLUG_not_an_object(self):
        """views.py filters `.filter(slug=faction_slug)`; a Faction instance there
        would coerce via str() and silently match nothing."""
        p = Profile.objects.create(discord="capseat", discord_id="720")
        LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=1,
                               faction=self.factions[0])
        _seat_no, _profile, faction_slug, _vb = seated_profiles(self.thread)[0]
        self.assertEqual(faction_slug, self.factions[0].slug)

    def _vagabond(self, title):
        """A saved Vagabond. `animal` is required: Vagabond.save() routes through
        animal_default_picture, which lowercases it."""
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)
        return Vagabond.objects.create(
            title=title, animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True)

    def test_seated_profiles_returns_the_vagabond_slug(self):
        """The vagabond rides alongside the faction: all 12 vagabond variants
        share one Faction row, so faction alone can't say which one was taken."""
        p = Profile.objects.create(discord="capvb", discord_id="723")
        vb = self._vagabond("Seat Ranger")
        LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=1,
                               faction=self.factions[0], vagabond=vb)
        _seat_no, _profile, _faction, vagabond_slug = seated_profiles(self.thread)[0]
        self.assertEqual(vagabond_slug, vb.slug)

    def test_seated_profiles_vagabond_is_none_when_unset(self):
        p = Profile.objects.create(discord="capnovb", discord_id="724")
        LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=1,
                               faction=self.factions[0])
        self.assertIsNone(seated_profiles(self.thread)[0][3])

    def test_a_deleted_profile_leaves_a_blank_seat_in_position(self):
        keep = Profile.objects.create(discord="capkeep", discord_id="721")
        drop = Profile.objects.create(discord="capdrop", discord_id="722")
        LFGSeat.objects.create(thread=self.thread, profile=drop, seat_number=1)
        LFGSeat.objects.create(thread=self.thread, profile=keep, seat_number=2)
        drop.delete()

        seats = seated_profiles(self.thread)
        # The row survives with profile=None so the record form keeps its slot.
        self.assertEqual([n for n, _p, _f, _v in seats], [1, 2])
        self.assertIsNone(seats[0][1])
        self.assertEqual(seats[1][1], keep)

    # ── captains_by_seat: a SIBLING of seated_profiles, not a 5th element ──
    def test_captains_by_seat_maps_seat_number_to_slugs(self):
        p = Profile.objects.create(discord="capm", discord_id="725")
        seat = LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=1, faction=self.factions[0])
        caps = [self._vagabond(f"Map Captain {i}") for i in range(3)]
        seat.captains.set(caps)

        # Order is not guaranteed on an unordered M2M, and callers filter with
        # slug__in, so compare as a set.
        mapping = captains_by_seat(self.thread)
        self.assertEqual(list(mapping), [1])
        self.assertEqual(set(mapping[1]['captains']), {c.slug for c in caps})

    def test_captains_by_seat_carries_the_discarded_captain(self):
        p = Profile.objects.create(discord="capdisc", discord_id="728")
        seat = LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=1, faction=self.factions[0])
        taken = [self._vagabond(f"Disc Captain {i}") for i in range(3)]
        dropped = self._vagabond("Disc Dropped")
        seat.captains.set(taken)
        seat.discarded_captain = dropped
        seat.save(update_fields=["discarded_captain"])

        self.assertEqual(captains_by_seat(self.thread)[1]['discarded'],
                         dropped.slug)

    def test_captains_by_seat_discarded_is_none_on_a_short_roll(self):
        """Fewer than 4 qualified means nothing was discarded -- not an error."""
        p = Profile.objects.create(discord="capshort", discord_id="729")
        seat = LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=1, faction=self.factions[0])
        seat.captains.set([self._vagabond("Short Captain")])
        self.assertIsNone(captains_by_seat(self.thread)[1]['discarded'])

    def test_captains_by_seat_omits_seats_with_none(self):
        p = Profile.objects.create(discord="capnone", discord_id="726")
        LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=1,
                               faction=self.factions[0])
        self.assertEqual(captains_by_seat(self.thread), {})

    # ── undrafted_pick: the one drafted faction nobody took ──
    def _draft_with(self, *factions):
        draft = LFGDraft.objects.create(thread=self.thread)
        for i, f in enumerate(factions, 1):
            LFGDraftPick.objects.create(draft=draft, faction=f, order=i)
        return draft

    def _seat(self, n, faction=None, discord_id=None):
        p = Profile.objects.create(discord=f"und{n}{discord_id or ''}",
                                   discord_id=f"73{n}{discord_id or ''}")
        return LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=n, faction=faction)

    def test_undrafted_pick_returns_the_single_unseated_faction(self):
        self._draft_with(self.factions[0], self.factions[1], self.factions[2])
        self._seat(1, self.factions[0])
        self._seat(2, self.factions[1])

        pick = undrafted_pick(self.thread)
        self.assertIsNotNone(pick)
        self.assertEqual(pick.faction_id, self.factions[2].pk)

    def test_undrafted_pick_is_none_mid_pick(self):
        """Two still unseated: someone is about to take one, so prefilling it
        would claim a faction that is still in play."""
        self._draft_with(self.factions[0], self.factions[1], self.factions[2])
        self._seat(1, self.factions[0])
        self.assertIsNone(undrafted_pick(self.thread))

    def test_undrafted_pick_is_none_when_all_are_seated(self):
        self._draft_with(self.factions[0], self.factions[1])
        self._seat(1, self.factions[0])
        self._seat(2, self.factions[1])
        self.assertIsNone(undrafted_pick(self.thread))

    def test_undrafted_pick_is_none_without_a_draft(self):
        """No draft means no 'undrafted' faction -- everything unpicked is just
        unpicked. LFGDraft.thread is a OneToOne, so this must not raise."""
        self._seat(1, self.factions[0])
        self.assertIsNone(undrafted_pick(self.thread))

    def test_undrafted_pick_carries_its_vagabond_and_captains(self):
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        vb = self._vagabond("Undrafted Ranger")
        caps = [self._vagabond(f"Undrafted Captain {i}") for i in range(4)]
        leftover = LFGDraftPick.objects.create(
            draft=draft, faction=self.factions[1], vagabond=vb, order=2)
        leftover.captains.set(caps)
        self._seat(1, self.factions[0])

        pick = undrafted_pick(self.thread)
        self.assertEqual(pick.vagabond_id, vb.pk)
        self.assertEqual({c.slug for c in pick.captains.all()},
                         {c.slug for c in caps})

    def test_full_captain_complement_matches_the_roller(self):
        """FULL_CAPTAIN_COMPLEMENT is duplicated from DRAFT_CAPTAIN_COUNT to keep
        the record view off the Discord stack; they must not drift."""
        self.assertEqual(FULL_CAPTAIN_COMPLEMENT, di.DRAFT_CAPTAIN_COUNT)

    def test_seated_profiles_stays_a_4_tuple_with_captains_set(self):
        """The backward-compatibility check: adding captains must not widen the
        tuple, which callers unpack at a fixed width."""
        p = Profile.objects.create(discord="capwidth", discord_id="727")
        seat = LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=1, faction=self.factions[0])
        seat.captains.set([self._vagabond("Width Captain")])
        for row in seated_profiles(self.thread):
            self.assertEqual(len(row), 4)


class PickCommandTests(TestCase):
    """/pick: choosing factions seat by seat, last seat first."""

    THREAD_ID = "1303834523347456099"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        self.designer = Profile.objects.create(discord="pickcmd", discord_id="750")
        self.factions = [
            Faction.objects.create(
                title=f"Pick Cmd Faction {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(6)
        ]
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)

    def _roster(self, count, seated=True):
        """Players with discord_id '76<i>'. `seated` writes a real seating (the
        default); seated=False leaves the thread unseated, as /pick finds it
        before anyone runs /seating."""
        profiles = [
            Profile.objects.create(discord=f"pkp{i}", discord_id=f"76{i}",
                                   display_name=f"Pick Player {i}")
            for i in range(1, count + 1)
        ]
        self.thread.players.set(profiles)
        if seated:
            for i, p in enumerate(profiles, 1):
                LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=i)
            self.thread.seating_set = True
            self.thread.save(update_fields=["seating_set"])
        return profiles

    def _command(self, channel_id=None, guild_id=None):
        response = di._handle_pick_command({
            "_channel_id": channel_id or self.THREAD_ID,
            "_author_id": self.OWNER,
            "_guild_id": guild_id,
        })
        return json.loads(response.content)["data"]

    def _select(self, slug, clicker, mode=di.PICK_MODE_PLAYERS, channel_id=None):
        payload = {
            "channel_id": channel_id or self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_faction", mode, self.OWNER, di.PICK_OPEN),
                "values": [slug],
            },
            "message": {"id": "msg", "components": []},
        }
        response = di._handle_pick_faction(payload)
        return json.loads(response.content)["data"]

    # ── the map/deck setup reminder ──
    def _map_and_deck(self):
        return (Map.objects.create(title="Pick Map", designer=self.designer,
                                   status=StatusChoices.STABLE, official=True),
                Deck.objects.create(title="Pick Deck", designer=self.designer,
                                    card_total=54,
                                    status=StatusChoices.STABLE, official=True))

    def test_reminder_names_both_when_neither_is_set(self):
        self._roster(3)
        content = self._command()["content"]
        self.assertIn("-# Remember to choose a map and deck", content)

    def test_reminder_names_only_what_is_missing(self):
        """A table that already chose a map shouldn't be told to choose one."""
        self._roster(3)
        game_map, _deck = self._map_and_deck()
        self.thread.map = game_map
        self.thread.save(update_fields=["map"])
        content = self._command()["content"]
        self.assertIn("-# Remember to choose a deck for this game.", content)
        self.assertNotIn("a map", content)

    def test_reminder_names_no_command(self):
        """/map and /deck are per-guild (enabled_commands), so naming them can
        point at commands this guild doesn't have -- and choosing a map isn't
        required to pick factions anyway."""
        self._roster(3)
        content = self._command()["content"]
        self.assertNotIn("/map", content)
        self.assertNotIn("/deck", content)

    def test_no_reminder_once_both_are_set(self):
        self._roster(3)
        game_map, deck = self._map_and_deck()
        self.thread.map, self.thread.deck = game_map, deck
        self.thread.save(update_fields=["map", "deck"])
        content = self._command()["content"]
        self.assertNotIn("-#", content)
        # The question itself is untouched by the reminder either way.
        self.assertIn("assign every faction yourself", content)

    # ── guards ──
    def test_a_one_player_thread_is_refused(self):
        self.thread.players.set([
            Profile.objects.create(discord="pku", discord_id="769")])
        content = self._command()["content"]
        self.assertIn("enough players", content)
        # Must not send the user to a command their guild may not even have.
        self.assertNotIn("/seating", content)

    # ── unseated: the guild HAS /seating ──
    def _guild(self, commands):
        guild = DiscordGuild.objects.create(guild_id="9300000000000001",
                                            name="Pick Guild",
                                            enabled_commands=commands)
        return guild.guild_id

    def test_unseated_offers_the_seating_choice_first(self):
        """With no seating, /pick asks whether to assign a random order before
        asking who chooses the factions."""
        self._roster(3, seated=False)
        data = self._command(guild_id=self._guild(["pick", "seating"]))
        labels = [c["label"] for c in data["components"][0]["components"]]
        self.assertEqual(labels, ["Seat Players", "Skip Seating", "Cancel"])
        self.assertIn("haven't been seated", data["content"])

    def test_the_seating_choice_writes_nothing(self):
        """Both buttons create the seats, so an abandoned prompt must leave none
        behind."""
        self._roster(3, seated=False)
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            self._command(guild_id=self._guild(["pick"]))
        post.assert_not_called()
        thread = LFGThread.objects.get(pk=self.thread.pk)
        self.assertEqual(thread.seats.count(), 0)
        self.assertFalse(thread.seating_set)

    def test_the_seating_choice_buttons_are_owner_locked(self):
        self._roster(3, seated=False)
        data = self._command(guild_id=self._guild(["pick", "seating"]))
        for comp in data["components"][0]["components"]:
            action, args = di.decode_custom_id(comp["custom_id"])
            if action in ("pick_seat", "pick_noseat"):
                self.assertEqual(args[-1], self.OWNER)

    def test_a_seated_thread_skips_the_seating_choice(self):
        """It would offer to overwrite an order players were just shown."""
        self._roster(3)
        data = self._command(guild_id=self._guild(["pick", "seating"]))
        labels = [c["label"] for c in data["components"][0]["components"]]
        self.assertEqual(labels, ["Players pick", "Assign all", "Cancel"])

    def test_the_guild_seating_setting_does_not_change_the_prompt(self):
        """The prompt used to branch on whether the guild had /seating enabled.
        /pick does the seating itself now, so that setting is irrelevant here."""
        self._roster(3, seated=False)
        guild_id = self._guild(["pick", "seating"])
        with_seating = self._command(guild_id=guild_id)

        DiscordGuild.objects.filter(guild_id=guild_id).update(
            enabled_commands=["pick"])
        without = self._command(guild_id=guild_id)
        self.assertEqual(with_seating["content"], without["content"])

    def _skip_seating(self):
        """Click Skip Seating, returning the mode prompt that follows."""
        return json.loads(di._handle_pick_noseat({
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": self.OWNER}},
            "data": {"custom_id": di.encode_custom_id("pick_noseat", self.OWNER)},
            "message": {"id": "msg", "components": []},
        }).content)["data"]

    def test_the_unseated_mode_prompt_does_not_claim_a_seat_order(self):
        self._roster(3, seated=False)
        self._command(guild_id=self._guild(["pick"]))
        data = self._skip_seating()
        self.assertIn("each player pick in turn?", data["content"])
        self.assertNotIn("seat order", data["content"])

    def test_the_unordered_panel_shows_no_seat_numbers(self):
        self._roster(3, seated=False)
        self._command(guild_id=self._guild(["pick"]))
        data = self._skip_seating()
        self.assertNotIn("1. ", data["content"])
        self.assertNotIn("seat 3", data["content"])

    def test_the_unordered_roster_is_not_shuffled(self):
        """Filler seat numbers reach Effort.seat when the recorder doesn't drag
        the rows, so inventing a random order would be worse than none."""
        players = self._roster(4, seated=False)
        self._command(guild_id=self._guild(["pick"]))
        self._skip_seating()
        seats = self.thread.seats.order_by("seat_number")
        self.assertEqual([s.profile_id for s in seats],
                         [p.pk for p in self.thread.players.all()])
        self.assertEqual(len(players), 4)

    def test_seat_players_numbers_the_panel_without_posting(self):
        """The whole point of the new flow: the order is visible in the panel, so
        no separate seating message is announced."""
        self._roster(3, seated=False)
        self._command(guild_id=self._guild(["pick"]))
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            data = json.loads(di._handle_pick_seat({
                "channel_id": self.THREAD_ID,
                "member": {"user": {"id": self.OWNER}},
                "data": {"custom_id": di.encode_custom_id("pick_seat", self.OWNER)},
                "message": {"id": "msg", "components": []},
            }).content)["data"]
        post.assert_not_called()
        self.assertTrue(LFGThread.objects.get(pk=self.thread.pk).seating_set)
        self.assertIn("in seat order", data["content"])

    def test_unrelated_channel_is_refused(self):
        data = self._command("777777777777777777")
        self.assertIn("game thread", data["content"])

    def test_a_pool_smaller_than_the_table_is_refused(self):
        Faction.objects.all().delete()
        Faction.objects.create(
            title="Lonely", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        self._roster(4)
        self.assertIn("Only 1 factions", self._command()["content"])

    # ── turn order ──
    def test_the_last_seat_picks_first(self):
        players = self._roster(4)
        seats = list(self.thread.seats.all())
        self.assertEqual(di._pick_next_seat(seats).profile, players[-1])

    def test_turn_descends_after_each_pick(self):
        players = self._roster(3)
        self._select(self.factions[0].slug, players[2].discord_id)
        seats = list(self.thread.seats.select_related("profile", "faction"))
        self.assertEqual(di._pick_next_seat(seats).profile, players[1])

    def test_a_seat_with_no_profile_is_skipped(self):
        """No clicker could ever match a removed player, so waiting on that seat
        would stall the table forever."""
        players = self._roster(3)
        players[2].delete()
        seats = list(self.thread.seats.select_related("profile", "faction"))
        self.assertEqual(di._pick_next_seat(seats).profile, players[1])

    # ── authorization ──
    def test_a_player_out_of_turn_is_refused_and_nothing_is_written(self):
        players = self._roster(3)
        data = self._select(self.factions[0].slug, players[0].discord_id)
        # Names whose turn it actually is, so the refused player knows who to wait on.
        self.assertEqual(data["content"],
                         f"It's {players[2].name}'s turn to pick a faction.")
        self.assertFalse(self.thread.seats.exclude(faction=None).exists())

    def test_the_turn_player_persists_their_faction(self):
        players = self._roster(3)
        self._select(self.factions[0].slug, players[2].discord_id)
        seat = self.thread.seats.get(seat_number=3)
        self.assertEqual(seat.faction, self.factions[0])

    def test_assign_mode_lets_the_invoker_pick_for_everyone(self):
        players = self._roster(3)
        self._select(self.factions[0].slug, self.OWNER, mode=di.PICK_MODE_ASSIGN)
        self.assertEqual(self.thread.seats.get(seat_number=3).faction,
                         self.factions[0])

    def test_assign_mode_refuses_everyone_else(self):
        players = self._roster(3)
        data = self._select(self.factions[1].slug, players[2].discord_id,
                            mode=di.PICK_MODE_ASSIGN)
        self.assertIn("can assign", data["content"])
        self.assertFalse(self.thread.seats.exclude(faction=None).exists())

    # ── writes ──
    def test_a_taken_faction_cannot_be_taken_twice(self):
        players = self._roster(3)
        self._select(self.factions[0].slug, players[2].discord_id)
        data = self._select(self.factions[0].slug, players[1].discord_id)
        self.assertIn("already taken", data["content"])
        self.assertIsNone(self.thread.seats.get(seat_number=2).faction)

    def test_a_second_click_does_not_consume_another_seat(self):
        """The target seat is derived from the DB, not the custom_id, so a
        double-click lands on the same seat and is rejected."""
        players = self._roster(3)
        self._select(self.factions[0].slug, players[2].discord_id)
        self._select(self.factions[0].slug, players[2].discord_id)
        self.assertEqual(self.thread.seats.exclude(faction=None).count(), 1)

    def test_a_seat_filled_between_check_and_write_is_rejected(self):
        """The seat is re-resolved under select_for_update with
        faction__isnull=True, so the loser of a race is rejected rather than
        overwriting the winner."""
        players = self._roster(3)
        seat3 = self.thread.seats.get(seat_number=3)

        real = di.LFGSeat.objects.select_for_update

        def fill_then_lock(*a, **kw):
            # Simulate the winning click landing after this one authorized.
            LFGSeat.objects.filter(pk=seat3.pk).update(faction=self.factions[1])
            di.LFGSeat.objects.select_for_update = real
            return real(*a, **kw)

        with mock.patch.object(di.LFGSeat.objects, "select_for_update",
                               side_effect=fill_then_lock):
            data = self._select(self.factions[0].slug, players[2].discord_id)

        self.assertIn("just picked", data["content"])
        self.assertEqual(self.thread.seats.get(pk=seat3.pk).faction,
                         self.factions[1])

    def test_picking_the_vagabond_faction_attaches_its_drafted_vagabond(self):
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)
        players = self._roster(2)
        vb = Vagabond.objects.create(
            title="Pick Ranger", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True)
        draft = LFGDraft.objects.create(thread=self.thread, players=2)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0],
                                    vagabond=vb, order=1)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[1], order=2)

        self._select(self.factions[0].slug, players[1].discord_id)
        seat = self.thread.seats.get(seat_number=2)
        self.assertEqual(seat.faction, self.factions[0])
        self.assertEqual(seat.vagabond, vb)

    # ── pool ──
    def test_the_draft_is_the_pool_when_there_is_one(self):
        self._roster(2)
        draft = LFGDraft.objects.create(thread=self.thread, players=2)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[1], order=2)
        self.assertEqual([slug for slug, _t, _v in di._pick_pool(self.thread)],
                         [self.factions[0].slug, self.factions[1].slug])

    def test_without_a_draft_the_pool_is_every_official_stable_faction(self):
        self._roster(2)
        self.assertEqual(len(di._pick_pool(self.thread)), len(self.factions))

    def test_a_faction_outside_the_pool_is_refused(self):
        players = self._roster(2)
        draft = LFGDraft.objects.create(thread=self.thread, players=2)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[1], order=2)
        data = self._select(self.factions[5].slug, players[1].discord_id)
        self.assertIn("isn't in this game's pool", data["content"])

    # ── the owner-lock escape hatch ──
    def test_pick_custom_ids_end_in_a_non_snowflake(self):
        """The dispatcher owner-locks any custom_id whose LAST arg looks like a
        snowflake. If these regress, every player except the invoker is silently
        blocked and the command looks broken for the whole table."""
        self._roster(3)
        seats = list(self.thread.seats.select_related("profile", "faction"))
        data = di._pick_panel_data(self.thread, seats, di.PICK_MODE_PLAYERS, self.OWNER)
        ids = [c["custom_id"] for row in data["components"] for c in row["components"]]
        self.assertTrue(ids)
        for custom_id in ids:
            last = di.decode_custom_id(custom_id)[1][-1]
            self.assertEqual(last, di.PICK_OPEN)
            self.assertFalse(last.isdigit())

    def test_the_mode_prompt_stays_owner_locked(self):
        """The opposite case: mode buttons SHOULD end in the invoker's snowflake
        so only they choose how the table picks."""
        self._roster(3)
        data = self._command()
        modes = [c["custom_id"] for row in data["components"]
                 for c in row["components"] if c["custom_id"].startswith("pick_mode")]
        self.assertTrue(modes)
        for custom_id in modes:
            self.assertEqual(di.decode_custom_id(custom_id)[1][-1], self.OWNER)

    # ── the panel ──
    def test_the_panel_drops_factions_already_taken(self):
        players = self._roster(3)
        self._select(self.factions[0].slug, players[2].discord_id)
        seats = list(self.thread.seats.select_related("profile", "faction"))
        data = di._pick_panel_data(self.thread, seats, di.PICK_MODE_PLAYERS, self.OWNER)
        offered = [o["value"] for o in data["components"][0]["components"][0]["options"]]
        self.assertNotIn(self.factions[0].slug, offered)

    def test_the_panel_never_pings(self):
        self._roster(3)
        seats = list(self.thread.seats.select_related("profile", "faction"))
        data = di._pick_panel_data(self.thread, seats, di.PICK_MODE_PLAYERS, self.OWNER)
        self.assertEqual(data["allowed_mentions"], {"parse": []})

    def test_the_final_panel_clears_its_components(self):
        players = self._roster(2)
        self._select(self.factions[0].slug, players[1].discord_id)
        data = self._select(self.factions[1].slug, players[0].discord_id)
        self.assertEqual(data["components"], [])
        self.assertIn("Draft Complete", data["content"])

    # ── the roll capture, which must branch on thread type ──
    def test_an_lfg_thread_records_the_pick_in_the_roll_log(self):
        """lfg_option_querysets narrows factions to the roll log, so a pick that
        isn't logged would be silently dropped at prefill."""
        players = self._roster(2)
        with mock.patch.object(di.record_lfg_components_task, "delay") as capture:
            self._select(self.factions[0].slug, players[1].discord_id)
        items = capture.call_args.args[1]
        self.assertEqual([i["slug"] for i in items], [self.factions[0].slug])
        self.assertEqual(capture.call_args.kwargs["source"], "pick")


class PickVagabondFollowUpTests(TestCase):
    """Picking the Vagabond faction with no draft must ask WHICH vagabond.

    All 12 vagabond variants share one Faction row, so a seat recorded as
    Vagabond with no vagabond collapses Ranger and Thief into one record.
    """

    THREAD_ID = "1303834523347456077"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)

        self.designer = Profile.objects.create(discord="pkvb", discord_id="790")
        # The real slug is load-bearing: the follow-up is keyed off it.
        self.vagabond_faction = Faction.objects.create(
            title="Vagabond", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        self.other = Faction.objects.create(
            title="Vb Other Faction", animal="Mouse", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        self.ranger = Vagabond.objects.create(
            title="Vb Ranger", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True)
        self.thief = Vagabond.objects.create(
            title="Vb Thief", animal="Mouse", designer=self.designer,
            status=StatusChoices.STABLE, official=True)

        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.players = [
            Profile.objects.create(discord=f"pkv{i}", discord_id=f"79{i}",
                                   display_name=f"Vb Player {i}")
            for i in range(1, 3)
        ]
        self.thread.players.set(self.players)
        for i, p in enumerate(self.players, 1):
            LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=i)
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])

    def _select(self, slug, clicker):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_faction", di.PICK_MODE_PLAYERS, self.OWNER, di.PICK_OPEN),
                "values": [slug],
            },
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_faction(payload).content)["data"]

    def test_committing_a_faction_bumps_the_threads_last_activity(self):
        """The commit saves the SEAT, so the parent needs its own bump -- without
        it, a thread being actively picked in ages toward the cleanup task."""
        stale = timezone.now() - timedelta(days=200)
        LFGThread.objects.filter(pk=self.thread.pk).update(last_activity=stale)

        # The LAST seat picks first, so players[1] is the valid clicker.
        self._select(self.other.slug, self.players[1].discord_id)

        self.thread.refresh_from_db()
        self.assertEqual(
            self.thread.seats.get(profile=self.players[1]).faction_id,
            self.other.pk)
        self.assertGreater(self.thread.last_activity, stale)

    def _choose_vagabond(self, slug, clicker, faction_slug=None):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_vagabond", di.PICK_MODE_PLAYERS, self.OWNER,
                    faction_slug or self.vagabond_faction.slug, di.PICK_OPEN),
                "values": [slug],
            },
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_vagabond(payload).content)["data"]

    def _last_seat(self):
        # The LAST seat picks first.
        return self.thread.seats.get(seat_number=2)

    def test_picking_vagabond_prompts_instead_of_writing_the_seat(self):
        """The deferred write is the whole fix: an abandoned prompt must leave
        the seat untouched rather than stranded with a faction and no vagabond."""
        data = self._select(self.vagabond_faction.slug,
                            self.players[1].discord_id)
        values = [o["value"]
                  for o in data["components"][0]["components"][0]["options"]]
        self.assertEqual(sorted(values), sorted([self.ranger.slug, self.thief.slug]))

        seat = self._last_seat()
        self.assertIsNone(seat.faction_id)
        self.assertIsNone(seat.vagabond_id)

    def test_choosing_the_vagabond_writes_both_and_advances(self):
        self._select(self.vagabond_faction.slug, self.players[1].discord_id)
        data = self._choose_vagabond(self.ranger.slug, self.players[1].discord_id)

        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.vagabond_faction.pk)
        self.assertEqual(seat.vagabond_id, self.ranger.pk)
        # Turn advanced to seat 1.
        self.assertIn(self.players[0].name, data["content"])

    def test_two_seats_take_different_vagabonds(self):
        """The point of the fix: Ranger and Thief must not collapse."""
        self._select(self.vagabond_faction.slug, self.players[1].discord_id)
        self._choose_vagabond(self.ranger.slug, self.players[1].discord_id)
        # Seat 1 can't take Vagabond again (faction taken), so just check seat 2
        # kept its identity.
        self.assertEqual(self._last_seat().vagabond_id, self.ranger.pk)

    def test_a_non_vagabond_faction_still_writes_immediately(self):
        self._select(self.other.slug, self.players[1].discord_id)
        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.other.pk)
        self.assertIsNone(seat.vagabond_id)

    def test_the_follow_up_records_a_vagabond_roll(self):
        """lfg_option_querysets narrows to the roll log, so a vagabond missing
        from it would be dropped at prefill."""
        self._select(self.vagabond_faction.slug, self.players[1].discord_id)
        with mock.patch.object(di.record_lfg_components_task, "delay") as capture:
            self._choose_vagabond(self.ranger.slug, self.players[1].discord_id)
        items = capture.call_args.args[1]
        self.assertEqual(
            [(i["kind"], i["slug"]) for i in items],
            [("Faction", self.vagabond_faction.slug),
             ("Vagabond", self.ranger.slug)])
        self.assertEqual(capture.call_args.kwargs["source"], "pick")

    def test_the_chosen_vagabond_shows_on_the_board(self):
        """All 12 vagabond variants share one Faction row, so a seat reading only
        "Vagabond" can't be told apart from another one."""
        self._select(self.vagabond_faction.slug, self.players[1].discord_id)
        with mock.patch.object(di, "vagabond_emoji_for",
                               lambda v: f"<:M{v.title.replace(' ', '')}:9>"):
            data = self._choose_vagabond(self.ranger.slug,
                                         self.players[1].discord_id)
        self.assertIn(f"<:M{self.ranger.title.replace(' ', '')}:9> "
                      f"{self.ranger.title}", data["content"])

    def test_a_drafted_vagabond_skips_the_prompt(self):
        """With a draft the vagabond is already decided, so the seat is written
        in one click -- the prompt exists only for the no-draft path."""
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(
            draft=draft, faction=self.vagabond_faction, vagabond=self.thief,
            order=1)
        LFGDraftPick.objects.create(draft=draft, faction=self.other, order=2)

        data = self._select(self.vagabond_faction.slug,
                            self.players[1].discord_id)
        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.vagabond_faction.pk)
        self.assertEqual(seat.vagabond_id, self.thief.pk)
        self.assertIn(self.players[0].name, data["content"])

    def test_the_follow_up_authorizes_the_turn(self):
        """PICK_OPEN keeps the dispatcher lock off, so the handler must reject a
        player clicking out of turn itself."""
        self._select(self.vagabond_faction.slug, self.players[1].discord_id)
        data = self._choose_vagabond(self.ranger.slug, self.players[0].discord_id)
        # Still players[1]'s turn: their faction is in, but the vagabond isn't chosen.
        self.assertEqual(data["content"],
                         f"It's {self.players[1].name}'s turn to pick a faction.")
        self.assertIsNone(self._last_seat().vagabond_id)


class PickCaptainsFollowUpTests(TestCase):
    """Knaves of the Deepwood takes 3 captains.

    WITH a draft the offer is a roll of 4 and the choice is 3 of those, so a seat
    can't take captains the draft never offered. WITHOUT a draft there is nothing
    to be faithful to, so the whole captain-capable pool is offered.

    This fixture has NO draft, so its offer is the full pool (6 here).
    """

    THREAD_ID = "1303834523347456066"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)

        self.designer = Profile.objects.create(discord="pkcap", discord_id="800")
        self.knaves = Faction.objects.create(
            title="Knaves of the Deepwood", animal="Mole", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        self.other = Faction.objects.create(
            title="Cap Other Faction", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        # Six captain-capable, mirroring production: the roll must narrow to 4.
        self.captains = [
            Vagabond.objects.create(
                title=f"Cap Vagabond {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True, captain=True)
            for i in range(6)
        ]
        # A non-captain vagabond, which must never be offered.
        self.non_captain = Vagabond.objects.create(
            title="Cap Not A Captain", animal="Mouse", designer=self.designer,
            status=StatusChoices.STABLE, official=True, captain=False)

        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.players = [
            Profile.objects.create(discord=f"pkq{i}", discord_id=f"80{i}",
                                   display_name=f"Cap Player {i}")
            for i in range(1, 3)
        ]
        self.thread.players.set(self.players)
        for i, p in enumerate(self.players, 1):
            LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=i)
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])

    def _select(self, slug, clicker):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_faction", di.PICK_MODE_PLAYERS, self.OWNER, di.PICK_OPEN),
                "values": [slug],
            },
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_faction(payload).content)["data"]

    def _with_draft(self):
        """Give the thread a draft, so the captain offer is a ROLL OF 4 rather
        than the whole pool. Required by anything about the discarded 4th captain
        or about rejecting captains outside the offer -- both only exist because a
        draft constrains the offer."""
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(draft=draft, faction=self.knaves, order=1)
        LFGDraftPick.objects.create(draft=draft, faction=self.other, order=2)
        return draft

    def _choose_captains(self, slugs, clicker):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_captains", di.PICK_MODE_PLAYERS, self.OWNER,
                    self.knaves.slug, di.PICK_OPEN),
                "values": list(slugs),
            },
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_captains(payload).content)["data"]

    def _last_seat(self):
        return self.thread.seats.get(seat_number=2)

    def _offered(self, data):
        select = data["components"][0]["components"][0]
        return select, [o["value"] for o in select["options"]]

    def test_with_no_draft_every_captain_is_offered(self):
        """No draft means no rolled 4 to be faithful to, so the table chooses
        from the whole captain-capable pool rather than an arbitrary subset."""
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        select, values = self._offered(data)
        self.assertEqual(len(values), len(self.captains))
        self.assertEqual(set(values), {c.slug for c in self.captains})
        self.assertEqual(select["min_values"], 3)
        self.assertEqual(select["max_values"], 3)
        # Never the non-captain vagabond.
        self.assertNotIn(self.non_captain.slug, values)
        # The faction is deferred, exactly as the vagabond path defers it.
        self.assertIsNone(self._last_seat().faction_id)

    def test_with_a_draft_the_offer_is_still_a_roll_of_four(self):
        """The 3-of-4 rule is what makes a drafted Knaves seat honest; widening
        the no-draft offer must not have widened this one too."""
        self._with_draft()
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, values = self._offered(data)
        self.assertEqual(len(values), di.DRAFT_CAPTAIN_COUNT)

    def test_chosen_captains_show_on_the_board(self):
        """The seat line must say WHICH captains, not just Knaves -- that's the
        whole difference between two Knaves seats."""
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        with mock.patch.object(di, "vagabond_emoji_for",
                               lambda v: f"<:M{v.title.replace(' ', '')}:9>"):
            result = self._choose_captains(offered[:3], self.players[1].discord_id)
        chosen = Vagabond.objects.filter(slug__in=offered[:3])
        for captain in chosen:
            self.assertIn(f"<:M{captain.title.replace(' ', '')}:9>",
                          result["content"])

    def test_the_offer_is_not_shown_as_a_decision(self):
        """`captains` holds the OFFERED set while the prompt is open, so the board
        must not render it until the seat commits."""
        with mock.patch.object(di, "vagabond_emoji_for",
                               lambda v: f"<:M{v.title.replace(' ', '')}:9>"):
            data = self._select(self.knaves.slug, self.players[1].discord_id)
        # The parked offer is on the seat, but no captain emoji is on the board.
        self.assertEqual(self._last_seat().captains.count(), len(self.captains))
        self.assertNotIn("<:M", data["content"])

    def test_the_offer_is_parked_on_the_seat(self):
        """The bot is stateless and a custom_id can't carry a list, so the rolled
        4 ride on the seat until the follow-up narrows them."""
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, values = self._offered(data)
        parked = {c.slug for c in self._last_seat().captains.all()}
        self.assertEqual(parked, set(values))

    def test_choosing_three_stores_exactly_those_three(self):
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, values = self._offered(data)
        chosen = values[:3]
        self._choose_captains(chosen, self.players[1].discord_id)

        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.knaves.pk)
        self.assertEqual({c.slug for c in seat.captains.all()}, set(chosen))

    def test_the_stored_three_are_a_subset_of_the_four_offered(self):
        """The regression for the premise that only 4 captain-capable vagabonds
        exist. With 6 in the pool, a seat must not end up with captains that
        were never rolled."""
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        self._choose_captains(offered[:3], self.players[1].discord_id)
        stored = {c.slug for c in self._last_seat().captains.all()}
        self.assertTrue(stored <= set(offered))

    def test_captains_outside_the_offer_are_rejected(self):
        """A select echoes whatever values it is sent, so the handler must
        validate against the parked roll rather than trusting the payload.

        Needs a draft: without one every captain is offered, so there is no
        "outside the offer" to forge."""
        self._with_draft()
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        not_offered = [c.slug for c in self.captains if c.slug not in offered]
        self.assertTrue(not_offered, "need a captain outside the offer")

        forged = [offered[0], offered[1], not_offered[0]]
        result = self._choose_captains(forged, self.players[1].discord_id)
        self.assertIn("weren't the ones offered", result["content"])
        self.assertIsNone(self._last_seat().faction_id)

    def test_the_discarded_captain_is_stored(self):
        """The 4th offered captain. Only knowable at commit -- the chosen 3
        overwrite the parked 4. Needs a draft: only a rolled offer has a 4th."""
        self._with_draft()
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        chosen, expected = offered[:3], offered[3]
        self._choose_captains(chosen, self.players[1].discord_id)

        seat = self._last_seat()
        self.assertEqual(seat.discarded_captain.slug, expected)
        self.assertNotIn(expected, {c.slug for c in seat.captains.all()})

    def test_abandoning_knaves_clears_a_stale_discarded_captain(self):
        """A field left out of update_fields is silently not written, which would
        strand the discarded captain on whatever faction won instead. Needs a
        draft: with no draft nothing is discarded, so there is no stale value."""
        self._with_draft()
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        self._choose_captains(offered[:3], self.players[1].discord_id)
        self.assertIsNotNone(self._last_seat().discarded_captain_id)

        # Same seat picks again after a reset.
        di._pick_clear(self.thread)
        self.assertIsNone(self._last_seat().discarded_captain_id)

        self._select(self.other.slug, self.players[1].discord_id)
        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.other.pk)
        self.assertIsNone(seat.discarded_captain_id)

    def test_the_follow_up_records_captain_rolls(self):
        """ROLL_KIND_TO_BUCKET maps Captain -> captains; without these the record
        form's narrowing won't offer them."""
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        _select_c, offered = self._offered(data)
        with mock.patch.object(di.record_lfg_components_task, "delay") as capture:
            self._choose_captains(offered[:3], self.players[1].discord_id)
        items = capture.call_args.args[1]
        self.assertEqual(items[0]["kind"], "Faction")
        captains = [i["slug"] for i in items if i["kind"] == "Captain"]
        self.assertEqual(set(captains), set(offered[:3]))

    def test_abandoning_the_prompt_leaves_no_captains_on_another_faction(self):
        """The parked roll must not survive onto a different faction."""
        self._select(self.knaves.slug, self.players[1].discord_id)
        # Whatever was offered is parked; the size depends on whether a draft
        # constrained it, and what matters here is that it's cleared below.
        self.assertEqual(self._last_seat().captains.count(), len(self.captains))

        self._select(self.other.slug, self.players[1].discord_id)
        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.other.pk)
        self.assertEqual(seat.captains.count(), 0)

    def test_too_few_captains_skips_the_prompt_and_still_writes(self):
        Vagabond.objects.filter(captain=True).update(captain=False)
        data = self._select(self.knaves.slug, self.players[1].discord_id)
        seat = self._last_seat()
        self.assertEqual(seat.faction_id, self.knaves.pk)
        self.assertEqual(seat.captains.count(), 0)
        # Advanced to the next seat rather than prompting.
        self.assertIn(self.players[0].name, data["content"])


class PickSessionLifecycleTests(TestCase):
    """One /pick session at a time, and Stop as the way back to a clean slate."""

    THREAD_ID = "1303834523347456055"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)

        self.designer = Profile.objects.create(discord="pklife", discord_id="810")
        self.factions = [
            Faction.objects.create(
                title=f"Life Faction {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(4)
        ]
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.players = [
            Profile.objects.create(discord=f"pkl{i}", discord_id=f"81{i}",
                                   display_name=f"Life Player {i}")
            for i in range(1, 3)
        ]
        self.thread.players.set(self.players)
        for i, p in enumerate(self.players, 1):
            LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=i)
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])

    def _command(self):
        response = di._handle_pick_command({
            "_channel_id": self.THREAD_ID,
            "_author_id": self.OWNER,
            "_guild_id": None,
        })
        return json.loads(response.content)["data"]

    def _select(self, slug, clicker):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_faction", di.PICK_MODE_PLAYERS, self.OWNER, di.PICK_OPEN),
                "values": [slug],
            },
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_faction(payload).content)["data"]

    def _stop(self, clicker=None):
        """Stop is restricted to the table, so this clicks as a PLAYER by default
        (self.OWNER is not on the roster in this fixture)."""
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker or self.players[0].discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "pick_cancel", self.OWNER, di.PICK_OPEN)},
            "message": {"id": "msg", "components": []},
        }
        return json.loads(di._handle_pick_cancel(payload).content)["data"]

    def test_a_second_pick_is_refused_once_a_faction_is_taken(self):
        self._select(self.factions[0].slug, self.players[1].discord_id)
        data = self._command()
        self.assertIn("already underway", data["content"])

    def test_a_fresh_thread_still_opens_a_panel(self):
        data = self._command()
        self.assertNotIn("already underway", data["content"])

    def test_stop_clears_the_picks_but_keeps_the_seating(self):
        self._select(self.factions[0].slug, self.players[1].discord_id)
        data = self._stop()
        self.assertIn("cleared", data["content"])
        self.assertEqual(data["components"], [])

        self.assertEqual(self.thread.seats.count(), 2)
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.seating_set)
        self.assertFalse(
            self.thread.seats.filter(faction__isnull=False).exists())

    def test_pick_runs_again_after_stop(self):
        self._select(self.factions[0].slug, self.players[1].discord_id)
        self._stop()
        data = self._command()
        self.assertNotIn("already underway", data["content"])

    def test_stop_clears_vagabond_and_captains(self):
        vb = Vagabond.objects.create(
            title="Life Vagabond", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True)
        seat = self.thread.seats.get(seat_number=2)
        seat.faction = self.factions[0]
        seat.vagabond = vb
        seat.save()
        seat.captains.set([vb])

        seat.discarded_captain = vb
        seat.save(update_fields=["discarded_captain"])

        self._stop()
        seat.refresh_from_db()
        self.assertIsNone(seat.faction_id)
        self.assertIsNone(seat.vagabond_id)
        self.assertEqual(seat.captains.count(), 0)
        # .update() writes only the columns it names, so this is the guard that
        # the new field was actually added to that list.
        self.assertIsNone(seat.discarded_captain_id)

    def test_stop_deletes_pick_rolls_but_spares_other_sources(self):
        """/draft and /random share the roll log; their history isn't ours."""
        LFGRoll.objects.create(thread=self.thread, kind="Faction",
                               slug=self.factions[0].slug, source="pick")
        LFGRoll.objects.create(thread=self.thread, kind="Map",
                               slug="some-map", source="random")
        LFGRoll.objects.create(thread=self.thread, kind="Faction",
                               slug=self.factions[1].slug, source="draft")

        self._stop()
        remaining = sorted(
            LFGRoll.objects.filter(thread=self.thread).values_list("source", flat=True))
        self.assertEqual(remaining, ["draft", "random"])

    def test_stop_on_a_prompt_with_no_picks_says_so(self):
        """Cancel on the mode prompt must not claim to have cleared picks."""
        data = self._stop()
        self.assertNotIn("factions cleared", data["content"])
        self.assertTrue(data["content"].endswith("."))

    def test_stop_names_the_player_who_pressed_it(self):
        """Any of the PLAYERS can Stop -- not only whoever ran /pick -- so the
        table needs to see who discarded their picks."""
        self._select(self.factions[0].slug, self.players[1].discord_id)
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": self.players[1].discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "pick_cancel", self.OWNER, di.PICK_OPEN)},
            "message": {"id": "msg", "components": []},
        }
        data = json.loads(di._handle_pick_cancel(payload).content)["data"]
        self.assertIn(f"Picking stopped by {self.players[1].name}", data["content"])
        self.assertIn("factions cleared", data["content"])

    def test_a_non_player_cannot_stop_the_picks(self):
        """The buttons carry PICK_OPEN so the dispatcher's owner-lock is off --
        without this check anyone in the channel could discard the table's picks."""
        self._select(self.factions[0].slug, self.players[1].discord_id)
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": "999888777666555444",
                                "username": "stranger"}},
            "data": {"custom_id": di.encode_custom_id(
                "pick_cancel", self.OWNER, di.PICK_OPEN)},
            "message": {"id": "msg", "components": []},
        }
        data = json.loads(di._handle_pick_cancel(payload).content)["data"]
        self.assertEqual(data["flags"], di.EPHEMERAL)
        self.assertIn("Only the players", data["content"])
        # The picks survive.
        self.assertTrue(LFGSeat.objects.filter(
            thread=self.thread, faction__isnull=False).exists())

    def test_the_invoker_can_stop_when_they_are_a_player(self):
        """Whoever ran /pick is normally at the table; they stop it like anyone
        else on the roster, via the same membership check."""
        data = self._stop(clicker=self.players[1].discord_id)
        self.assertIn(f"Picking stopped by {self.players[1].name}",
                      data["content"])

    def test_stop_does_not_ping_whoever_pressed_it(self):
        """A plain name, not a <@id> mention -- this is public content."""
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": self.players[1].discord_id}},
            "data": {"custom_id": di.encode_custom_id(
                "pick_cancel", self.OWNER, di.PICK_OPEN)},
            "message": {"id": "msg", "components": []},
        }
        data = json.loads(di._handle_pick_cancel(payload).content)["data"]
        self.assertNotIn("<@", data["content"])
        self.assertEqual(data["allowed_mentions"], {"parse": []})

    def test_an_open_follow_up_does_not_lock_the_thread(self):
        """A seat mid-prompt has no faction, so an abandoned prompt must leave
        /pick runnable rather than trapping the table behind it."""
        vagabond_faction = Faction.objects.create(
            title="Vagabond", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True,
            component="Faction", type=Faction.TypeChoices.MILITANT)
        Vagabond.objects.create(
            title="Life Ranger", animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True)

        self._select(vagabond_faction.slug, self.players[1].discord_id)
        self.assertFalse(
            self.thread.seats.filter(faction__isnull=False).exists())
        data = self._command()
        self.assertNotIn("already underway", data["content"])


class PickSeatChoiceTests(TestCase):
    """The Seat-players / Assign-without-seating buttons, and what seating_set
    protects once one of them has run."""

    THREAD_ID = "1303834523347456088"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        designer = Profile.objects.create(discord="pkchoice", discord_id="780")
        self.factions = [
            Faction.objects.create(
                title=f"Choice Faction {i}", animal="Fox", designer=designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(5)
        ]
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.players = [
            Profile.objects.create(discord=f"pkc{i}", discord_id=f"78{i}",
                                   display_name=f"Choice Player {i}")
            for i in range(1, 4)
        ]
        self.thread.players.set(self.players)

    def _click(self, action):
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": self.OWNER}},
            "data": {"custom_id": di.encode_custom_id(action, self.OWNER)},
            "message": {"id": "msg", "components": []},
        }
        handler = di.COMPONENT_HANDLERS[action]
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            response = handler(payload)
        return json.loads(response.content)["data"], post

    def _reload(self):
        return LFGThread.objects.get(pk=self.thread.pk)

    def test_seat_players_creates_a_real_seating(self):
        data, post = self._click("pick_seat")
        thread = self._reload()
        self.assertTrue(thread.seating_set)
        self.assertEqual(thread.seats.count(), 3)
        # NOT announced: the panel this leads to lists the seats numbered, so a
        # separate seating message would say the same thing twice. /seating is
        # still the way to post an order to the thread.
        post.assert_not_called()
        # Then asks how the table wants to pick.
        self.assertIn("assign every faction yourself", data["content"])

    def test_skip_seating_leaves_seating_unset_and_asks_for_the_mode(self):
        """Skipping the seating creates filler seats but decides only the ORDER --
        the invoker still chooses whether players pick for themselves."""
        data, post = self._click("pick_noseat")
        thread = self._reload()
        self.assertFalse(thread.seating_set)
        self.assertEqual(thread.seats.count(), 3)
        post.assert_not_called()          # nothing announced
        labels = [c["label"] for c in data["components"][0]["components"]]
        self.assertEqual(labels, ["Players pick", "Assign all", "Cancel"])

    def test_after_assigning_seating_offers_a_normal_prompt_not_an_overwrite(self):
        """The regression seating_set exists to prevent: these seats are filler,
        so /seating must not claim it is replacing a seating order."""
        self._click("pick_noseat")
        data = json.loads(di._handle_seating_command({
            "_channel_id": self.THREAD_ID, "_author_id": self.OWNER,
        }).content)["data"]
        self.assertNotIn("overwrite", data["content"].lower())
        self.assertEqual(data["components"][0]["components"][0]["label"], "Yes")

    def test_after_a_real_seating_seating_still_warns(self):
        self._click("pick_seat")
        data = json.loads(di._handle_seating_command({
            "_channel_id": self.THREAD_ID, "_author_id": self.OWNER,
        }).content)["data"]
        self.assertIn("overwrite", data["content"].lower())

    def test_seating_after_assigning_does_not_say_reseated(self):
        self._click("pick_noseat")
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": self.OWNER}},
            "data": {"custom_id": di.encode_custom_id("draft_seat", self.OWNER)},
            "message": {"id": "msg", "components": []},
        }
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            di._handle_draft_seat(payload)
        self.assertNotIn("Re-seated", post.call_args.args[1])
        self.assertTrue(self._reload().seating_set)

    def test_a_real_seating_is_still_reachable_after_an_unordered_pick(self):
        """Filler seats must not be a one-way trap: /seating still establishes a
        real order afterwards. (/pick itself no longer offers to seat -- it asks
        only who chooses -- so /seating is the route.)"""
        self._click("pick_noseat")
        self.assertFalse(self._reload().seating_set)

        with mock.patch.object(di.post_channel_message_task, "delay"):
            di._handle_draft_seat({
                "channel_id": self.THREAD_ID,
                "member": {"user": {"id": self.OWNER}},
                "data": {"custom_id": di.encode_custom_id("draft_seat", self.OWNER)},
                "message": {"id": "msg", "components": []},
            })
        self.assertTrue(self._reload().seating_set)


class GuildAllowsTests(TestCase):
    """_guild_allows: the first handler-side read of enabled_commands."""

    def test_an_enabled_command_is_allowed(self):
        DiscordGuild.objects.create(guild_id="940000000000000001", name="G",
                                    enabled_commands=["pick", "seating"])
        self.assertTrue(di._guild_allows("940000000000000001", "seating"))

    def test_a_disabled_command_is_not(self):
        DiscordGuild.objects.create(guild_id="940000000000000002", name="G",
                                    enabled_commands=["pick"])
        self.assertFalse(di._guild_allows("940000000000000002", "seating"))

    def test_an_absent_guild_row_allows_nothing(self):
        """Matches /help: a guild with no row has nothing enabled but /help."""
        self.assertFalse(di._guild_allows("940000000000000003", "seating"))

    def test_an_empty_whitelist_allows_nothing(self):
        DiscordGuild.objects.create(guild_id="940000000000000004", name="G",
                                    enabled_commands=[])
        self.assertFalse(di._guild_allows("940000000000000004", "seating"))

    def test_no_guild_id_is_permissive(self):
        """A DM has no whitelist to consult, so nothing is withheld."""
        self.assertTrue(di._guild_allows(None, "seating"))


class RosterGuardedCommandTests(TestCase):
    """A thread with a roster belongs to its players: commands that WRITE to it are
    refused for anyone else.

    These go through the real view, not the handlers directly, because the guard
    lives in the command dispatcher -- calling a handler bypasses it entirely.
    """

    GUILD_ID = "1093259831470735512"
    THREAD_ID = "1303834523347456040"
    OUTSIDER = "999888777666555444"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        self.guild = DiscordGuild.objects.create(
            guild_id=self.GUILD_ID, name="Guard Guild")
        designer = Profile.objects.create(discord="guarddz", discord_id="700")
        for i in range(6):
            Faction.objects.create(
                title=f"Guard Faction {i}", animal="Fox", designer=designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.players = [
            Profile.objects.create(discord=f"guardp{i}", discord_id=f"71{i}",
                                   display_name=f"Guard Player {i}")
            for i in range(3)
        ]
        self.thread.players.set(self.players)

    def _command(self, name, user_id, channel_id=None, options=None):
        payload = {
            "type": 2,
            "data": {"name": name, "options": options or []},
            "guild_id": self.GUILD_ID,
            "channel_id": channel_id or self.THREAD_ID,
            "channel": {"name": "a thread", "type": 11},
            "member": {"user": {"id": user_id, "username": f"user{user_id}"}},
            "token": "tok",
        }
        with mock.patch.object(di, "_verify_signature", return_value=True):
            response = self.client.post(
                reverse("discord-interactions"), data=json.dumps(payload),
                content_type="application/json")
        return json.loads(response.content)["data"]

    def _refused(self, data):
        return "Only the players in this game" in data.get("content", "")

    def test_every_guarded_command_refuses_an_outsider(self):
        for name in sorted(di.ROSTER_GUARDED_COMMANDS):
            with self.subTest(command=name):
                self.assertTrue(self._refused(self._command(name, self.OUTSIDER)),
                                f"/{name} let a non-player through")

    def test_nothing_is_written_by_a_refused_command(self):
        self._command("pick", self.OUTSIDER)
        self._command("seating", self.OUTSIDER)
        thread = LFGThread.objects.get(pk=self.thread.pk)
        self.assertEqual(thread.seats.count(), 0)
        self.assertFalse(thread.seating_set)
        self.assertEqual(LFGRoll.objects.filter(thread=thread).count(), 0)

    def test_a_player_is_allowed(self):
        data = self._command("pick", self.players[0].discord_id)
        self.assertFalse(self._refused(data))

    def test_the_host_is_allowed_even_if_not_a_player(self):
        host = Profile.objects.create(discord="guardhost", discord_id="7900")
        self.thread.host = host
        self.thread.save(update_fields=["host"])
        self.assertNotIn(host, self.thread.players.all())
        self.assertFalse(self._refused(
            self._command("pick", host.discord_id)))

    def test_a_guild_moderator_is_allowed(self):
        mod = Profile.objects.create(discord="guardmod", discord_id="7901")
        self.guild.guild_moderators.add(mod)
        self.assertFalse(self._refused(self._command("pick", mod.discord_id)))

    def test_a_site_admin_is_allowed(self):
        # `admin` is derived from group == "A", not a settable field.
        admin = Profile.objects.create(discord="guardadmin", discord_id="7902",
                                       group="A")
        self.assertTrue(admin.admin)
        self.assertFalse(self._refused(self._command("pick", admin.discord_id)))

    # ── no roster, no restriction ────────────────────────────────────────────
    def test_a_plain_channel_is_open_to_anyone(self):
        data = self._command("faction", self.OUTSIDER, channel_id="555000999")
        self.assertFalse(self._refused(data))

    def test_a_thread_with_no_players_is_open(self):
        """A game still being set up has nothing to protect yet."""
        self.thread.players.clear()
        self.assertFalse(self._refused(self._command("pick", self.OUTSIDER)))

    def test_a_refused_channel_creates_no_profile(self):
        """ensure_profile_from_discord WRITES, so it must not run before a roster
        is established -- otherwise every lookup in any channel makes a row."""
        before = Profile.objects.count()
        self._command("faction", self.OUTSIDER, channel_id="555000999")
        self.assertEqual(Profile.objects.count(), before)

    # ── commands that stay open ──────────────────────────────────────────────
    def test_ungated_commands_still_work_for_anyone(self):
        for name in ("help", "record", "upcoming"):
            with self.subTest(command=name):
                self.assertFalse(self._refused(self._command(name, self.OUTSIDER)))

    def test_record_is_not_guarded(self):
        """It hands back a link the SITE re-checks permissions on, so gating it
        would block a moderator fixing a mis-recorded game without protecting
        anything."""
        self.assertNotIn("record", di.ROSTER_GUARDED_COMMANDS)

    # ── tournament group threads use a different roster source ───────────────
    def _group_thread(self, thread_id="1303834523347456041"):
        """A group thread whose roster comes from the PLAYER GROUP, not the
        LFGThread (which has no players of its own)."""
        designer = Profile.objects.create(discord="ggdz", discord_id="800")
        tournament = Tournament.objects.create(
            name="Guard Tournament", guild=self.guild, designer=designer)
        stage = Stage.objects.create(tournament=tournament, name="S", order=1)
        rnd = Round.objects.create(stage=stage, round_number=1)
        self.group_mod = Profile.objects.create(discord="ggmod", discord_id="8100")
        self.group = PlayerGroup.objects.create(
            round=rnd, group_number=1, name="Guard Group",
            discord_thread=(
                f"https://discord.com/channels/{self.GUILD_ID}/{thread_id}"),
            group_moderator=self.group_mod)
        series = MatchSeries.objects.create(
            round=rnd, player_group=self.group, number_of_games=1)
        Match.objects.create(round=rnd, series=series)
        self.group_players = [
            Profile.objects.create(discord=f"ggp{i}", discord_id=f"81{i}")
            for i in range(3)
        ]
        self.group.tournament_players.set([
            TournamentPlayer.objects.create(tournament=tournament, profile=p)
            for p in self.group_players
        ])
        return thread_id

    def test_a_group_thread_guards_by_the_groups_roster(self):
        thread_id = self._group_thread()
        self.assertTrue(self._refused(
            self._command("pick", self.OUTSIDER, channel_id=thread_id)))
        self.assertFalse(self._refused(self._command(
            "pick", self.group_players[0].discord_id, channel_id=thread_id)))

    def test_a_group_moderator_may_act_in_a_group_thread(self):
        thread_id = self._group_thread()
        self.assertFalse(self._refused(self._command(
            "pick", self.group_mod.discord_id, channel_id=thread_id)))

    def test_a_group_with_no_players_is_open(self):
        """Neither tournament_players nor MatchSeats: nothing to protect yet, and
        it closes the moment anyone is added."""
        thread_id = self._group_thread()
        self.group.tournament_players.clear()
        self.assertFalse(self._refused(
            self._command("pick", self.OUTSIDER, channel_id=thread_id)))


class PickCommandGroupThreadTests(TestCase):
    """/pick in a tournament group thread: it creates the LFGThread and seats the
    group in ROSTER order without establishing a seating, and must NOT write
    rolls. Run /seating first to get a real order -- /pick then reuses it."""

    GUILD_ID = "1093259831470735512"
    THREAD_ID = "1303834523347456040"
    OWNER = "111111111111111111"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        self.guild = DiscordGuild.objects.create(
            guild_id=self.GUILD_ID, name="Pick Guild")
        self.designer = Profile.objects.create(discord="pkgrp", discord_id="770")
        self.factions = [
            Faction.objects.create(
                title=f"Pick Grp Faction {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(4)
        ]
        self.tournament = Tournament.objects.create(
            name="Pick Tournament", guild=self.guild, designer=self.designer)
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name="Group A",
            discord_thread=(
                f"https://discord.com/channels/{self.GUILD_ID}/{self.THREAD_ID}"),
        )
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)

    def _members(self, count):
        profiles = [
            Profile.objects.create(discord=f"pkg{i}", discord_id=f"77{i}",
                                   display_name=f"Grp Pick {i}")
            for i in range(1, count + 1)
        ]
        self.group.tournament_players.set([
            TournamentPlayer.objects.create(tournament=self.tournament, profile=p)
            for p in profiles
        ])
        return profiles

    def _command(self):
        with mock.patch.object(di.post_channel_message_task, "delay"):
            response = di._handle_pick_command({
                "_channel_id": self.THREAD_ID, "_author_id": self.OWNER,
            })
        return json.loads(response.content)["data"]

    def _choose(self, action):
        """Click Seat Players / Skip Seating. A button payload carries NO channel
        name, so the group roster has to resolve by thread id alone -- which works
        because /pick linked the thread to the group before showing the prompt."""
        handler = (di._handle_pick_seat if action == "pick_seat"
                   else di._handle_pick_noseat)
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            response = handler({
                "channel_id": self.THREAD_ID,
                "member": {"user": {"id": self.OWNER}},
                "data": {"custom_id": di.encode_custom_id(action, self.OWNER)},
                "message": {"id": "msg", "components": []},
            })
        return json.loads(response.content)["data"], post

    def test_it_creates_the_series_thread_and_seats_the_group(self):
        self._members(3)
        self.assertFalse(LFGThread.objects.filter(thread_id=self.THREAD_ID).exists())
        self._command()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertEqual(thread.series, self.series)
        # The prompt itself writes no seats -- the button does.
        self.assertEqual(thread.seats.count(), 0)
        self._choose("pick_noseat")
        self.assertEqual(thread.seats.count(), 3)

    def test_it_works_without_a_draft(self):
        """With no /seating run, /pick offers the seating choice first."""
        self._members(3)
        data = self._command()
        labels = [c["label"] for c in data["components"][0]["components"]]
        self.assertEqual(labels, ["Seat Players", "Skip Seating", "Cancel"])

    def test_seat_players_seats_the_group_without_posting(self):
        """Guards the id-only roster lookup: the button payload has no channel
        name, so this only works because /pick linked the thread first."""
        players = self._members(3)
        self._command()
        data, post = self._choose("pick_seat")
        post.assert_not_called()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertTrue(thread.seating_set)
        self.assertEqual({s.profile_id for s in thread.seats.all()},
                         {p.pk for p in players})
        self.assertIn("in seat order", data["content"])

    def test_skip_seating_seats_the_group_in_roster_order(self):
        players = self._members(3)
        self._command()
        _data, post = self._choose("pick_noseat")
        post.assert_not_called()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertFalse(thread.seating_set)
        self.assertEqual([s.profile_id for s in thread.seats.order_by("seat_number")],
                         [p.pk for p in players])

    def _stop(self, clicker):
        return json.loads(di._handle_pick_cancel({
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": clicker, "username": "someone"}},
            "data": {"custom_id": di.encode_custom_id(
                "pick_cancel", self.OWNER, di.PICK_OPEN)},
            "message": {"id": "msg", "components": []},
        }).content)["data"]

    def test_only_group_members_can_stop_the_picks(self):
        """In a group thread the roster is the PLAYER GROUP's, not the LFGThread's
        (which is empty here), so the membership check has to resolve it that way."""
        players = self._members(3)
        self._command()
        self._choose("pick_noseat")

        refused = self._stop("999888777666555444")
        self.assertEqual(refused["flags"], di.EPHEMERAL)
        self.assertIn("Only the players", refused["content"])

        allowed = self._stop(players[0].discord_id)
        self.assertIn("Picking stopped", allowed["content"])

    def test_it_does_not_invent_a_seating(self):
        """/pick must not assert an order nobody asked for: the prompt itself
        shuffles nothing, sets no seating_set and posts nothing."""
        self._members(3)
        with mock.patch.object(di.post_channel_message_task, "delay") as post:
            di._handle_pick_command({
                "_channel_id": self.THREAD_ID, "_author_id": self.OWNER,
            })
        post.assert_not_called()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        self.assertFalse(thread.seating_set)
        self.assertEqual(thread.seats.count(), 0)

    def test_seating_first_still_drives_the_order(self):
        """The seats /seating established are reused, numbered, and not replaced."""
        self._members(3)
        di._handle_seating_command({
            "_channel_id": self.THREAD_ID, "_author_id": self.OWNER,
            "_guild_id": self.GUILD_ID,
        })
        before = list(self.thread_seat_ids())
        data = self._command()
        self.assertIn("Faction Picks", data["content"])
        self.assertEqual(before, list(self.thread_seat_ids()))

    def test_a_second_run_does_not_reseat(self):
        self._members(3)
        self._command()
        self._choose("pick_noseat")
        before = list(self.thread_seat_ids())
        self._command()
        self._choose("pick_noseat")
        self.assertEqual(before, list(self.thread_seat_ids()))

    def thread_seat_ids(self):
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        return thread.seats.order_by("seat_number").values_list("profile_id", flat=True)

    def test_a_group_thread_never_writes_rolls(self):
        """Match mode narrows its faction field from this same log, so a roll
        here would shrink that tournament match's allowed factions."""
        players = self._members(3)
        self._command()
        self._choose("pick_noseat")
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        last = thread.seats.order_by("-seat_number").first()
        payload = {
            "channel_id": self.THREAD_ID,
            "member": {"user": {"id": last.profile.discord_id}},
            "data": {
                "custom_id": di.encode_custom_id(
                    "pick_faction", di.PICK_MODE_PLAYERS, self.OWNER, di.PICK_OPEN),
                "values": [self.factions[0].slug],
            },
            "message": {"id": "msg", "components": []},
        }
        with mock.patch.object(di.record_lfg_components_task, "delay") as capture:
            di._handle_pick_faction(payload)
        capture.assert_not_called()
        # The seat itself is still written -- only the roll is skipped.
        self.assertEqual(thread.seats.get(pk=last.pk).faction, self.factions[0])

    @skipUnless(connection.vendor == "postgresql",
                "select_for_update is a no-op on SQLite, so this cannot fail there")
    def test_the_seat_lock_does_not_join_across_nullable_fks(self):
        """LFGSeat.profile and .faction are both nullable, so select_related on
        them LEFT OUTER JOINs -- and Postgres rejects FOR UPDATE against the
        nullable side of an outer join with NotSupportedError.

        This shipped because the dev suite runs on SQLite, where the lock is a
        no-op and the bad query is silently accepted. Guarded here so the
        select_related can't come back."""
        self._members(3)
        self._command()
        thread = LFGThread.objects.get(thread_id=self.THREAD_ID)
        seat = thread.seats.first()
        with transaction.atomic():
            self.assertIsNotNone(
                LFGSeat.objects.select_for_update().filter(pk=seat.pk).first())


class PickedFactionsByProfileTests(TestCase):
    """The join match mode uses to prefill factions picked with /pick.

    Keyed by profile rather than seat_number: MatchSeat.seat_number is nullable
    and /pick seats a group thread by shuffling the PlayerGroup roster, so a
    seat-number join could attach a faction to the wrong player."""

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        self.designer = Profile.objects.create(discord="pickdesigner", discord_id="730")
        self.factions = [
            Faction.objects.create(
                title=f"Pick Faction {i}", animal="Fox", designer=self.designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT)
            for i in range(3)
        ]
        self.thread = LFGThread.objects.create(thread_id="thread-picked-1")

    def test_maps_each_seat_to_its_own_profile(self):
        a = Profile.objects.create(discord="pickA", discord_id="731")
        b = Profile.objects.create(discord="pickB", discord_id="732")
        LFGSeat.objects.create(thread=self.thread, profile=a, seat_number=1,
                               faction=self.factions[0])
        LFGSeat.objects.create(thread=self.thread, profile=b, seat_number=2,
                               faction=self.factions[1])

        picked = picked_factions_by_profile(self.thread)
        self.assertEqual(picked[a.pk].faction, self.factions[0])
        self.assertEqual(picked[b.pk].faction, self.factions[1])

    def test_seat_numbers_do_not_decide_the_mapping(self):
        """The regression this key exists to prevent: /pick's seat numbers are a
        shuffle of the group roster and need not line up with MatchSeat's, so a
        positional or seat-number join would swap these two players' factions."""
        a = Profile.objects.create(discord="pickC", discord_id="733")
        b = Profile.objects.create(discord="pickD", discord_id="734")
        # `a` sits LAST here; a seat-number join against a MatchSeat list that
        # happens to hold `a` first would hand `a` the wrong faction.
        LFGSeat.objects.create(thread=self.thread, profile=b, seat_number=1,
                               faction=self.factions[0])
        LFGSeat.objects.create(thread=self.thread, profile=a, seat_number=2,
                               faction=self.factions[1])

        picked = picked_factions_by_profile(self.thread)
        self.assertEqual(picked[a.pk].faction, self.factions[1])
        self.assertEqual(picked[b.pk].faction, self.factions[0])

    def test_a_player_who_left_the_series_is_simply_absent(self):
        """Stale seating is self-correcting: no roster comparison needed."""
        gone = Profile.objects.create(discord="pickGone", discord_id="735")
        LFGSeat.objects.create(thread=self.thread, profile=gone, seat_number=1,
                               faction=self.factions[0])
        replacement = Profile.objects.create(discord="pickNew", discord_id="736")

        picked = picked_factions_by_profile(self.thread)
        self.assertIn(gone.pk, picked)
        self.assertNotIn(replacement.pk, picked)

    def test_a_seat_with_no_profile_is_skipped(self):
        """A deleted Profile leaves the seat behind with profile=None; it must
        not land under a None key and collide with another such seat."""
        drop = Profile.objects.create(discord="pickDrop", discord_id="737")
        LFGSeat.objects.create(thread=self.thread, profile=drop, seat_number=1,
                               faction=self.factions[0])
        drop.delete()

        self.assertEqual(picked_factions_by_profile(self.thread), {})

    def test_unpicked_seats_carry_no_faction(self):
        p = Profile.objects.create(discord="pickNone", discord_id="738")
        LFGSeat.objects.create(thread=self.thread, profile=p, seat_number=1)

        picked = picked_factions_by_profile(self.thread)
        self.assertIsNone(picked[p.pk].faction_id)
        self.assertIsNone(picked[p.pk].vagabond_id)


class RandomOptionsPanelTests(TestCase):
    """/random's options panel: the selects gather platform, fan content and (for
    Hirelings) the side, then Roll resolves from the message's own select state."""

    OWNER = "123456789012345678"  # must look like a snowflake for the owner-lock
    OTHER = "987654321098765432"

    def setUp(self):
        # Saving a Faction rewrites the stock animal image in place; nothing here
        # tests image handling. Same guard DraftLFGSeatingTests uses.
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)

        designer = Profile.objects.create(discord="fandesigner", discord_id="700")
        for i in range(3):
            Faction.objects.create(
                title=f"Official Faction {i}", animal="Fox", designer=designer,
                status=StatusChoices.STABLE, official=True,
                component="Faction", type=Faction.TypeChoices.MILITANT,
            )
        for i in range(2):
            Faction.objects.create(
                title=f"Fan Faction {i}", animal="Fox", designer=designer,
                status=StatusChoices.STABLE, official=False,
                component="Faction", type=Faction.TypeChoices.MILITANT,
            )

    # ── helpers ─────────────────────────────────────────────────────────────
    def _panel(self, kind="Faction", **state):
        return di._random_options_data(kind, self.OWNER, **state)

    def _payload(self, action, kind="Faction", values=None, owner=None,
                 clicker=None, **state):
        """A component interaction whose message carries a rendered panel."""
        panel = self._panel(kind, **state)
        data = {"custom_id": di.encode_custom_id(action, kind, owner or self.OWNER)}
        if action == "random_cancel":
            data["custom_id"] = di.encode_custom_id(action, owner or self.OWNER)
        if values is not None:
            data["values"] = values
        return {
            "type": di.MESSAGE_COMPONENT,
            "data": data,
            "message": {"components": panel["components"]},
            "channel_id": "chan-1",
            "member": {"user": {"id": clicker or owner or self.OWNER,
                                "username": "roller"}},
        }

    def _selects(self, body):
        """{action: [defaulted values]} for each select in a response body."""
        out = {}
        for row in body["data"]["components"]:
            for comp in row["components"]:
                if comp["type"] == 3:
                    action = comp["custom_id"].split(":")[0]
                    out[action] = [o["value"] for o in comp["options"]
                                   if o.get("default")]
        return out

    def _custom_ids(self, components):
        return [c["custom_id"] for row in components for c in row["components"]]

    # ── the pool ────────────────────────────────────────────────────────────
    def test_eligible_excludes_fan_by_default(self):
        titles = [f.title for f in di._random_eligible(
            "Faction", di.DRAFT_PLATFORM_TTS)]
        self.assertEqual(len(titles), 3)
        self.assertFalse([t for t in titles if t.startswith("Fan")])

    def test_eligible_includes_fan_when_asked(self):
        titles = [f.title for f in di._random_eligible(
            "Faction", di.DRAFT_PLATFORM_TTS, include_fan_content=True)]
        self.assertEqual(len(titles), 5)
        self.assertTrue([t for t in titles if t.startswith("Fan")])

    def test_roll_defaults_to_official_only(self):
        """An untouched panel rolls the official pool — the no-regression case."""
        body = json.loads(di._handle_random_roll_post(
            self._payload("random_roll_post")).content)
        description = body["data"]["embeds"][0]["description"]
        self.assertNotIn("fan content", description)
        self.assertIn(body["data"]["embeds"][0]["title"].split(": ")[1],
                      [f"Official Faction {i}" for i in range(3)])

    def test_roll_includes_fan_when_selected(self):
        body = json.loads(di._handle_random_roll_post(
            self._payload("random_roll_post", fan="1")).content)
        self.assertIn("incl. fan content",
                      body["data"]["embeds"][0]["description"])

    # ── panel state ─────────────────────────────────────────────────────────
    def test_changing_a_select_applies_the_new_value(self):
        """The fired select must be overridden with its echoed value; the message
        state still holds the pre-change value, so reading it alone loses the
        change and the panel appears frozen."""
        body = json.loads(di._handle_random_option(
            self._payload("random_opt_fan", values=["1"], fan="0")).content)
        self.assertEqual(self._selects(body)["random_opt_fan"], ["1"])

    def test_selecting_an_option_preserves_the_others(self):
        body = json.loads(di._handle_random_option(self._payload(
            "random_opt_platform", kind="Hireling", values=["rd"],
            platform_key="tts", fan="1", side="P")).content)
        selects = self._selects(body)
        self.assertEqual(selects["random_opt_platform"], ["rd"])
        self.assertEqual(selects["random_opt_fan"], ["1"])
        self.assertEqual(selects["random_opt_side"], ["P"])

    def test_panel_state_round_trips(self):
        platform, side, fan = di._random_panel_state(self._payload(
            "random_roll_post", kind="Hireling", platform_key="rd", fan="1",
            side="D"))
        self.assertEqual(platform, di.DRAFT_PLATFORM_RD)
        self.assertEqual(side, "D")
        self.assertTrue(fan)

    # ── panel shape ─────────────────────────────────────────────────────────
    def test_hireling_panel_has_side_select_and_others_do_not(self):
        self.assertIn("random_opt_side", self._selects(
            {"data": self._panel("Hireling")}))
        self.assertNotIn("random_opt_side", self._selects(
            {"data": self._panel("Faction")}))

    def test_select_action_names_do_not_prefix_each_other(self):
        """selected_values matches by startswith, so a shared stem would make the
        first select shadow the rest."""
        names = list(di.RANDOM_OPTION_ACTIONS)
        for name in names:
            self.assertEqual([n for n in names if n.startswith(name)], [name])

    def test_panel_custom_ids_end_in_owner_and_fit(self):
        for kind in ("Faction", "Hireling"):
            for custom_id in self._custom_ids(self._panel(kind)["components"]):
                self.assertTrue(custom_id.endswith(f":{self.OWNER}"), custom_id)
                self.assertLessEqual(len(custom_id), 100, custom_id)

    # ── dispatch ────────────────────────────────────────────────────────────
    def test_owner_lock_fires_on_every_panel_control(self):
        """Someone other than the invoker gets refused on every control. Goes
        through the real view, since the lock lives in its component dispatch."""
        url = reverse("discord-interactions")
        for action in list(di.RANDOM_OPTION_ACTIONS) + ["random_roll_post",
                                                        "random_cancel"]:
            payload = self._payload(action, values=["1"], clicker=self.OTHER)
            with mock.patch.object(di, "_verify_signature", return_value=True):
                response = self.client.post(
                    url, data=json.dumps(payload), content_type="application/json")
            self.assertIn("Only the host",
                          json.loads(response.content)["data"]["content"], action)

    def test_owner_may_use_the_panel_controls(self):
        """The lock must not fire on the invoker — the flip side of the test above,
        so a lock that refuses everyone can't pass."""
        url = reverse("discord-interactions")
        payload = self._payload("random_opt_fan", values=["1"])
        with mock.patch.object(di, "_verify_signature", return_value=True):
            response = self.client.post(
                url, data=json.dumps(payload), content_type="application/json")
        body = json.loads(response.content)
        self.assertEqual(self._selects(body)["random_opt_fan"], ["1"])

    def test_cancel_clears_the_panel(self):
        body = json.loads(di._handle_random_cancel(
            self._payload("random_cancel")).content)
        self.assertEqual(body["data"]["components"], [])
        self.assertIn("cancelled", body["data"]["content"])

    # ── command routing ─────────────────────────────────────────────────────
    def _command(self, kind):
        return json.loads(di._handle_random_command({
            "options": [{"name": "kind", "value": kind}],
            "_author_id": self.OWNER, "_channel_id": "chan-1",
        }).content)

    def test_post_kinds_open_a_panel(self):
        for kind in ("Faction", "Hireling", "Captain", "Map"):
            body = self._command(kind)
            self.assertIn(f"**Random {kind}**", body["data"]["content"], kind)

    def test_roll_still_uses_the_dice_prompt(self):
        body = self._command("Roll")
        self.assertIn("how many dice", body["data"]["content"])

    def test_suit_resolves_without_a_panel(self):
        body = self._command("Suit")
        self.assertNotIn("components", body["data"])
        self.assertTrue(body["data"]["embeds"][0]["title"].startswith("Random Suit"))


# ── /schedule participant confirmation ───────────────────────────────────────
# The consensus flow: a proposed time every roster player must confirm before it
# reaches Match.scheduled_time. Gated per tournament by
# require_participant_schedule_confirmation (default True).

class ScheduleConsensusGateTests(ScheduleFixtureMixin, TestCase):
    """_consensus_required decides which flow runs, and is the ONLY place that
    decision is made. Two conditions: the tournament opts in AND there's a roster
    to poll. recording_access is deliberately NOT one of them -- it governs who
    may WRITE the time, not who may say when they're free."""

    def test_flag_defaults_true(self):
        self.build()
        self.assertTrue(self.tournament.require_participant_schedule_confirmation)

    def test_requires_confirmation_follows_the_flag_alone(self):
        self.build()
        self.assertTrue(self.tournament.requires_schedule_confirmation())

        self.tournament.require_participant_schedule_confirmation = False
        self.assertFalse(self.tournament.requires_schedule_confirmation())

    def test_moderators_only_access_still_polls_the_players(self):
        """Being unable to SET a time is no reason to be unable to say when you
        can play. Under MODERATORS access the roster still confirms; the write
        then waits on a moderator pressing Set Time."""
        self.build(recording_access=Tournament.RecordingAccessTypes.MODERATORS,
                   populate_group=True)
        self.assertTrue(self.tournament.require_participant_schedule_confirmation)
        self.assertTrue(self.tournament.requires_schedule_confirmation())
        required, roster = di._consensus_required(self.match)
        self.assertTrue(required)
        self.assertEqual(len(roster), 2)

    def test_seated_roster_enables_consensus_without_group_m2m(self):
        """A group populated by SEATS alone still gets consensus.

        _match_roster falls back to MatchSeat when tournament_players is empty,
        because can_schedule authorizes off seats: without the fallback these
        players could set a time but would never be asked to confirm one."""
        self.build()  # populate_group=False -> no tournament_players
        required, roster = di._consensus_required(self.match)
        self.assertTrue(required)
        self.assertEqual([p.pk for p in roster], [self.player.pk])

    def test_no_roster_at_all_disables_consensus(self):
        """With neither group members NOR seats there is nobody to poll, so the
        original single-confirm path stands."""
        self.build()
        MatchSeat.objects.filter(series=self.series).delete()
        required, roster = di._consensus_required(self.match)
        self.assertFalse(required)
        self.assertEqual(roster, [])

    def test_required_when_flag_and_roster_present(self):
        self.build(populate_group=True)
        required, roster = di._consensus_required(self.match)
        self.assertTrue(required)
        self.assertEqual(len(roster), 2)


class ScheduleRosterTests(ScheduleFixtureMixin, TestCase):

    def test_roster_falls_back_to_seats(self):
        """self.player is SEATED but not in the group's M2M. tournament_players is
        still PREFERRED (see test_roster_lists_group_members); seats are only
        consulted when it yields nobody."""
        self.build()
        self.assertEqual([p.pk for p in di._match_roster(self.match)],
                         [self.player.pk])

    def test_roster_prefers_group_members_over_seats(self):
        """With the M2M populated the seat fallback must not run: self.teammate is
        a group member with NO seat, and must still appear."""
        self.build(populate_group=True)
        ids = {p.pk for p in di._match_roster(self.match)}
        self.assertIn(self.teammate.pk, ids)

    def test_roster_empty_without_members_or_seats(self):
        self.build()
        MatchSeat.objects.filter(series=self.series).delete()
        self.assertEqual(di._match_roster(self.match), [])

    def test_roster_lists_group_members(self):
        self.build(populate_group=True)
        ids = {p.pk for p in di._match_roster(self.match)}
        self.assertEqual(ids, {self.player.pk, self.teammate.pk})

    def test_roster_dedupes(self):
        self.build(populate_group=True)
        self.group.tournament_players.add(self.tournament_player)
        self.assertEqual(len(di._match_roster(self.match)), 2)

    def test_no_player_group(self):
        self.build()
        self.series.player_group = None
        self.series.save(update_fields=["player_group"])
        self.match.refresh_from_db()
        self.assertEqual(di._match_roster(self.match), [])


class ResolveClickerTests(ScheduleFixtureMixin, TestCase):
    """Three outcomes. Only MATCHED may act on a proposal."""

    def setUp(self):
        self.build(populate_group=True)
        self.roster = di._match_roster(self.match)

    def test_discord_id_match_is_matched(self):
        profile, status = di._resolve_clicker(self.roster, "2", "anything")
        self.assertEqual(status, di.CLICKER_MATCHED)
        self.assertEqual(profile.pk, self.player.pk)

    def test_username_match_on_unlinked_profile_is_unlinked_not_matched(self):
        self.teammate.discord_id = None
        self.teammate.save(update_fields=["discord_id"])
        profile, status = di._resolve_clicker(
            di._match_roster(self.match), "999888777666555444", "teammate")
        self.assertEqual(status, di.CLICKER_UNLINKED)
        self.assertEqual(profile.pk, self.teammate.pk)

    def test_username_match_never_writes_discord_id(self):
        """The escalation guard: a username is user-controlled, so it must never
        bind a snowflake to a Profile that the whole bot then trusts."""
        self.teammate.discord_id = None
        self.teammate.save(update_fields=["discord_id"])
        di._resolve_clicker(di._match_roster(self.match), "999888777666555444", "teammate")
        self.teammate.refresh_from_db()
        self.assertIsNone(self.teammate.discord_id)

    def test_username_match_is_sanitized(self):
        self.teammate.discord_id = None
        self.teammate.discord = "team.mate"
        self.teammate.save(update_fields=["discord_id", "discord"])
        _profile, status = di._resolve_clicker(
            di._match_roster(self.match), "999888777666555444", "Team.Mate")
        self.assertEqual(status, di.CLICKER_UNLINKED)

    def test_linked_profile_never_matched_by_username(self):
        """teammate already has discord_id=5; a stranger claiming that username
        must not resolve to them."""
        _profile, status = di._resolve_clicker(self.roster, "999888777666555444", "teammate")
        self.assertEqual(status, di.CLICKER_UNKNOWN)

    def test_non_roster_is_unknown(self):
        _profile, status = di._resolve_clicker(self.roster, "3", "outsider")
        self.assertEqual(status, di.CLICKER_UNKNOWN)

    def test_no_username_is_unknown(self):
        _profile, status = di._resolve_clicker(self.roster, "999888777666555444", None)
        self.assertEqual(status, di.CLICKER_UNKNOWN)


class ScheduleProposalCommandTests(ScheduleFixtureMixin, TestCase):
    """The Confirm button creates a proposal instead of writing the time."""

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.ts = int(self.when.timestamp())

    def _payload(self, owner=None):
        return {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_confirm", self.match.id, self.ts,
                owner or self.player.discord_id)},
            "guild_id": self.guild.guild_id,
            "channel_id": "555000111",
            "token": None,
        }

    def _confirm(self, owner=None):
        with mock.patch.object(di.post_schedule_proposal_task, "apply_async") as enqueue:
            response = di._handle_schedule_confirm(self._payload(owner))
        return response, enqueue

    def test_creates_proposal_and_does_not_write_time(self):
        response, _ = self._confirm()
        self.assertEqual(json.loads(response.content)["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)
        self.assertEqual(ScheduleProposal.objects.count(), 1)

    def test_proposer_is_seeded_as_confirmed(self):
        self._confirm()
        proposal = ScheduleProposal.objects.get()
        self.assertEqual([p.pk for p in proposal.confirmed_by.all()], [self.player.pk])
        self.assertEqual([p.pk for p in proposal.pending_profiles()], [self.teammate.pk])

    def test_non_roster_moderator_seeds_nobody(self):
        """A group moderator scheduling a game they don't play in confirms no one —
        every player still has to agree."""
        self._confirm(owner=self.group_mod.discord_id)
        proposal = ScheduleProposal.objects.get()
        self.assertEqual(proposal.confirmed_by.count(), 0)
        self.assertEqual(proposal.pending_profiles().count(), 2)

    def test_enqueues_public_post_with_proposal_id(self):
        _response, enqueue = self._confirm()
        proposal = ScheduleProposal.objects.get()
        self.assertEqual(enqueue.call_args.args[0][0], proposal.pk)
        self.assertEqual(enqueue.call_args.kwargs["countdown"], 2)

    def test_records_channel_for_later_edits(self):
        self._confirm()
        self.assertEqual(ScheduleProposal.objects.get().channel_id, "555000111")


class ScheduleLegacyPathTests(ScheduleFixtureMixin, TestCase):
    """The regression guard: with consensus off, /schedule behaves exactly as it
    did before this feature."""

    def setUp(self):
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.ts = int(self.when.timestamp())

    def _payload(self):
        return {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_confirm", self.match.id, self.ts, self.player.discord_id)},
            "guild_id": self.guild.guild_id,
            "channel_id": "555000111",
            "token": None,
        }

    def test_flag_off_with_full_roster_writes_directly(self):
        self.build(populate_group=True)
        self.tournament.require_participant_schedule_confirmation = False
        self.tournament.save(update_fields=["require_participant_schedule_confirmation"])
        di._handle_schedule_confirm(self._payload())
        self.match.refresh_from_db()
        self.assertEqual(int(self.match.scheduled_time.timestamp()), self.ts)
        self.assertEqual(ScheduleProposal.objects.count(), 0)

    def test_moderators_access_now_opens_a_proposal(self):
        """MODERATORS access used to skip consensus entirely. It now polls the
        roster like any other tournament -- only the final write is reserved."""
        self.build(recording_access=Tournament.RecordingAccessTypes.MODERATORS,
                   populate_group=True)
        with mock.patch.object(di.post_schedule_proposal_task, "delay"):
            di._handle_schedule_confirm({
                "data": {"custom_id": di.encode_custom_id(
                    "schedule_confirm", self.match.id, self.ts,
                    self.group_mod.discord_id)},
                "guild_id": self.guild.guild_id, "channel_id": "555000111",
                "token": None,
            })
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)
        self.assertEqual(ScheduleProposal.objects.count(), 1)

    def test_gate_is_rechecked_on_the_button_not_encoded(self):
        """Flipping the setting between prompt and click takes effect immediately."""
        self.build(populate_group=True)
        self.tournament.require_participant_schedule_confirmation = False
        self.tournament.save(update_fields=["require_participant_schedule_confirmation"])
        di._handle_schedule_confirm(self._payload())
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.scheduled_time)


class ScheduleProposalButtonTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player,
            channel_id="555000111", guild_id=self.guild.guild_id)
        self.proposal.roster.set([self.player, self.teammate])
        self.proposal.confirmed_by.add(self.player)

    def _payload(self, action="sched_prop_ok", user_id="5", username="teammate",
                 proposal_id=None):
        return {
            "data": {"custom_id": di.encode_custom_id(
                action, proposal_id or self.proposal.pk, "g")},
            "guild_id": self.guild.guild_id,
            "member": {"user": {"id": user_id, "username": username}},
            "token": None,
        }

    def _body(self, response):
        return json.loads(response.content)

    def test_agreed_when_the_proposer_may_not_schedule(self):
        """Consent and authority are separate. Under MODERATORS access the roster
        can agree, but the time waits for someone allowed to write it."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.AGREED)
        labels = [c["label"] for c in body["data"]["components"][0]["components"]]
        self.assertEqual(labels, ["Set Time", "Reject"])

    def test_set_time_refuses_a_player(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        body = self._body(di._handle_schedule_proposal_set(
            self._payload(action="sched_prop_set")))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_set_time_writes_for_a_moderator(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        self._body(di._handle_schedule_proposal_set(self._payload(
            action="sched_prop_set", user_id=self.group_mod.discord_id,
            username="groupmod")))
        self.match.refresh_from_db()
        self.assertEqual(self.match.scheduled_time, self.when)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CONFIRMED)

    def test_confirm_is_refused_once_agreed(self):
        """Consent is already complete there -- only Set Time and Reject apply."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.assertIn("already agreed", body["data"]["content"])

    def test_a_moderator_can_reject_an_agreed_time(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            self._body(di._handle_schedule_proposal_reject(self._payload(
                action="sched_prop_no", user_id=self.group_mod.discord_id,
                username="groupmod")))
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.REJECTED)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_a_player_cannot_reject_an_agreed_time(self):
        """Rejecting a time still being negotiated is ordinary; destroying a
        completed agreement on one late click undoes everyone else's work."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        body = self._body(di._handle_schedule_proposal_reject(
            self._payload(action="sched_prop_no")))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.assertIn("Ask a moderator", body["data"]["content"])
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.AGREED)

    def test_a_player_can_still_reject_an_open_time(self):
        """The narrowing applies only to AGREED -- an open proposal is unchanged."""
        third = Profile.objects.create(discord="third", discord_id="6")
        self.proposal.roster.add(third)
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            self._body(di._handle_schedule_proposal_reject(
                self._payload(action="sched_prop_no")))
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.REJECTED)

    def test_an_agreed_proposal_is_still_swept(self):
        """AGREED is live, so it must not survive a time set another way."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            with self.captureOnCommitCallbacks(execute=True):
                di._cancel_open_proposals(self.match, "cancelled")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_an_agreed_proposal_is_still_expired(self):
        from the_gatehouse.tasks import cleanup_stale_schedule_proposals
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        di._handle_schedule_proposal_confirm(self._payload())
        ScheduleProposal.objects.filter(pk=self.proposal.pk).update(
            proposed_time=timezone.now() - timedelta(days=1))
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            cleanup_stale_schedule_proposals()
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_non_roster_clicker_refused(self):
        body = self._body(di._handle_schedule_proposal_confirm(
            self._payload(user_id="3", username="outsider")))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.assertIn("not one of this game's players", body["data"]["content"])
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_final_confirm_writes_time_and_clears_buttons(self):
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertEqual(body["type"], di.RESPONSE_UPDATE_MESSAGE)
        self.assertEqual(body["data"]["components"], [])
        self.match.refresh_from_db()
        self.assertEqual(self.match.scheduled_time, self.when)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CONFIRMED)

    def test_partial_confirm_keeps_buttons_and_writes_nothing(self):
        third = Profile.objects.create(discord="third", discord_id="6")
        self.proposal.roster.add(third)
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertTrue(body["data"]["components"])
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_double_click_is_idempotent(self):
        third = Profile.objects.create(discord="third", discord_id="6")
        self.proposal.roster.add(third)
        di._handle_schedule_proposal_confirm(self._payload())
        di._handle_schedule_proposal_confirm(self._payload())
        self.assertEqual(self.proposal.confirmed_by.count(), 2)

    def test_finalize_preserves_derived_name(self):
        original_name, original_number = self.match.name, self.match.match_number
        di._handle_schedule_proposal_confirm(self._payload())
        self.match.refresh_from_db()
        self.assertEqual(self.match.name, original_name)
        self.assertEqual(self.match.match_number, original_number)

    def test_reject_retires_proposal_without_writing(self):
        body = self._body(di._handle_schedule_proposal_reject(self._payload(
            action="sched_prop_no")))
        self.assertEqual(body["data"]["components"], [])
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.REJECTED)
        self.assertEqual(self.proposal.rejected_by_id, self.teammate.pk)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_click_on_terminal_proposal_refused(self):
        self.proposal.status = ScheduleProposal.Status.REJECTED
        self.proposal.save(update_fields=["status"])
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)

    def test_past_proposed_time_refused(self):
        self.proposal.proposed_time = timezone.now() - timedelta(hours=1)
        self.proposal.save(update_fields=["proposed_time"])
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertIn("already passed", body["data"]["content"])

    def test_cross_guild_refused(self):
        payload = self._payload()
        payload["guild_id"] = "999999"
        body = self._body(di._handle_schedule_proposal_confirm(payload))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_stale_custom_id_refused(self):
        payload = {"data": {"custom_id": "sched_prop_ok"},
                   "guild_id": self.guild.guild_id, "token": None}
        body = self._body(di._handle_schedule_proposal_confirm(payload))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)

    def test_match_played_since_proposal_refused(self):
        self.match.status = CompetitionStatus.COMPLETED
        self.match.save(update_fields=["status"])
        body = self._body(di._handle_schedule_proposal_confirm(self._payload()))
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)


class UnlinkedClickerTests(ScheduleFixtureMixin, TestCase):
    """A roster player whose Discord isn't linked is told how to fix it, and
    cannot act until they do."""

    def setUp(self):
        self.build(populate_group=True)
        self.teammate.discord_id = None
        self.teammate.save(update_fields=["discord_id"])
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player,
            channel_id="555000111", guild_id=self.guild.guild_id)
        self.proposal.roster.set([self.player, self.teammate])
        self.proposal.confirmed_by.add(self.player)

    def _payload(self, action="sched_prop_ok"):
        return {
            "data": {"custom_id": di.encode_custom_id(action, self.proposal.pk, "g")},
            "guild_id": self.guild.guild_id,
            "member": {"user": {"id": "999888777666555444", "username": "teammate"}},
            "token": None,
        }

    def test_confirm_tells_them_to_log_in_and_does_not_count(self):
        body = json.loads(di._handle_schedule_proposal_confirm(self._payload()).content)
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.assertIn("isn't linked", body["data"]["content"])
        self.assertEqual(self.proposal.confirmed_by.count(), 1)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)

    def test_impersonator_cannot_finalize(self):
        di._handle_schedule_proposal_confirm(self._payload())
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.OPEN)

    def test_pending_list_marks_unlinked_players(self):
        value = di._name_list_value([self.teammate])
        self.assertIn("not linked", value)


class ScheduleProposalRejectEligibilityTests(ScheduleFixtureMixin, TestCase):
    """Reject accepts a wider set than Confirm: any roster player (even one who
    already confirmed) plus anyone who passes can_schedule."""

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player,
            channel_id="555000111", guild_id=self.guild.guild_id)
        self.proposal.roster.set([self.player, self.teammate])
        self.proposal.confirmed_by.add(self.player)

    def _reject(self, user_id, username):
        return json.loads(di._handle_schedule_proposal_reject({
            "data": {"custom_id": di.encode_custom_id(
                "sched_prop_no", self.proposal.pk, "g")},
            "guild_id": self.guild.guild_id,
            "member": {"user": {"id": user_id, "username": username}},
            "token": None,
        }).content)

    def test_already_confirmed_player_may_still_reject(self):
        """Plans change — earlier consent must not trap the group."""
        self._reject("2", "player")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.REJECTED)

    def test_group_moderator_off_roster_may_reject(self):
        """The escape hatch for a proposal stuck behind an unresponsive player."""
        self._reject("4", "groupmod")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.REJECTED)

    def test_outsider_cannot_reject(self):
        body = self._reject("3", "outsider")
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.OPEN)


class SchedulePermissionTests(ScheduleFixtureMixin, TestCase):
    """Confirmations express CONSENT; authority to schedule comes from the
    proposer and is re-asserted at write time."""

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player,
            channel_id="555000111", guild_id=self.guild.guild_id)
        self.proposal.roster.set([self.player, self.teammate])
        self.proposal.confirmed_by.set([self.player, self.teammate])

    def test_revoked_permission_cancels_instead_of_confirming(self):
        """The ordering guard: a refused proposal must land CANCELLED, never
        CONFIRMED-with-no-time."""
        self.tournament.recording_access = Tournament.RecordingAccessTypes.MODERATORS
        self.tournament.save(update_fields=["recording_access"])
        ok, error = di._finalize_proposal(self.proposal)
        self.assertFalse(ok)
        self.assertIn("permission", error)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_missing_proposer_cancels(self):
        self.proposal.proposed_by = None
        self.proposal.save(update_fields=["proposed_by"])
        ok, _error = di._finalize_proposal(self.proposal)
        self.assertFalse(ok)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_authorised_finalize_writes(self):
        ok, error = di._finalize_proposal(self.proposal)
        self.assertTrue(ok, error)
        self.match.refresh_from_db()
        self.assertEqual(self.match.scheduled_time, self.when)


class ScheduleProposalSupersedeTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build(populate_group=True)
        self.when_a = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.when_b = (timezone.now() + timedelta(days=11)).replace(microsecond=0)
        self.a = self._proposal(self.when_a, "111")
        self.b = self._proposal(self.when_b, "222")

    def _proposal(self, when, message_id):
        proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=when, proposed_by=self.player,
            channel_id="555000111", message_id=message_id,
            guild_id=self.guild.guild_id)
        proposal.roster.set([self.player, self.teammate])
        proposal.confirmed_by.set([self.player, self.teammate])
        return proposal

    def test_finalizing_one_supersedes_the_other(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay") as strip:
                ok, _error = di._finalize_proposal(self.a)
        self.assertTrue(ok)
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, ScheduleProposal.Status.SUPERSEDED)
        # One sweep of the losing proposals. _finalize_proposal also defers a
        # schedule-channel announcement, so assert the strip task specifically rather
        # than counting every on_commit callback.
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay") as strip:
            for cb in callbacks:
                cb()
        self.assertEqual(strip.call_count, 1)

    def test_strip_task_enqueued_for_loser_only(self):
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay") as strip:
            with self.captureOnCommitCallbacks(execute=True):
                di._finalize_proposal(self.a)
        self.assertEqual(strip.call_args.args[0], [self.b.pk])
        self.assertEqual(strip.call_args.args[1], "superseded")

    def test_confirming_loser_after_finalize_changes_nothing(self):
        """The real safety property: the DB status refuses the click even if the
        cosmetic button-strip never happened."""
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            di._finalize_proposal(self.a)
        self.match.refresh_from_db()
        original = self.match.scheduled_time
        body = json.loads(di._handle_schedule_proposal_confirm({
            "data": {"custom_id": di.encode_custom_id("sched_prop_ok", self.b.pk, "g")},
            "guild_id": self.guild.guild_id,
            "member": {"user": {"id": "5", "username": "teammate"}},
            "token": None,
        }).content)
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertEqual(self.match.scheduled_time, original)

    def test_compare_and_swap_blocks_a_second_finalize(self):
        """Runs on SQLite, where select_for_update is a no-op — so this is what
        actually proves the race is closed."""
        ScheduleProposal.objects.filter(pk=self.a.pk).update(
            status=ScheduleProposal.Status.CONFIRMED)
        ok, error = di._finalize_proposal(self.a)
        self.assertFalse(ok)
        self.assertIn("first", error)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)


class ScheduleProposalInvalidationTests(_NoLoginSignalMixin, ScheduleFixtureMixin,
                                        TestCase):
    """Every path that writes or clears scheduled_time must retire open proposals."""

    def setUp(self):
        super().setUp()
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.ts = int(self.when.timestamp())
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player,
            channel_id="555000111", message_id="111", guild_id=self.guild.guild_id)
        self.proposal.roster.set([self.player, self.teammate])

    def test_legacy_direct_write_cancels_open_proposals(self):
        self.tournament.require_participant_schedule_confirmation = False
        self.tournament.save(update_fields=["require_participant_schedule_confirmation"])
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            with self.captureOnCommitCallbacks(execute=True):
                di._handle_schedule_confirm({
                    "data": {"custom_id": di.encode_custom_id(
                        "schedule_confirm", self.match.id, self.ts,
                        self.player.discord_id)},
                    "guild_id": self.guild.guild_id, "channel_id": "555000111",
                    "token": None,
                })
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_clearing_cancels_open_proposals(self):
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])
        with mock.patch.object(di.strip_schedule_proposal_messages_task, "delay"):
            with self.captureOnCommitCallbacks(execute=True):
                di._handle_schedule_clear_confirm({
                    "data": {"custom_id": di.encode_custom_id(
                        "schedule_clear_confirm", self.match.id,
                        self.player.discord_id)},
                    "guild_id": self.guild.guild_id, "token": None,
                })
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)

    def test_website_edit_cancels_open_proposals(self):
        """The bracket editor writes scheduled_time directly. Without this sweep a
        stale Confirm button in Discord could later overwrite the time set here."""
        from django.urls import reverse
        from django.contrib.auth.models import User

        # create_user auto-creates the Profile, so use that one rather than
        # reassigning self.designer (Profile.user is unique).
        user = User.objects.create_user(username="organizer", password="pw")
        self.tournament.designer = user.profile
        self.tournament.save(update_fields=["designer"])
        self.client.force_login(user)

        url = reverse("round-edit-series", kwargs={
            "tournament_slug": self.tournament.slug,
            "stage_slug": self.stage.slug,
            "round_slug": self.round.slug,
        })
        new_time = (timezone.now() + timedelta(days=20)).replace(microsecond=0)
        with mock.patch.object(
                di.strip_schedule_proposal_messages_task, "delay") as strip:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    url,
                    data=json.dumps({
                        "series_id": self.series.id,
                        "matches": [{"id": self.match.id,
                                     "scheduled_time": new_time.isoformat()}],
                    }),
                    content_type="application/json",
                )
        self.assertEqual(response.status_code, 200)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)
        # The message edit says WHERE the time came from, not just that the
        # proposal died -- "cancelled" is a vaguer catch-all.
        _ids, reason = strip.call_args.args
        self.assertEqual(reason, "website")

    def test_website_reason_has_its_own_message(self):
        from the_gatehouse.tasks import _PROPOSAL_RETIRED_TEXT
        text = _PROPOSAL_RETIRED_TEXT["website"]
        self.assertIn("website", text.lower())
        self.assertNotEqual(text, _PROPOSAL_RETIRED_TEXT["cancelled"])

    def test_confirm_on_cancelled_proposal_refused(self):
        self.proposal.status = ScheduleProposal.Status.CANCELLED
        self.proposal.save(update_fields=["status"])
        body = json.loads(di._handle_schedule_proposal_confirm({
            "data": {"custom_id": di.encode_custom_id(
                "sched_prop_ok", self.proposal.pk, "g")},
            "guild_id": self.guild.guild_id,
            "member": {"user": {"id": "5", "username": "teammate"}},
            "token": None,
        }).content)
        self.assertEqual(body["data"]["flags"], di.EPHEMERAL)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.scheduled_time)


class ScheduleProposalRenderTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build(populate_group=True)
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match, proposed_time=self.when, proposed_by=self.player)
        self.proposal.roster.set([self.player, self.teammate])
        self.proposal.confirmed_by.add(self.player)

    def test_custom_ids_do_not_end_in_a_snowflake(self):
        """The owner-lock regression guard: a trailing snowflake would lock every
        player except the proposer out of their own confirmation."""
        data = di._schedule_proposal_data(self.proposal, self.match)
        for component in data["components"][0]["components"]:
            _action, args = di.decode_custom_id(component["custom_id"])
            last = args[-1]
            self.assertFalse(last.isdigit() and len(last) >= 17,
                             f"{component['custom_id']} would trigger the owner-lock")

    def test_custom_ids_within_length_cap(self):
        data = di._schedule_proposal_data(self.proposal, self.match)
        for component in data["components"][0]["components"]:
            self.assertLessEqual(len(component["custom_id"]), 100)

    def test_pending_view_lists_both_groups(self):
        data = di._schedule_proposal_data(self.proposal, self.match)
        fields = {f["name"]: f["value"] for f in data["embeds"][0]["fields"]}
        self.assertIn(f"<@{self.teammate.discord_id}>", fields["Waiting on"])
        self.assertIn(f"<@{self.player.discord_id}>", fields["✅ Confirmed"])
        self.assertNotIn(f"<@{self.player.discord_id}>", fields["Waiting on"])

    def test_pings_are_off_so_nothing_notifies(self):
        """SCHEDULE_PROPOSAL_PINGS is off: no content line and no open mentions,
        even on the first post. The embed still names who is waiting."""
        first = di._schedule_proposal_data(self.proposal, self.match, mention=True)
        self.assertNotIn("content", first)
        self.assertEqual(first["allowed_mentions"]["parse"], [])
        edit = di._schedule_proposal_data(self.proposal, self.match)
        self.assertNotIn("content", edit)
        self.assertEqual(edit["allowed_mentions"]["parse"], [])

    def test_enabling_pings_mentions_the_pending_players(self):
        """The disabled path must keep working, so exercise it explicitly."""
        with mock.patch.object(di, "SCHEDULE_PROPOSAL_PINGS", True):
            first = di._schedule_proposal_data(
                self.proposal, self.match, mention=True)
            edit = di._schedule_proposal_data(self.proposal, self.match)
        pending = list(self.proposal.pending_profiles())
        self.assertTrue(pending, "fixture needs someone still to confirm")
        for profile in pending:
            if profile.discord_id:
                self.assertIn(f"<@{profile.discord_id}>", first["content"])
        self.assertEqual(first["allowed_mentions"]["parse"], ["users"])
        # Still only the FIRST post -- an edit carries no mention line.
        self.assertNotIn("content", edit)

    def test_an_unlinked_player_is_not_pinged_but_is_still_named(self):
        unlinked = Profile.objects.create(discord="nolink", display_name="No Link")
        self.proposal.roster.add(unlinked)
        with mock.patch.object(di, "SCHEDULE_PROPOSAL_PINGS", True):
            data = di._schedule_proposal_data(
                self.proposal, self.match, mention=True)
        self.assertNotIn("No Link", data.get("content", ""))
        waiting = data["embeds"][0]["fields"][0]["value"]
        self.assertIn("No Link", waiting)

    def test_rejected_view_does_not_name_the_rejecter(self):
        self.proposal.rejected_by = self.teammate
        data = di._schedule_rejected_data(self.proposal)
        description = data["embeds"][0]["description"]
        self.assertEqual(data["components"], [])
        # Match the rendered mention/name forms, not a bare id — a short snowflake
        # like "5" also occurs inside the <t:...> timestamp.
        self.assertNotIn(f"<@{self.teammate.discord_id}>", description)
        self.assertNotIn(self.teammate.discord, description)
        self.assertIn("A player rejected", description)

    def test_finalized_view_clears_components(self):
        self.match.scheduled_time = self.when
        self.match.save(update_fields=["scheduled_time"])
        data = di._schedule_finalized_data(self.proposal, self.match)
        self.assertEqual(data["components"], [])
        names = [f["name"] for f in data["embeds"][0]["fields"]]
        self.assertIn("✅ Confirmed by", names)

    def test_field_value_truncates_at_discord_cap(self):
        """An over-long field makes Discord reject the whole edit — which would
        silently discard a change already committed to the DB."""
        many = [Profile(discord=f"p{i}", discord_id=str(10**17 + i)) for i in range(200)]
        value = di._name_list_value(many)
        self.assertLessEqual(len(value), 1024)
        self.assertIn("more", value)

    def test_handlers_are_registered(self):
        self.assertIn("sched_prop_ok", di.COMPONENT_HANDLERS)
        self.assertIn("sched_prop_no", di.COMPONENT_HANDLERS)


class PostChannelMessageFullTests(TestCase):

    def test_returns_message_id_on_success(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"id": "12345"}
        with mock.patch.object(ds.requests, "post", return_value=response):
            result, message_id = ds.post_channel_message_full("chan", content="hi")
        self.assertEqual(result, ds.THREAD_OK)
        self.assertEqual(message_id, "12345")

    def test_permanent_failure_is_blocked(self):
        err = ds.requests.RequestException("nope")
        err.response = mock.Mock(status_code=403, text="forbidden")
        with mock.patch.object(ds.requests, "post", side_effect=err):
            result, message_id = ds.post_channel_message_full("chan", content="hi")
        self.assertEqual(result, ds.THREAD_BLOCKED)
        self.assertIsNone(message_id)

    def test_transient_failure_is_error(self):
        err = ds.requests.RequestException("boom")
        err.response = mock.Mock(status_code=500, text="server error")
        with mock.patch.object(ds.requests, "post", side_effect=err):
            result, _id = ds.post_channel_message_full("chan", content="hi")
        self.assertEqual(result, ds.THREAD_ERROR)


class ScheduleProposalTaskTests(ScheduleFixtureMixin, TestCase):

    def setUp(self):
        self.build(populate_group=True)
        self.proposal = ScheduleProposal.objects.create(
            match=self.match,
            proposed_time=timezone.now() + timedelta(days=10),
            proposed_by=self.player, channel_id="555000111")

    def test_post_task_records_message_id(self):
        from the_gatehouse import tasks
        with mock.patch(
            "the_gatehouse.services.discordservice.post_channel_message_full",
            return_value=(ds.THREAD_OK, "98765"),
        ) as post:
            tasks.post_schedule_proposal_task(self.proposal.pk, {"content": "x"})
        post.assert_called_once()
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.message_id, "98765")

    def test_post_task_reraises_transient_failure(self):
        from the_gatehouse import tasks
        with mock.patch(
            "the_gatehouse.services.discordservice.post_channel_message_full",
            return_value=(ds.THREAD_ERROR, None),
        ):
            with self.assertRaises(Exception):
                tasks.post_schedule_proposal_task(self.proposal.pk, {"content": "x"})

    def test_post_task_skips_non_open_proposal(self):
        from the_gatehouse import tasks
        self.proposal.status = ScheduleProposal.Status.SUPERSEDED
        self.proposal.save(update_fields=["status"])
        with mock.patch(
            "the_gatehouse.services.discordservice.post_channel_message_full",
        ) as post:
            tasks.post_schedule_proposal_task(self.proposal.pk, {"content": "x"})
        post.assert_not_called()

    def test_strip_task_skips_blank_message_id(self):
        from the_gatehouse import tasks
        with mock.patch(
            "the_gatehouse.services.discordservice.edit_channel_message",
        ) as edit:
            tasks.strip_schedule_proposal_messages_task([self.proposal.pk], "superseded")
        edit.assert_not_called()

    def test_strip_task_swallows_permanent_failure(self):
        from the_gatehouse import tasks
        self.proposal.message_id = "111"
        self.proposal.save(update_fields=["message_id"])
        with mock.patch(
            "the_gatehouse.services.discordservice.edit_channel_message",
            return_value=ds.THREAD_BLOCKED,
        ):
            tasks.strip_schedule_proposal_messages_task([self.proposal.pk], "superseded")

    def test_strip_task_reraises_transient_failure(self):
        from the_gatehouse import tasks
        self.proposal.message_id = "111"
        self.proposal.save(update_fields=["message_id"])
        with mock.patch(
            "the_gatehouse.services.discordservice.edit_channel_message",
            return_value=ds.THREAD_ERROR,
        ):
            with self.assertRaises(Exception):
                tasks.strip_schedule_proposal_messages_task(
                    [self.proposal.pk], "superseded")

    def test_cleanup_retires_past_proposals(self):
        from the_gatehouse import tasks
        self.proposal.proposed_time = timezone.now() - timedelta(hours=1)
        self.proposal.message_id = "111"
        self.proposal.save(update_fields=["proposed_time", "message_id"])
        with mock.patch.object(tasks.strip_schedule_proposal_messages_task, "delay"):
            retired = tasks.cleanup_stale_schedule_proposals()
        self.assertEqual(retired, 1)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ScheduleProposal.Status.CANCELLED)


class InlineGuildSyncOnLoginTests(TestCase):
    """Login refreshes Discord guilds INLINE when the cached group can't be trusted.

    A returning player already has group='P' persisted, so @player_required passes and
    the async refresh is fine. But a first login (never synced) or a still-Outcast user
    renders against a stale group and gets bounced. Those cases sync on the request
    thread, under a budget, so the group is right before the user's first click.

    These tests keep user_logged_in_handler CONNECTED (unlike _NoLoginSignalMixin) since
    the handler is what's under test; the Discord calls and Celery sends are mocked.
    """

    WW_GUILD = [{'id': 'ww-guild'}]

    def setUp(self):
        self.user = User.objects.create_user(username='newbie', password='pw')
        self.profile = self.user.profile
        # Every test patches the Discord boundary; nothing here touches the network.
        for target in ('send_discord_message_task', 'update_discord_avatar_task'):
            p = mock.patch(f'the_gatehouse.signals.{target}')
            p.start()
            self.addCleanup(p.stop)
    def _login(self):
        """Log in with the signal connected; returns the mocked async task.

        Fires the signal by logging the user in directly against a REAL request from
        RequestFactory, with session + messages attached. client.login() builds a bare
        request with no SERVER_NAME and no message storage, which the handler needs for
        its absolute URLs and welcome message — that's why the rest of the suite
        disconnects the handler, but here the handler is the thing under test.
        """
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request._messages = FallbackStorage(request)
        with mock.patch('the_gatehouse.signals.refresh_user_guilds_task') as task:
            # The async hand-off is wrapped in transaction.on_commit, which never runs
            # inside TestCase's rolled-back transaction unless captured.
            with self.captureOnCommitCallbacks(execute=True):
                auth_login(request, self.user,
                           backend='django.contrib.auth.backends.ModelBackend')
        request.session.save()
        return task

    def _patch_discord(self, guilds, display_name='Newbie'):
        """Patch the Discord boundary as imported inside refresh_user_guilds."""
        guilds_p = mock.patch(
            'the_gatehouse.services.discordservice.get_user_guilds', return_value=guilds)
        name_p = mock.patch(
            'the_gatehouse.services.discordservice.get_discord_display_name',
            return_value=display_name)
        derive_p = mock.patch(
            'the_gatehouse.services.discordservice.derive_guild_membership',
            return_value=(bool(guilds), False, False))
        update_p = mock.patch(
            'the_gatehouse.services.discordservice.update_user_guilds')
        mocks = [p.start() for p in (guilds_p, name_p, derive_p, update_p)]
        for p in (guilds_p, name_p, derive_p, update_p):
            self.addCleanup(p.stop)
        return mocks[0], mocks[1]

    def test_first_login_in_ww_promotes_inline(self):
        """The bug: a brand-new WW member must be group P before their first click."""
        self._patch_discord(self.WW_GUILD)
        self._login()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, 'P')
        self.assertTrue(self.profile.player)
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNotNone(self.profile.guilds_synced_at)

    def test_successful_inline_sync_does_not_also_enqueue_task(self):
        """Guards the double-refresh bug: no redundant Celery job after an inline sync."""
        self._patch_discord(self.WW_GUILD)
        task = self._login()
        task.delay.assert_not_called()

    def test_returning_player_makes_no_inline_discord_call(self):
        """Regression guard for the outage: the common path must stay fully async."""
        self.profile.group = 'P'
        self.profile.guilds_synced_at = timezone.now()
        self.profile.save(update_fields=['group', 'guilds_synced_at'])

        get_guilds, _ = self._patch_discord(self.WW_GUILD)
        task = self._login()

        get_guilds.assert_not_called()
        task.delay.assert_called_once_with(self.user.id)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.guilds_refreshing)

    def test_discord_failure_falls_back_to_async(self):
        """None means API failure: never demote, keep the spinner, hand off to Celery."""
        self._patch_discord(None)
        task = self._login()

        task.delay.assert_called_once_with(self.user.id)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, 'O')
        self.assertTrue(self.profile.guilds_refreshing)

    def test_login_survives_discord_exception(self):
        """A Discord outage must never break login itself."""
        with mock.patch('the_gatehouse.services.discordservice.get_user_guilds',
                        side_effect=RuntimeError('discord down')):
            task = self._login()

        task.delay.assert_called_once_with(self.user.id)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.guilds_refreshing)

    def test_non_ww_user_stays_outcast(self):
        self._patch_discord([])
        self._login()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, 'O')
        self.assertFalse(self.profile.player)

    def test_discord_id_backfill_is_not_clobbered_by_inline_sync(self):
        """The handler's own save() must not write its stale copy over the fresh group."""
        self._patch_discord(self.WW_GUILD)
        with mock.patch('the_gatehouse.signals.get_discord_id', return_value='42'):
            self._login()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.discord_id, '42')
        self.assertEqual(self.profile.group, 'P')


class RefreshUserGuildsBudgetTests(TestCase):
    """The inline path is bounded by a monotonic deadline, not per-call timeouts."""

    def setUp(self):
        self.user = User.objects.create_user(username='budget', password='pw')

    def _fake_clock(self):
        """A monotonic clock the test advances explicitly, via clock.advance(n)."""
        class Clock:
            now = 0.0
            def __call__(self):
                return self.now
            def advance(self, seconds):
                self.now += seconds
        return Clock()

    def test_budget_exhaustion_skips_the_cosmetic_display_name(self):
        """Group promotion gates access; the nickname can wait for the async task."""
        from the_gatehouse import tasks

        clock = self._fake_clock()

        def slow_guilds(user, timeout=None):
            clock.advance(99)   # a slow Discord eats the whole budget
            return [{'id': 'ww-guild'}]

        with mock.patch.object(tasks.time, 'monotonic', clock), \
             mock.patch('the_gatehouse.services.discordservice.get_user_guilds',
                        side_effect=slow_guilds), \
             mock.patch('the_gatehouse.services.discordservice.update_user_guilds'), \
             mock.patch('the_gatehouse.services.discordservice.derive_guild_membership',
                        return_value=(True, False, False)), \
             mock.patch('the_gatehouse.services.discordservice.get_discord_display_name'
                        ) as name:
            ok = tasks.refresh_user_guilds(self.user, budget=6)

        # The group promotion still landed and was saved...
        self.assertTrue(ok)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.group, 'P')
        # ...but the optional display-name lookup was skipped.
        name.assert_not_called()

    def test_exhausted_budget_before_first_call_returns_false(self):
        from the_gatehouse import tasks

        clock = self._fake_clock()
        clock.now = 99   # already past the deadline when we start

        with mock.patch.object(tasks.time, 'monotonic', clock), \
             mock.patch('the_gatehouse.services.discordservice.get_user_guilds'
                        ) as get_guilds:
            ok = tasks.refresh_user_guilds(self.user, budget=-1)

        self.assertFalse(ok)
        get_guilds.assert_not_called()

    def test_no_budget_means_no_timeout_override(self):
        """The async task path must keep the historical per-call 5s defaults."""
        from the_gatehouse import tasks

        with mock.patch('the_gatehouse.services.discordservice.get_user_guilds',
                        return_value=[]) as get_guilds, \
             mock.patch('the_gatehouse.services.discordservice.update_user_guilds'), \
             mock.patch('the_gatehouse.services.discordservice.derive_guild_membership',
                        return_value=(False, False, False)), \
             mock.patch('the_gatehouse.services.discordservice.get_discord_display_name',
                        return_value='x'):
            tasks.refresh_user_guilds(self.user)

        self.assertEqual(get_guilds.call_args.kwargs, {})


class FinishingSigninViewTests(_NoLoginSignalMixin, TestCase):
    """The interstitial holds a user whose sync didn't finish, then routes them on."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='holder', password='pw')
        self.profile = self.user.profile
        self.client.force_login(self.user)
        self.url = reverse('finishing-signin')

    def test_holds_while_refreshing(self):
        self.profile.guilds_refreshing = True
        self.profile.save(update_fields=['guilds_refreshing'])
        response = self.client.get(self.url, {'next': '/some/page/'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'the_gatehouse/finishing_signin.html')

    def test_forwards_to_next_once_synced_and_player(self):
        self.profile.group = 'P'
        self.profile.guilds_refreshing = False
        self.profile.save(update_fields=['group', 'guilds_refreshing'])
        response = self.client.get(self.url, {'next': '/some/page/'})
        self.assertRedirects(response, '/some/page/', fetch_redirect_response=False)

    def test_non_player_is_not_ping_ponged_back_to_next(self):
        """Sync finished and they're genuinely not in WW: route explicitly, not via next."""
        self.profile.group = 'O'
        self.profile.guilds_refreshing = False
        self.profile.save(update_fields=['group', 'guilds_refreshing'])
        response = self.client.get(self.url, {'next': '/some/page/'})
        self.assertRedirects(response, reverse('woodland-warriors-info'),
                             fetch_redirect_response=False)

    def test_rejects_off_host_next(self):
        self.profile.group = 'P'
        self.profile.guilds_refreshing = False
        self.profile.save(update_fields=['group', 'guilds_refreshing'])
        response = self.client.get(self.url, {'next': 'https://evil.example.com/'})
        self.assertRedirects(response, reverse('site-home'),
                             fetch_redirect_response=False)


class PlayerRequiredInterstitialTests(_NoLoginSignalMixin, TestCase):
    """@player_required sends a mid-sync user to the interstitial, not the WW info page."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='gated', password='pw')
        self.profile = self.user.profile
        self.factory = RequestFactory()

    def _run_decorator(self, path='/gated/page/?x=1'):
        @views.player_required
        def view(request):
            return HttpResponse('ok')

        request = self.factory.get(path)
        request.user = self.user
        return view(request)

    def test_redirects_to_interstitial_while_refreshing(self):
        self.profile.guilds_refreshing = True
        self.profile.save(update_fields=['guilds_refreshing'])
        response = self._run_decorator()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('finishing-signin'), response.url)
        # The query string must survive the round trip.
        self.assertIn('x%3D1', response.url)

    def test_redirects_to_ww_info_when_not_refreshing(self):
        self.profile.guilds_refreshing = False
        self.profile.save(update_fields=['guilds_refreshing'])
        response = self._run_decorator()
        self.assertRedirects(response, reverse('woodland-warriors-info'),
                             fetch_redirect_response=False)


class _AvatarTestMixin:
    """Helpers for driving update_discord_avatar without touching the network."""

    @staticmethod
    def _png_bytes(size=(1024, 1024)):
        buffer = io.BytesIO()
        Image.new('RGBA', size, (10, 200, 90, 255)).save(buffer, format='PNG')
        return buffer.getvalue()

    def _write_avatar(self, user, avatar='abc', status=200):
        """Run the avatar download the way the Celery task does."""
        social = type('SA', (), {
            'extra_data': {'id': '80351110224678912', 'avatar': avatar}
        })()
        response = type('Resp', (), {
            'content': self._png_bytes(), 'status_code': status
        })()
        with mock.patch(
            'the_gatehouse.services.discordservice.SocialAccount'
        ) as social_mock, mock.patch(
            'the_gatehouse.services.discordservice.requests.get',
            return_value=response,
        ):
            social_mock.objects.filter.return_value.first.return_value = social
            return update_discord_avatar(user, force=True)


class ProfileAvatarConcurrencyTests(_AvatarTestMixin, TestCase):
    """Profile.image is written by a Celery task while requests hold their own
    in-memory copy of the profile. Profile.save() used to delete whichever file
    it considered 'old', which meant a stale copy deleted the avatar the worker
    had just downloaded and reverted the pointer to the default.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='avatar-tests-')
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        patcher = override_settings(MEDIA_ROOT=self.media_root)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user(username='avataruser', password='x')
        self.user.refresh_from_db()

    def test_stale_bare_save_keeps_avatar(self):
        """A request that loaded the profile BEFORE the avatar landed must not
        delete the file or revert the pointer when it saves its own change."""
        stale = Profile.objects.get(pk=self.user.profile.pk)
        self.assertEqual(stale.image.name, DEFAULT_PROFILE_IMAGE)

        self._write_avatar(User.objects.get(pk=self.user.pk))
        written = Profile.objects.get(pk=self.user.profile.pk).image.name
        self.assertNotEqual(written, DEFAULT_PROFILE_IMAGE)

        stale.player_onboard = True
        stale.save()

        final = Profile.objects.get(pk=self.user.profile.pk)
        self.assertEqual(final.image.name, written)
        self.assertTrue(final.image.storage.exists(final.image.name))
        # The edit the request actually intended must still persist.
        self.assertTrue(final.player_onboard)

    def test_update_fields_save_leaves_image_file_alone(self):
        """The deletion check runs before super().save(), so a save that doesn't
        write `image` used to delete the file while leaving the DB pointer intact
        — a valid-looking path aimed at nothing."""
        stale = Profile.objects.get(pk=self.user.profile.pk)
        self._write_avatar(User.objects.get(pk=self.user.pk))
        written = Profile.objects.get(pk=self.user.profile.pk).image.name

        stale.player_onboard = True
        stale.save(update_fields=['player_onboard'])

        final = Profile.objects.get(pk=self.user.profile.pk)
        self.assertEqual(final.image.name, written)
        self.assertTrue(final.image.storage.exists(final.image.name))

    def test_genuine_replacement_still_cleans_up_old_file(self):
        """The original cleanup behaviour must survive the fix."""
        self._write_avatar(User.objects.get(pk=self.user.pk))
        first = Profile.objects.get(pk=self.user.profile.pk).image.name

        self._write_avatar(User.objects.get(pk=self.user.pk), avatar='zzz')

        final = Profile.objects.get(pk=self.user.profile.pk)
        self.assertNotEqual(final.image.name, first)
        self.assertTrue(final.image.storage.exists(final.image.name))
        self.assertFalse(final.image.storage.exists(first))

    def test_deliberate_reset_to_default_still_works(self):
        """repair_profile_avatars resets via queryset update, which bypasses the
        stale-revert guard in save()."""
        self._write_avatar(User.objects.get(pk=self.user.pk))
        profile = Profile.objects.get(pk=self.user.profile.pk)

        Profile.objects.filter(pk=profile.pk).update(image=DEFAULT_PROFILE_IMAGE)

        self.assertEqual(
            Profile.objects.get(pk=profile.pk).image.name, DEFAULT_PROFILE_IMAGE
        )

    def test_saved_avatar_is_really_webp(self):
        """The upload path always produces a .webp name, so the bytes must be
        WebP too rather than PNG under a .webp extension."""
        self._write_avatar(User.objects.get(pk=self.user.pk))
        profile = Profile.objects.get(pk=self.user.profile.pk)

        self.assertTrue(profile.image.name.endswith('.webp'))
        self.assertEqual(Image.open(profile.image.path).format, 'WEBP')

    def test_user_without_custom_avatar_gets_discord_default(self):
        """A falsy avatar hash used to return early, leaving the profile unset."""
        result = self._write_avatar(User.objects.get(pk=self.user.pk), avatar=None)

        profile = Profile.objects.get(pk=self.user.profile.pk)
        self.assertIsNotNone(result)
        self.assertNotEqual(profile.image.name, DEFAULT_PROFILE_IMAGE)
        self.assertTrue(profile.image.storage.exists(profile.image.name))


class RepairProfileAvatarsCommandTests(_AvatarTestMixin, TestCase):

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='avatar-repair-')
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        patcher = override_settings(MEDIA_ROOT=self.media_root)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user(username='repairuser', password='x')
        self.user.refresh_from_db()

    def _break_avatar(self):
        """Point the profile at a file that isn't there, the dead-link state."""
        self._write_avatar(User.objects.get(pk=self.user.pk))
        profile = Profile.objects.get(pk=self.user.profile.pk)
        profile.image.storage.delete(profile.image.name)
        return profile.image.name

    def test_dry_run_reports_without_writing(self):
        broken = self._break_avatar()
        out = io.StringIO()
        call_command('repair_profile_avatars', '--dry-run', stdout=out)

        self.assertIn('missing 1', out.getvalue())
        self.assertEqual(
            Profile.objects.get(pk=self.user.profile.pk).image.name, broken
        )

    def test_resets_to_default_when_no_discord_account(self):
        self._break_avatar()
        out = io.StringIO()
        call_command('repair_profile_avatars', stdout=out)

        self.assertEqual(
            Profile.objects.get(pk=self.user.profile.pk).image.name,
            DEFAULT_PROFILE_IMAGE,
        )

    def test_healthy_profiles_are_left_alone(self):
        self._write_avatar(User.objects.get(pk=self.user.pk))
        good = Profile.objects.get(pk=self.user.profile.pk).image.name
        out = io.StringIO()
        call_command('repair_profile_avatars', stdout=out)

        self.assertIn('missing 0', out.getvalue())
        self.assertEqual(
            Profile.objects.get(pk=self.user.profile.pk).image.name, good
        )


class EditGuildClaimTests(_NoLoginSignalMixin, TestCase):
    """/help links to the manage page for any guild where the invoker has Manage Guild,
    including guilds we never recorded (the bot can be added without the /databot/added/
    redirect completing) and guilds sync_bot_guilds created with no moderators. Opening
    that link re-runs Discord's verification instead of dead-ending on 404/403."""

    GUILD_ID = "100000000000000777"

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="claimer", password="pw")
        self.profile = self.user.profile
        self.profile.group = "P"          # plain player, not a site admin
        self.profile.player_onboard = True
        self.profile.save()
        self.client.force_login(self.user)
        self.url = reverse("edit-guild", args=[self.GUILD_ID])

    def _visit(self, in_guild=True, can_manage=True, method="get", data=None):
        """GET/POST the manage page with Discord's answers stubbed. Patches on the views
        module, which imports these by name."""
        with mock.patch("the_gatehouse.views.bot_in_guild", return_value=in_guild) as bot, \
             mock.patch("the_gatehouse.views.user_can_manage_guild",
                        return_value=can_manage) as manage, \
             mock.patch("the_gatehouse.views._get_guild",
                        return_value={"name": "Claimed Guild", "icon": "abc",
                                      "description": "desc"}), \
             mock.patch("the_gatehouse.views.register_guild_commands",
                        return_value=True) as register, \
             mock.patch("the_gatehouse.views.get_guild_roles", return_value=[]), \
             mock.patch("the_gatehouse.views.get_guild_forum_channels", return_value=[]), \
             mock.patch("the_gatehouse.views.get_forum_channel_info", return_value=None):
            response = getattr(self.client, method)(self.url, data or {})
        return response, bot, manage, register

    # ── missing row: the 404 this fixes ──
    def test_missing_guild_is_created_and_claimed_when_discord_agrees(self):
        response, _, _, register = self._visit()
        self.assertEqual(response.status_code, 200)
        guild = DiscordGuild.objects.get(guild_id=self.GUILD_ID)
        self.assertEqual(guild.actual_name, "Claimed Guild")
        self.assertTrue(guild.bot_member)
        self.assertTrue(guild.guild_moderators.filter(pk=self.profile.pk).exists())
        self.assertTrue(self.profile.guilds.filter(pk=guild.pk).exists())
        # A newly tracked guild needs its (empty) command set pushed.
        register.assert_called_once()

    def test_missing_guild_without_manage_permission_creates_nothing(self):
        response, _, _, register = self._visit(can_manage=False)
        self.assertRedirects(response, reverse("manage-guilds"))
        self.assertFalse(DiscordGuild.objects.filter(guild_id=self.GUILD_ID).exists())
        register.assert_not_called()

    def test_missing_guild_the_bot_is_not_in_creates_nothing(self):
        """A guild id in the URL is whatever the user typed; being able to manage a
        server we have no bot in must not conjure a row."""
        response, _, manage, _ = self._visit(in_guild=False)
        self.assertRedirects(response, reverse("manage-guilds"))
        self.assertFalse(DiscordGuild.objects.filter(guild_id=self.GUILD_ID).exists())
        # bot_in_guild short-circuits before the expensive member fetch.
        manage.assert_not_called()

    def test_uncertainty_is_not_treated_as_permission(self):
        """bot_in_guild returns None when it can't tell — never record on a maybe."""
        response, _, _, _ = self._visit(in_guild=None)
        self.assertRedirects(response, reverse("manage-guilds"))
        self.assertFalse(DiscordGuild.objects.filter(guild_id=self.GUILD_ID).exists())

    def test_a_non_snowflake_id_never_reaches_discord(self):
        with mock.patch("the_gatehouse.views.bot_in_guild") as bot:
            response = self.client.get(reverse("edit-guild", args=["not-an-id"]))
        self.assertRedirects(response, reverse("manage-guilds"))
        bot.assert_not_called()

    # ── existing row with no moderators: the 403 this fixes ──
    def test_sync_created_guild_is_claimable_by_its_manager(self):
        """sync_bot_guilds creates rows with no moderators, which used to lock the
        server's own owner out of the page /help sent them to."""
        DiscordGuild.objects.create(guild_id=self.GUILD_ID, name="Synced", bot_member=True)
        response, _, _, register = self._visit()
        self.assertEqual(response.status_code, 200)
        guild = DiscordGuild.objects.get(guild_id=self.GUILD_ID)
        self.assertTrue(guild.guild_moderators.filter(pk=self.profile.pk).exists())
        # The row already existed, so its commands are already registered.
        register.assert_not_called()

    def test_existing_guild_still_403s_a_user_discord_rejects(self):
        DiscordGuild.objects.create(guild_id=self.GUILD_ID, name="Someone Else's",
                                    bot_member=True)
        response, _, _, _ = self._visit(can_manage=False)
        self.assertEqual(response.status_code, 403)

    # ── the POST guard ──
    def test_post_never_claims(self):
        DiscordGuild.objects.create(guild_id=self.GUILD_ID, name="Someone Else's",
                                    bot_member=True)
        response, bot, manage, _ = self._visit(method="post",
                                               data={"enabled_commands": []})
        self.assertEqual(response.status_code, 403)
        bot.assert_not_called()
        manage.assert_not_called()

    # ── the happy path must stay free of Discord calls ──
    def test_an_existing_moderator_triggers_no_discord_permission_calls(self):
        guild = DiscordGuild.objects.create(guild_id=self.GUILD_ID, name="Mine",
                                            bot_member=True)
        guild.guild_moderators.add(self.profile)
        response, bot, manage, _ = self._visit()
        self.assertEqual(response.status_code, 200)
        bot.assert_not_called()
        manage.assert_not_called()


# ── /law embed author breadcrumb ─────────────────────────────────────────────

class LawAuthorBreadcrumbTests(TestCase):
    """The /law embed's author line: the group's prime-law title, plus the law's
    IMMEDIATE parent when that parent isn't the prime law itself.

    Only the direct parent is ever named, at any depth — the line used to carry
    the two nearest ancestors with a " ... " marking elided levels, which made it
    long and gave it a different shape at different depths."""

    def setUp(self):
        self.language = Language.objects.create(name="English", code="en")
        self.group = LawGroup.objects.create(
            title="Vagabond", abbreviation="V", public=True, slug="vagabond")
        # prime -> Relationships -> Improving Relationships -> Aid -> Deep
        self.prime = self._law("Vagabond", parent=None, prime_law=True)
        self.l1 = self._law("Relationships", parent=self.prime)
        self.l2 = self._law("Improving Relationships", parent=self.l1)
        self.l3 = self._law("Aid", parent=self.l2)
        self.l4 = self._law("Deep", parent=self.l3)

    def _law(self, title, parent, prime_law=False, group=None):
        return Law.objects.create(
            group=group or self.group, language=self.language, title=title,
            parent=parent, prime_law=prime_law)

    # Distinct from None, so a test can pass prime=None (a group with no prime
    # law) without it being read as "use the default".
    UNSET = object()

    def crumb(self, law, prime=UNSET, group=None):
        prime = self.prime if prime is self.UNSET else prime
        return ds._law_author_breadcrumb(law, prime, group or self.group)

    def test_prime_law_itself_shows_only_the_prime(self):
        self.assertEqual(self.crumb(self.prime), "Vagabond")

    def test_direct_child_of_prime_shows_only_the_prime(self):
        """The law's own title is already the embed title, so naming the prime as
        its parent too would just repeat it."""
        self.assertEqual(self.crumb(self.l1), "Vagabond")

    def test_grandchild_names_its_parent(self):
        self.assertEqual(self.crumb(self.l2), "Vagabond - Relationships")

    def test_deeper_law_names_only_its_direct_parent(self):
        """Previously "Vagabond - Relationships - Improving Relationships" — the
        grandparent is no longer carried."""
        self.assertEqual(self.crumb(self.l3), "Vagabond - Improving Relationships")

    def test_deepest_law_drops_the_ellipsis(self):
        """Previously "Vagabond ... Improving Relationships - Aid"."""
        self.assertEqual(self.crumb(self.l4), "Vagabond - Aid")

    def test_no_depth_produces_an_ellipsis(self):
        for law in (self.prime, self.l1, self.l2, self.l3, self.l4):
            with self.subTest(law=law.title):
                self.assertNotIn("...", self.crumb(law))

    # ── edges ────────────────────────────────────────────────────────────────
    def test_orphaned_chain_still_names_the_direct_parent(self):
        """A law whose ancestors never reach the prime. The old code flagged this
        with the " ... "; now the parent is simply named."""
        orphan_parent = self._law("Orphan A", parent=None)
        orphan = self._law("Orphan B", parent=orphan_parent)
        self.assertEqual(self.crumb(orphan), "Vagabond - Orphan A")

    def test_group_title_is_the_base_when_there_is_no_prime_law(self):
        other = LawGroup.objects.create(
            title="Marquise", abbreviation="M", public=True, slug="marquise")
        parent = self._law("Cats", parent=None, group=other)
        child = self._law("Wood", parent=parent, group=other)
        self.assertEqual(self.crumb(child, prime=None, group=other),
                         "Marquise - Cats")

    def test_blank_parent_title_degrades_to_the_base(self):
        """Rather than emitting a dangling " - "."""
        blank = self._law("", parent=self.prime)
        child = self._law("Child", parent=blank)
        self.assertEqual(self.crumb(child), "Vagabond")

    def test_embed_author_uses_the_breadcrumb(self):
        """The whole point of the function — confirm it reaches the embed."""
        embed = ds.build_law_embed(self.l3)
        self.assertEqual(embed["author"]["name"],
                         "Vagabond - Improving Relationships")


class LFGThreadCleanupTaskTests(TestCase):
    """cleanup_stale_lfg_threads: two windows, one for recorded threads and one
    for abandoned ones, both keyed on last_activity."""

    def _thread(self, thread_id, days_idle, **kw):
        """Create a thread idle for `days_idle`. Pass every field via **kw:
        backdating has to be the FINAL write, because LFGThread.save() bumps
        last_activity on any save that doesn't name it."""
        t = LFGThread.objects.create(thread_id=thread_id, **kw)
        t.last_activity = timezone.now() - timedelta(days=days_idle)
        t.save(update_fields=["last_activity"])
        return t

    # ── policy ──
    def test_recorded_thread_past_the_grace_period_is_deleted(self):
        from the_gatehouse import tasks
        self._thread("900000000000000001", 45,
                     status=LFGThread.Status.RECORDED)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 1)
        self.assertFalse(LFGThread.objects.exists())

    def test_freshly_recorded_thread_survives(self):
        """/record's duplicate guard is `if thread.game_id` — deleting the row
        early makes the next /record offer a blank form."""
        from the_gatehouse import tasks
        self._thread("900000000000000002", 5,
                     status=LFGThread.Status.RECORDED)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 0)
        self.assertEqual(LFGThread.objects.count(), 1)

    def test_open_thread_inside_the_long_window_survives(self):
        from the_gatehouse import tasks
        self._thread("900000000000000003", 90)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 0)
        self.assertEqual(LFGThread.objects.count(), 1)

    def test_abandoned_open_thread_is_deleted(self):
        from the_gatehouse import tasks
        self._thread("900000000000000004", 200)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 1)
        self.assertFalse(LFGThread.objects.exists())

    def test_save_progress_draft_game_is_not_treated_as_recorded(self):
        """game set but status OPEN is a game still being written. Keying the
        short window on game_id instead of status would delete it."""
        from the_gatehouse import tasks
        game = Game.objects.create()
        self._thread("900000000000000005", 45, game=game,
                     status=LFGThread.Status.OPEN)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 0)
        self.assertEqual(LFGThread.objects.count(), 1)

    def test_series_threads_follow_the_same_idle_rule(self):
        """Tournament group threads get no exemption."""
        from the_gatehouse import tasks
        designer = Profile.objects.create(discord="seriesdesigner",
                                          discord_id="960000000000000001")
        tournament = Tournament.objects.create(name="Cleanup Cup",
                                               designer=designer)
        stage = Stage.objects.create(tournament=tournament, name="Stage 1",
                                     order=1)
        round_ = Round.objects.create(stage=stage, round_number=1)
        series = MatchSeries.objects.create(round=round_, number_of_games=1)
        self._thread("900000000000000006", 200, series=series)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 1)
        self.assertFalse(LFGThread.objects.exists())

    def test_cancelled_thread_uses_the_short_window(self):
        from the_gatehouse import tasks
        self._thread("900000000000000007", 45,
                     status=LFGThread.Status.CANCELLED)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 1)

    def test_nothing_to_delete_returns_zero(self):
        from the_gatehouse import tasks
        self.assertEqual(tasks.cleanup_stale_lfg_threads(), 0)

    # ── cascade / survival ──
    def test_children_go_with_the_thread(self):
        from the_gatehouse import tasks
        thread = self._thread("900000000000000008", 200)
        designer = Profile.objects.create(discord="cascadedesigner",
                                          discord_id="960000000000000002")
        faction = Faction.objects.create(title="Cascade Cats", animal="Cat",
                                         designer=designer, slug="cascade-cats")
        LFGRoll.objects.create(thread=thread, kind="Faction", slug="cascade-cats")
        draft = LFGDraft.objects.create(thread=thread, players=4)
        LFGDraftPick.objects.create(draft=draft, faction=faction, order=1)
        LFGSeat.objects.create(thread=thread, seat_number=1)

        tasks.cleanup_stale_lfg_threads()

        self.assertFalse(LFGRoll.objects.exists())
        self.assertFalse(LFGDraft.objects.exists())
        self.assertFalse(LFGDraftPick.objects.exists())
        self.assertFalse(LFGSeat.objects.exists())

    def test_the_recorded_game_survives_its_thread(self):
        """LFGThread.game is SET_NULL, so only the scaffolding is discarded."""
        from the_gatehouse import tasks
        game = Game.objects.create()
        self._thread("900000000000000009", 45, game=game,
                     status=LFGThread.Status.RECORDED)
        tasks.cleanup_stale_lfg_threads()
        self.assertTrue(Game.objects.filter(pk=game.pk).exists())

    # ── safety ──
    def test_dry_run_reports_without_deleting(self):
        from the_gatehouse import tasks
        self._thread("900000000000000010", 200)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(dry_run=True), 1)
        self.assertEqual(LFGThread.objects.count(), 1)

    def test_limit_caps_a_run(self):
        from the_gatehouse import tasks
        for i in range(5):
            self._thread(f"90000000000000002{i}", 200)
        self.assertEqual(tasks.cleanup_stale_lfg_threads(limit=2), 2)
        self.assertEqual(LFGThread.objects.count(), 3)

    def test_limit_takes_the_oldest_first(self):
        from the_gatehouse import tasks
        oldest = self._thread("900000000000000031", 400)
        self._thread("900000000000000032", 300)
        self._thread("900000000000000033", 200)
        tasks.cleanup_stale_lfg_threads(limit=1)
        self.assertFalse(LFGThread.objects.filter(pk=oldest.pk).exists())
        self.assertEqual(LFGThread.objects.count(), 2)

    def test_deletion_spans_multiple_chunks(self):
        from the_gatehouse import tasks
        for i in range(5):
            self._thread(f"90000000000000004{i}", 200)
        with mock.patch.object(tasks, "_LFG_DELETE_CHUNK", 2):
            self.assertEqual(tasks.cleanup_stale_lfg_threads(), 5)
        self.assertFalse(LFGThread.objects.exists())

    def test_the_two_windows_are_independent(self):
        from the_gatehouse import tasks
        self._thread("900000000000000051", 45,
                     status=LFGThread.Status.RECORDED)
        self._thread("900000000000000052", 45)
        self.assertEqual(
            tasks.cleanup_stale_lfg_threads(recorded_after_days=0,
                                            stale_after_days=9999), 1)
        self.assertEqual(LFGThread.objects.count(), 1)


class LFGThreadLastActivityTests(TestCase):
    """LFGThread.save() keeps last_activity current even on the narrow
    update_fields writes that dominate this model's call sites."""

    THREAD_ID = "910000000000000001"

    def setUp(self):
        self.thread = LFGThread.objects.create(thread_id=self.THREAD_ID)
        self.stale = timezone.now() - timedelta(days=200)
        self.thread.last_activity = self.stale
        self.thread.save(update_fields=["last_activity"])

    def _bumped(self):
        self.thread.refresh_from_db()
        return self.thread.last_activity > self.stale

    def test_create_sets_a_fresh_timestamp(self):
        """Covers the _state.adding guard: an insert must not be widened."""
        fresh = LFGThread.objects.create(thread_id="910000000000000099")
        self.assertGreater(fresh.last_activity,
                           timezone.now() - timedelta(minutes=1))

    def test_narrow_nickname_save_bumps_last_activity(self):
        self.thread.nickname = "Renamed"
        self.thread.save(update_fields=["nickname"])
        self.assertTrue(self._bumped())

    def test_narrow_seating_save_bumps_last_activity(self):
        self.thread.seating_set = True
        self.thread.save(update_fields=["seating_set"])
        self.assertTrue(self._bumped())

    def test_narrow_game_status_save_bumps_last_activity(self):
        """The write the_warroom's record view makes."""
        self.thread.status = LFGThread.Status.RECORDED
        self.thread.save(update_fields=["game", "status"])
        self.assertTrue(self._bumped())

    def test_an_explicit_last_activity_write_is_respected(self):
        """Every fixture in these tests depends on this escape hatch."""
        pinned = timezone.now() - timedelta(days=300)
        self.thread.last_activity = pinned
        self.thread.save(update_fields=["last_activity"])
        self.thread.refresh_from_db()
        self.assertAlmostEqual(self.thread.last_activity, pinned,
                               delta=timedelta(seconds=1))

    def test_capturing_a_roll_bumps_the_thread(self):
        """Rolls are children, so nothing else would touch the parent."""
        from the_gatehouse import tasks
        designer = Profile.objects.create(discord="bumpdesigner",
                                          discord_id="960000000000000003")
        Faction.objects.create(title="Bump Birds", animal="Bird",
                               designer=designer, slug="bump-birds")
        tasks.record_lfg_components_task(
            self.THREAD_ID, [{"kind": "Faction", "slug": "bump-birds"}],
            source="random")
        self.assertTrue(self._bumped())


class CreateMatchThreadsTaskTests(_NoLoginSignalMixin, TestCase):
    """The bulk thread task: one forum thread per un-threaded series, players pinged,
    thread linked back to the group, outcome reported as a UserNotification."""

    FORUM = [{"id": "300000000000000033", "name": "matches"}]

    def setUp(self):
        super().setUp()
        self.guild = DiscordGuild.objects.create(guild_id="920100", name="Task Guild",
                                                 bot_member=True)
        self.actor = Profile.objects.create(discord="actor", discord_id="90")
        self.player = Profile.objects.create(discord="p1", discord_id="91")
        self.unlinked = Profile.objects.create(discord="p2", discord_id=None)

        self.tournament = Tournament.objects.create(
            name="Task Cup", guild=self.guild,
            game_threads_channel=self.FORUM[0]["id"])
        self.stage = Stage.objects.create(tournament=self.tournament, name="S1", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name="Group A")
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)

        tp = TournamentPlayer.objects.create(tournament=self.tournament,
                                             profile=self.player)
        self.group.tournament_players.add(tp)

    def _run(self, thread_id="777001", forum=None, side_effect=None):
        """Runs the task with Discord stubbed. The task calls the _result variant, which
        returns (thread_id, retry_after, status) -- the status lets it tell a 400 (e.g. a
        forum that requires a tag) from a transient failure. Sleeps are patched out so
        pacing doesn't slow the suite."""
        from the_gatehouse import tasks
        return_value = (thread_id, None, None)
        with mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                        return_value=self.FORUM if forum is None else forum), \
             mock.patch("the_gatehouse.tasks.time.sleep") as self.sleep, \
             mock.patch("the_gatehouse.services.discordservice.create_forum_thread_result",
                        **({"side_effect": side_effect} if side_effect
                           else {"return_value": return_value})) as create:
            tasks.create_match_threads_task(
                self.round.id, self.actor.id, self.tournament.id)
        return create

    def test_creates_a_thread_and_links_it(self):
        create = self._run()
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[0], self.FORUM[0]["id"])
        self.assertEqual(args[1], "Group A")            # titled with the group name
        self.assertIn("<@91>", kwargs["content"])        # player pinged

        self.group.refresh_from_db()
        self.assertEqual(
            self.group.discord_thread,
            f"https://discord.com/channels/{self.guild.guild_id}/777001")

        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 game thread created", note.message)
        self.assertEqual(note.related_url, self.round.get_matches_url())

    def test_a_group_that_already_has_a_thread_is_skipped(self):
        self.group.discord_thread = f"https://discord.com/channels/{self.guild.guild_id}/1"
        self.group.save()
        create = self._run()
        create.assert_not_called()

    def test_running_twice_does_not_duplicate(self):
        self._run()
        create = self._run(thread_id="777002")
        create.assert_not_called()
        self.group.refresh_from_db()
        # Still the FIRST thread -- link_group_thread's compare-and-swap held.
        self.assertTrue(self.group.discord_thread.endswith("/777001"))

    def test_players_without_discord_do_not_block_the_thread(self):
        tp = TournamentPlayer.objects.create(tournament=self.tournament,
                                             profile=self.unlinked)
        self.group.tournament_players.add(tp)
        create = self._run()
        create.assert_called_once()
        content = create.call_args.kwargs["content"]
        self.assertIn("<@91>", content)
        self.assertNotIn("None", content)

    def test_a_failed_creation_writes_no_url_and_reports_it(self):
        create = self._run(thread_id=None)
        create.assert_called_once()
        self.group.refresh_from_db()
        self.assertEqual(self.group.discord_thread, "")
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertEqual(note.message_type, MessageChoices.WARNING)

    def test_requests_are_spaced_out(self):
        """Back-to-back creation is what trips the limit; pause between calls."""
        for n in (2, 3):
            group = PlayerGroup.objects.create(
                round=self.round, group_number=n, name=f"Group {n}")
            MatchSeries.objects.create(
                round=self.round, player_group=group, number_of_games=1)
        create = self._run()
        self.assertEqual(create.call_count, 3)
        # One pause per call after the first -- never before the first.
        from the_gatehouse import tasks
        self.assertEqual(
            self.sleep.call_args_list,
            [mock.call(tasks._THREAD_CREATE_INTERVAL)] * 2)

    def test_a_rate_limit_is_retried_not_counted_as_failed(self):
        """A 429 means "too fast", not "impossible" -- wait and retry the SAME group,
        instead of burning it and racing on to the next."""
        create = self._run(side_effect=[(None, 2.5, 429), ("777009", None, None)])
        self.assertEqual(create.call_count, 2)
        self.sleep.assert_any_call(2.5)          # honoured Discord's retry_after
        self.group.refresh_from_db()
        self.assertTrue(self.group.discord_thread.endswith("/777009"))
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 game thread created", note.message)
        self.assertNotIn("failed", note.message)

    def test_a_long_retry_after_is_capped(self):
        """Discord can ask for hundreds of seconds; don't park the worker that long."""
        from the_gatehouse import tasks
        create = self._run(side_effect=[(None, 900.0, 429), ("777010", None, None)])
        self.sleep.assert_any_call(tasks._THREAD_CREATE_MAX_BACKOFF)
        for call in self.sleep.call_args_list:
            self.assertLessEqual(call.args[0], tasks._THREAD_CREATE_MAX_BACKOFF)

    def test_a_persistent_rate_limit_explains_itself(self):
        """Still limited after the retry: report it as retryable, not a mystery."""
        create = self._run(side_effect=[(None, 1.0, 429), (None, 1.0, 429)])
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertIn("rate limited", note.message)
        self.assertEqual(note.message_type, MessageChoices.WARNING)

    def test_a_plain_failure_does_not_mention_rate_limiting(self):
        create = self._run(thread_id=None)
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertNotIn("rate limited", note.message)

    def test_a_stale_channel_aborts_with_a_warning(self):
        """The tournament moved guilds between the click and the task running."""
        other = DiscordGuild.objects.create(guild_id="920999", name="Other",
                                            bot_member=True)
        Tournament.objects.filter(pk=self.tournament.pk).update(guild=other)
        create = self._run(forum=[])     # the new guild has no such forum
        create.assert_not_called()
        note = UserNotification.objects.get(profile=self.actor)
        self.assertEqual(note.message_type, MessageChoices.WARNING)
        self.group.refresh_from_db()
        self.assertEqual(self.group.discord_thread, "")

    # --- forum tags -------------------------------------------------------------
    # A forum with "require tag when posting" rejects EVERY post without applied_tags.
    # That is what made this feature fail in production while /lfg (which does pass a
    # tag) kept working in the very same channel.

    def test_the_configured_tag_is_sent(self):
        self.tournament.game_threads_tag = "55501"
        self.tournament.save()
        create = self._run()
        self.assertEqual(create.call_args.kwargs["tag_id"], "55501")

    def test_no_tag_configured_sends_none(self):
        create = self._run()
        self.assertIsNone(create.call_args.kwargs["tag_id"])

    def test_the_rate_limit_retry_also_carries_the_tag(self):
        """The retry must not drop the tag, or a throttled item fails a second time for
        an entirely unrelated reason."""
        self.tournament.game_threads_tag = "55501"
        self.tournament.save()
        create = self._run(side_effect=[(None, 1.0, 429), ("777011", None, None)])
        self.assertEqual(create.call_count, 2)
        for call in create.call_args_list:
            self.assertEqual(call.kwargs["tag_id"], "55501")

    def test_a_400_with_no_tag_set_names_the_likely_cause(self):
        """The production symptom: retrying is pointless until a tag is chosen, so say
        so rather than inviting an identical failure."""
        create = self._run(side_effect=[(None, None, 400)])
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertIn("may require a tag", note.message)
        self.assertNotIn("rate limited", note.message)
        self.assertEqual(note.message_type, MessageChoices.WARNING)

    def test_a_400_with_a_tag_already_set_does_not_blame_the_tag(self):
        """A tag IS configured, so the 400 is something else -- don't misdirect."""
        self.tournament.game_threads_tag = "55501"
        self.tournament.save()
        self._run(side_effect=[(None, None, 400)])
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertNotIn("may require a tag", note.message)

    def test_a_non_400_failure_does_not_blame_the_tag(self):
        self._run(side_effect=[(None, None, 403)])
        note = UserNotification.objects.get(profile=self.actor)
        self.assertIn("1 failed", note.message)
        self.assertNotIn("may require a tag", note.message)


class CreateForumThreadResultTests(TestCase):
    """create_forum_thread_result: the payload it builds and what it reports back."""

    CHANNEL = "300000000000000033"

    def _post(self, status=None, json_body=None, text="", exc=None):
        """Patch requests.post with a response that raise_for_status()es on `status`."""
        import requests
        resp = mock.Mock()
        resp.status_code = status
        resp.text = text
        resp.json.return_value = json_body or {}
        resp.headers = {}
        if status and status >= 400:
            err = requests.HTTPError(f"{status} Client Error", response=resp)
            resp.raise_for_status.side_effect = err
        else:
            resp.raise_for_status.return_value = None
        return mock.patch("the_gatehouse.services.discordservice.requests.post",
                          return_value=resp)

    def test_a_tag_becomes_applied_tags(self):
        from the_gatehouse.services.discordservice import create_forum_thread_result
        with self._post(json_body={"id": "77"}) as post:
            thread_id, retry_after, status = create_forum_thread_result(
                self.CHANNEL, "Group A", content="hi", tag_id="55501")
        self.assertEqual(thread_id, "77")
        self.assertIsNone(status)
        self.assertEqual(post.call_args.kwargs["json"]["applied_tags"], ["55501"])

    def test_no_tag_omits_applied_tags(self):
        from the_gatehouse.services.discordservice import create_forum_thread_result
        with self._post(json_body={"id": "77"}) as post:
            create_forum_thread_result(self.CHANNEL, "Group A", content="hi")
        self.assertNotIn("applied_tags", post.call_args.kwargs["json"])

    def test_a_400_reports_its_status(self):
        from the_gatehouse.services.discordservice import create_forum_thread_result
        with self._post(status=400, json_body={"code": 40067}):
            thread_id, retry_after, status = create_forum_thread_result(
                self.CHANNEL, "Group A", content="hi")
        self.assertIsNone(thread_id)
        self.assertIsNone(retry_after)
        self.assertEqual(status, 400)

    def test_a_failure_logs_discord_status_and_body(self):
        """str(e) alone hides the JSON error code naming the real cause."""
        from the_gatehouse.services.discordservice import create_forum_thread_result
        body = '{"message": "A tag is required...", "code": 40067}'
        with self._post(status=400, json_body={"code": 40067}, text=body):
            with self.assertLogs("the_gatehouse.services.discordservice",
                                 level="ERROR") as logs:
                create_forum_thread_result(self.CHANNEL, "Group A", content="hi")
        logged = "\n".join(logs.output)
        self.assertIn("400", logged)
        self.assertIn("40067", logged)

    def test_the_detailed_wrapper_still_returns_a_pair(self):
        """rename_channel shares the (result, retry_after) shape; /lfg destructures it."""
        from the_gatehouse.services.discordservice import create_forum_thread_detailed
        with self._post(json_body={"id": "77"}):
            result = create_forum_thread_detailed(self.CHANNEL, "Group A", content="hi")
        self.assertEqual(result, ("77", None))


class TournamentGuildChannelsFormTagTests(TestCase):
    """Tag validation on the series-channels form. Catches a tag-required forum at SAVE
    time, so the moderator fixes it here rather than discovering it as a round of failed
    threads later."""

    FORUM = [{"id": "300000000000000033", "name": "matches"}]
    TEXT = [{"id": "200000000000000011", "name": "general"}]
    TAGS = [{"id": "55501", "name": "Match"}, {"id": "55502", "name": "Final"}]

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="930100", name="Form Guild",
                                                 bot_member=True)
        self.tournament = Tournament.objects.create(name="Form Cup", guild=self.guild)

    # Sentinel: `info=None` is a meaningful value (Discord unreachable), so it can't
    # double as "not supplied".
    _UNSET = object()

    def _form(self, data, requires_tag=False, info=_UNSET):
        """Bind the form with Discord's channel lists and forum info stubbed."""
        from the_gatehouse.forms import TournamentGuildChannelsForm
        finfo = ({"is_forum": True, "requires_tag": requires_tag, "tags": self.TAGS}
                 if info is self._UNSET else info)
        with mock.patch("the_gatehouse.services.discordservice.get_guild_text_channels",
                        return_value=self.TEXT), \
             mock.patch("the_gatehouse.services.discordservice.get_guild_forum_channels",
                        return_value=self.FORUM), \
             mock.patch("the_gatehouse.services.discordservice.get_forum_channel_info",
                        return_value=finfo):
            form = TournamentGuildChannelsForm(
                data, instance=self.tournament, guild=self.guild)
            form.is_valid()
        return form

    def test_a_tag_required_forum_rejects_a_blank_tag(self):
        form = self._form({'game_threads_channel': self.FORUM[0]['id'],
                           'game_threads_tag': ''}, requires_tag=True)
        self.assertIn('game_threads_tag', form.errors)

    def test_a_tag_required_forum_accepts_a_valid_tag(self):
        form = self._form({'game_threads_channel': self.FORUM[0]['id'],
                           'game_threads_tag': '55501'}, requires_tag=True)
        self.assertEqual(form.errors, {})
        self.assertEqual(form.cleaned_data['game_threads_tag'], '55501')

    def test_a_tag_from_another_forum_is_rejected(self):
        form = self._form({'game_threads_channel': self.FORUM[0]['id'],
                           'game_threads_tag': '99999'})
        self.assertIn('game_threads_tag', form.errors)

    def test_an_optional_tag_forum_saves_without_one(self):
        form = self._form({'game_threads_channel': self.FORUM[0]['id'],
                           'game_threads_tag': ''})
        self.assertEqual(form.errors, {})
        self.assertIsNone(form.cleaned_data['game_threads_tag'])

    def test_an_unreachable_discord_still_saves(self):
        """An API outage must not lock the settings page (same rule as GuildLFGRoleForm)."""
        form = self._form({'game_threads_channel': self.FORUM[0]['id'],
                           'game_threads_tag': ''}, info=None)
        self.assertEqual(form.errors, {})

    def test_a_tag_without_a_forum_is_dropped(self):
        """No forum means the tag has nothing to belong to; don't store an orphan that
        would silently reattach to a different forum later."""
        form = self._form({'game_threads_channel': '', 'game_threads_tag': '55501'})
        self.assertEqual(form.errors, {})
        self.assertIsNone(form.cleaned_data['game_threads_tag'])

    def test_repointing_the_guild_clears_the_tag(self):
        """The tag belongs to the OLD guild's forum, so it goes with the channel."""
        other = DiscordGuild.objects.create(guild_id="930999", name="Other",
                                            bot_member=True)
        self.tournament.game_threads_channel = self.FORUM[0]['id']
        self.tournament.game_threads_tag = "55501"
        self.tournament.save()

        self.tournament.guild = other
        self.tournament.save()

        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.game_threads_channel)
        self.assertIsNone(self.tournament.game_threads_tag)
