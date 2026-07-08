from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from the_gatehouse.models import DiscordGuild, Profile
from the_warroom.forms import GameCreateForm
from the_warroom.models import (
    Effort, Game, Match, MatchSeries, Round, Stage, Tournament,
)
from the_warroom.views import _can_record_match, user_can_record_in_round


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


class GuildMatchRecordingTests(TestCase):
    """Under GUILD access a guild member may record standalone games, but NOT
    match games -- match recording stays limited to seated participants (and
    managers/group moderators). Guarded by _can_record_match."""

    def setUp(self):
        self.guild = DiscordGuild.objects.create(guild_id="4004", name="Match Guild")

        self.member = User.objects.create_user(username="matchmember", password="x")
        self.member.profile.guilds.add(self.guild)

        self.tournament = Tournament.objects.create(
            name="Match Tournament",
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
        self.series = MatchSeries.objects.create(round=self.round)
        self.match = Match.objects.create(round=self.round, series=self.series)

    def test_guild_member_cannot_record_match(self):
        # Guild membership grants standalone recording, but the member is not
        # seated in this match's series, so match recording must be denied.
        self.assertFalse(_can_record_match(self.member.profile, self.match))


class ExtraRoundsCountingTests(TestCase):
    """A game in tournament A's round can also be counted in tournament B via
    extra_rounds, without joining B's bracket/primary-round relations."""

    def setUp(self):
        # Tournament A (the game's primary home)
        self.tour_a = Tournament.objects.create(name="Discord Tournament", is_active=True)
        self.stage_a = Stage.objects.create(tournament=self.tour_a, name="A1", order=1, is_active=True)
        self.round_a = Round.objects.create(stage=self.stage_a, round_number=1, is_active=True)

        # Tournament B (the aggregate that should also count the game)
        self.tour_b = Tournament.objects.create(name="2026 Games", is_active=True)
        self.stage_b = Stage.objects.create(tournament=self.tour_b, name="B1", order=1, is_active=True)
        self.round_b = Round.objects.create(stage=self.stage_b, round_number=1, is_active=True)

        self.player = Profile.objects.create(discord="player1")
        self.game = Game.objects.create(round=self.round_a, final=True)
        Effort.objects.create(game=self.game, player=self.player)

    def test_baseline_counts_only_primary(self):
        self.assertEqual(self.tour_a.game_count(), 1)
        self.assertEqual(self.tour_b.game_count(), 0)

    def test_extra_round_counts_in_both_tournaments(self):
        self.game.extra_rounds.add(self.round_b)
        self.assertEqual(self.tour_a.game_count(), 1)
        self.assertEqual(self.tour_b.game_count(), 1)
        self.assertEqual(self.round_b.game_count(), 1)
        self.assertEqual(self.stage_b.game_count(), 1)

    def test_extra_round_counts_players(self):
        self.game.extra_rounds.add(self.round_b)
        self.assertEqual(self.tour_b.all_player_count(), 1)
        self.assertEqual(self.round_b.all_player_count, 1)

    def test_no_double_count_when_both_rounds_same_tournament(self):
        # An extra round within the SAME tournament must not double-count the game.
        other_round_a = Round.objects.create(stage=self.stage_a, round_number=2, is_active=True)
        self.game.extra_rounds.add(other_round_a)
        self.assertEqual(self.tour_a.game_count(), 1)

    def test_extra_round_excluded_from_primary_relations(self):
        self.game.extra_rounds.add(self.round_b)
        # Bucket B (primary-only) relations must ignore the extra association.
        self.assertNotIn(self.game, self.round_b.games.all())
        self.assertFalse(Game.objects.filter(round=self.round_b).exists())
        self.assertEqual(self.game.get_tournament(), self.tour_a)


class ExtraRoundsControlTests(TestCase):
    """Add/remove extra-round endpoints authorize against the round's tournament.

    The user_logged_in signal handler builds absolute URIs and adds messages,
    neither of which a bare force_login request supports; disconnect it for these
    auth-flow tests.
    """

    def setUp(self):
        from django.contrib.auth.signals import user_logged_in
        from the_gatehouse.signals import user_logged_in_handler
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)

        super().setUp()
        self._build()

    @staticmethod
    def _make_player(username):
        user = User.objects.create_user(username=username, password="x")
        profile = user.profile
        profile.group = Profile.GroupChoices.PLAYER
        profile.player_onboard = True
        profile.save()
        return user

    def _build(self):
        # Designer of tournament B
        self.designer = self._make_player("designer")
        self.tour_b = Tournament.objects.create(
            name="2026 Games", designer=self.designer.profile, is_active=True
        )
        self.stage_b = Stage.objects.create(tournament=self.tour_b, name="B1", order=1, is_active=True)
        self.round_b = Round.objects.create(stage=self.stage_b, round_number=1, is_active=True)
        # A second round in the same tournament B (used for one-per-tournament tests).
        self.round_b2 = Round.objects.create(stage=self.stage_b, round_number=2, is_active=True)

        # A game living in an unrelated tournament A
        self.tour_a = Tournament.objects.create(name="Discord", is_active=True)
        self.stage_a = Stage.objects.create(tournament=self.tour_a, name="A1", order=1, is_active=True)
        self.round_a = Round.objects.create(stage=self.stage_a, round_number=1, is_active=True)
        self.game = Game.objects.create(round=self.round_a, final=True)

        self.outsider = self._make_player("outsider")

    def _add_url(self):
        return reverse('game-add-extra-round', args=[self.game.id])

    def _remove_url(self):
        return reverse('game-remove-extra-round', args=[self.game.id, self.round_b.id])

    def test_designer_can_add_extra_round(self):
        self.client.force_login(self.designer)
        resp = self.client.post(self._add_url(), {'round_id': self.round_b.id})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.round_b, self.game.extra_rounds.all())

    def test_non_designer_forbidden(self):
        self.client.force_login(self.outsider)
        resp = self.client.post(self._add_url(), {'round_id': self.round_b.id})
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(self.round_b, self.game.extra_rounds.all())

    def test_designer_can_remove_extra_round(self):
        self.game.extra_rounds.add(self.round_b)
        self.client.force_login(self.designer)
        resp = self.client.post(self._remove_url())
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(self.round_b, self.game.extra_rounds.all())

    def test_non_designer_cannot_remove(self):
        self.game.extra_rounds.add(self.round_b)
        self.client.force_login(self.outsider)
        resp = self.client.post(self._remove_url())
        self.assertEqual(resp.status_code, 403)
        self.assertIn(self.round_b, self.game.extra_rounds.all())

    def test_cannot_add_second_round_from_same_tournament(self):
        # Game already counts in tournament B via round_b; adding round_b2 (same
        # tournament) must be rejected and leave extra_rounds unchanged.
        self.game.extra_rounds.add(self.round_b)
        self.client.force_login(self.designer)
        resp = self.client.post(self._add_url(), {'round_id': self.round_b2.id})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn(self.round_b2, self.game.extra_rounds.all())
        self.assertIn(self.round_b, self.game.extra_rounds.all())

    def test_addable_rounds_excludes_occupied_tournament(self):
        from the_warroom.views import _extra_rounds_control_context
        self.game.extra_rounds.add(self.round_b)
        context = _extra_rounds_control_context(self.game, self.designer.profile)
        addable = list(context['addable_rounds'])
        # round_b is already added; round_b2 shares its tournament, so neither is offered.
        self.assertNotIn(self.round_b, addable)
        self.assertNotIn(self.round_b2, addable)
