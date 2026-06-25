from django.contrib.auth.models import User
from django.test import TestCase

from the_gatehouse.models import DiscordGuild, Profile
from the_warroom.forms import GameCreateForm
from the_warroom.models import Round, Stage, Tournament
from the_warroom.views import user_can_record_in_round


class GuildRecordingAccessTests(TestCase):
    """The GUILD recording_access tier lets any member of the linked guild
    record games, even if they are not a stage participant."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="1001", name="Test Guild")

        self.member = Profile.objects.create(discord="member")
        self.member.guilds.add(self.guild)

        self.outsider = Profile.objects.create(discord="outsider")

        self.tournament = Tournament.objects.create(
            name="Guild Tournament",
            guild=self.guild,
            recording_access=Tournament.RecordingAccessTypes.GUILD,
        )

    def test_member_can_record_under_guild_access(self):
        self.assertTrue(self.tournament.guild_members_can_record(self.member))

    def test_non_member_cannot_record(self):
        self.assertFalse(self.tournament.guild_members_can_record(self.outsider))

    def test_lower_tier_denies_guild_members(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.REGISTERED
        self.tournament.save()
        self.assertFalse(self.tournament.guild_members_can_record(self.member))

    def test_cleared_guild_denies_members(self):
        self.tournament.guild = None
        self.tournament.save()
        self.assertFalse(self.tournament.guild_members_can_record(self.member))

    def test_helpers_include_guild_tier(self):
        self.assertTrue(self.tournament.players_can_record_matches())
        self.assertTrue(self.tournament.players_can_record_standalone())


class GameCreateFormGuildRoundTests(TestCase):
    """A guild-only user (not a stage participant) sees a round only when the
    tournament grants GUILD recording access."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="2002", name="Round Guild")

        self.user = User.objects.create_user(username="guildonly", password="x")
        self.user.profile.guilds.add(self.guild)

        self.tournament = Tournament.objects.create(
            name="Round Tournament",
            guild=self.guild,
            recording_access=Tournament.RecordingAccessTypes.GUILD,
            is_active=True,
        )
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1, is_active=True
        )
        self.round = Round.objects.create(
            stage=self.stage, round_number=1, is_active=True
        )

    def _round_ids(self):
        form = GameCreateForm(user=self.user)
        return set(form.fields['round'].queryset.values_list('id', flat=True))

    def test_guild_member_sees_round_under_guild_access(self):
        self.assertIn(self.round.id, self._round_ids())

    def test_guild_member_hidden_under_registered_access(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.REGISTERED
        self.tournament.save()
        self.assertNotIn(self.round.id, self._round_ids())


class PlayableRoundGuildTests(TestCase):
    """user_can_record_in_round (which drives the nav-header New Game button /
    playable_round) honors guild membership under GUILD access."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="3003", name="Nav Guild")

        self.member = User.objects.create_user(username="navmember", password="x")
        self.member.profile.guilds.add(self.guild)

        self.outsider = User.objects.create_user(username="navoutsider", password="x")

        self.tournament = Tournament.objects.create(
            name="Nav Tournament",
            guild=self.guild,
            recording_access=Tournament.RecordingAccessTypes.GUILD,
            is_active=True,
        )
        self.stage = Stage.objects.create(
            tournament=self.tournament, name="Stage 1", order=1, is_active=True
        )
        self.round = Round.objects.create(
            stage=self.stage, round_number=1, is_active=True
        )

    def test_guild_member_can_record_in_round(self):
        self.assertTrue(user_can_record_in_round(self.round, self.member))

    def test_non_member_cannot_record_in_round(self):
        self.assertFalse(user_can_record_in_round(self.round, self.outsider))

    def test_guild_member_denied_under_lower_tier(self):
        self.tournament.recording_access = Tournament.RecordingAccessTypes.REGISTERED
        self.tournament.save()
        self.assertFalse(user_can_record_in_round(self.round, self.member))
