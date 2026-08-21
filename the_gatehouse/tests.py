import json
from unittest import mock
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from kombu.exceptions import OperationalError as KombuOperationalError
from the_warroom.models import (
    Effort, Game, Match, MatchSeat, MatchSeries, PlayerGroup, Round, Stage,
    StageParticipant, Tournament, TournamentPlayer, CompetitionStatus,
)
from the_gatehouse.tasks import update_post_status
from the_keep.models import StatusChoices, Faction
from the_gatehouse.models import DiscordGuild, GuildLFGRole, Profile
from the_gatehouse import views
from the_gatehouse.signals import user_logged_in_handler
from the_gatehouse.services import discord_commands as dc
from the_gatehouse.services.time_parsing import (
    NEED_TIMEZONE, parse_user_datetime, format_discord_timestamp,
    search_timezones, valid_timezone,
)
from the_gatehouse.services.discordservice import build_upcoming_embed
from the_gatehouse.services import discordservice as ds
from the_gatehouse import discord_interactions as di

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
    """The parser accepts absolute date+time and epoch forms, and refuses
    anything it would have to guess at."""

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

    def test_time_without_date_rejected(self):
        when, err = parse_user_datetime("8pm", TZ, now=NOW)
        self.assertIsNone(when)
        self.assertIn("date", err)

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


class ScheduleFixtureMixin:
    """A tournament with one stage, round, group, series and match, plus a guild
    and a seated player."""

    def build(self, recording_access=Tournament.RecordingAccessTypes.SCHEDULED,
              thread_id="555000111", group_name="Group A"):
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
        body = self.assertEphemeral(di._handle_schedule_command(self._data()))
        self.assertIn("timezone", body["data"]["content"].lower())

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
        self.assertIn("can't set the time", body["data"]["content"])

    def test_unknown_discord_user_rejected(self):
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(author="99999999")))
        self.assertIn("account", body["data"]["content"].lower())

    def test_no_match_for_thread_rejected(self):
        body = self.assertEphemeral(
            di._handle_schedule_command(self._data(channel="123123123")))
        self.assertIn("couldn't find", body["data"]["content"].lower())

    def test_used_in_plain_channel_says_to_use_a_thread(self):
        data = self._data(channel="123123123")
        data["_channel_type"] = 0  # GUILD_TEXT, not a thread
        body = self.assertEphemeral(di._handle_schedule_command(data))
        self.assertIn("thread", body["data"]["content"])

    def test_thread_type_still_reports_missing_link(self):
        data = self._data(channel="123123123")
        data["_channel_type"] = 11  # PUBLIC_THREAD
        body = self.assertEphemeral(di._handle_schedule_command(data))
        self.assertIn("couldn't find", body["data"]["content"].lower())

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

    def setUp(self):
        self.build()
        self.when = (timezone.now() + timedelta(days=10)).replace(microsecond=0)
        self.ts = int(self.when.timestamp())

    UNSET = object()

    def _payload(self, match_id=UNSET, ts=UNSET, owner=UNSET, guild=UNSET):
        return {
            "data": {"custom_id": di.encode_custom_id(
                "schedule_confirm",
                self.match.id if match_id is self.UNSET else match_id,
                self.ts if ts is self.UNSET else ts,
                self.player.discord_id if owner is self.UNSET else owner,
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
        self.assertIn("can't set the time", body["data"]["content"])
        self.assertNotIn("Remove the scheduled time", body["data"]["content"])

    def test_clear_works_without_a_profile_timezone(self):
        """Clearing needs no timezone, so it must not hit the NEED_TIMEZONE prompt."""
        self._schedule()
        self.assertFalse(self.player.timezone)
        body = self._body(di._handle_schedule_command(self._data()))
        self.assertIn("Remove the scheduled time", body["data"]["content"])

    def test_no_match_for_thread_still_errors(self):
        body = self._body(di._handle_schedule_command(self._data(channel="123123123")))
        self.assertIn("couldn't find", body["data"]["content"].lower())


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

    def _payload(self, players_value):
        return {
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
