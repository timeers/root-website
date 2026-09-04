import json
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Prefetch
from django.db.models.signals import post_save
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from the_gatehouse.models import DiscordGuild, Profile
from the_databot.models import (
    GuildLFGRole, LFGThread, LFGSeat, LFGDraft, LFGDraftPick,
)
from the_databot.services.lfg_game import lfg_option_querysets
from the_keep.models import (
    Deck, Faction, Hireling, Landmark, Map, StatusChoices, Tweak, Vagabond,
)
from the_warroom.services.box_score_import import (
    BoxScoreImportError, normalize_turns, parse_box_score_json, resolve_import,
    validate_participants,
)
from the_gatehouse.signals import handle_image_resize, user_logged_in_handler
from the_warroom.forms import GameCreateForm
from the_databot.tasks import create_match_threads_task
from the_warroom.models import (
    CompetitionStatus, Effort, Game, Match, MatchSeat, MatchSeries, PlayerGroup,
    Round, Stage, StageParticipant, Tournament, TournamentPlayer,
)
from the_warroom.views import (
    _can_record_match, user_can_record_in_round, _prefill_undrafted,
)


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


class ExtraRoundsLeaderboardAggregateTests(TestCase):
    """Leaderboard AGGREGATES (not just counts) must not double-count a game that
    reaches a tournament through several rounds.

    ExtraRoundsCountingTests covers game_count()/all_player_count(). These cover the
    win-rate path, which spans the same round/extra_rounds OR but multiplies effort
    rows rather than game rows -- the failure mode is a doubled win count on a series
    leaderboard, not a doubled game count.
    """

    def setUp(self):
        self.tour_a = Tournament.objects.create(name="Aggregate Tournament", is_active=True)
        self.stage_a = Stage.objects.create(tournament=self.tour_a, name="A1", order=1, is_active=True)
        self.round_a = Round.objects.create(stage=self.stage_a, round_number=1, is_active=True)

        self.tour_b = Tournament.objects.create(name="Other Tournament", is_active=True)
        self.stage_b = Stage.objects.create(tournament=self.tour_b, name="B1", order=1, is_active=True)
        self.round_b = Round.objects.create(stage=self.stage_b, round_number=1, is_active=True)

        self.player = Profile.objects.create(discord="agg_player")
        self.faction = Faction.objects.create(title="Agg Faction", type="M", reach=5,
                                              animal="cat", designer=self.player)
        self.game = Game.objects.create(round=self.round_a, final=True, test_match=False)
        Effort.objects.create(game=self.game, player=self.player,
                              faction=self.faction, win=True)

    def _winrate(self, tournament):
        from the_warroom.models import filtered_winrate
        return filtered_winrate(player=self.player, tournament=tournament)

    def test_or_across_extra_rounds_multiplies_raw_rows(self):
        """Documents WHY the dedup below is required.

        The round/extra_rounds OR emits two independent join chains, so a game reaching
        the tournament through several legs yields the SAME effort row more than once.
        Two separate safeguards currently mask this -- the queryset-level .distinct() and
        the distinct=True on each Count in filtered_winrate -- so removing only one keeps
        totals correct, and a test asserting only on totals will not notice. This test
        pins the underlying duplication directly.

        Measured threshold: duplication begins at TWO extra rounds in the same
        tournament (one extra round still collapses to a single row), and the raw row
        count then scales with the number of extra rounds.
        """
        from the_warroom.models import effort_counts_for_tournament_q

        def raw_rows():
            return list(Effort.objects
                        .filter(game__final=True, player=self.player)
                        .filter(effort_counts_for_tournament_q(self.tour_a))
                        .values_list('id', flat=True))

        # One extra round: no multiplication yet.
        self.game.extra_rounds.add(
            Round.objects.create(stage=self.stage_a, round_number=2, is_active=True))
        self.assertEqual(len(raw_rows()), 1)

        # Two extra rounds: the same effort row now comes back twice.
        self.game.extra_rounds.add(
            Round.objects.create(stage=self.stage_a, round_number=3, is_active=True))
        ids = raw_rows()
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 1, "same effort row, duplicated by the OR")

        # Three: scales with the number of legs.
        self.game.extra_rounds.add(
            Round.objects.create(stage=self.stage_a, round_number=4, is_active=True))
        self.assertEqual(len(raw_rows()), 3)

        # Dedup must bring it back to one.
        self.assertEqual(
            Effort.objects.filter(game__final=True, player=self.player)
            .filter(effort_counts_for_tournament_q(self.tour_a)).distinct().count(), 1)

    def test_duplicate_legs_do_not_inflate(self):
        """Primary round AND two extra rounds, all inside the SAME tournament.

        This is the regression guard: the game counts once, so the player has exactly
        one effort and one win -- not two or three.
        """
        extra1 = Round.objects.create(stage=self.stage_a, round_number=2, is_active=True)
        extra2 = Round.objects.create(stage=self.stage_a, round_number=3, is_active=True)
        self.game.extra_rounds.add(extra1, extra2)

        stats = self._winrate(self.tour_a)
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['games'], 1)
        self.assertEqual(stats['win_points'], 1)
        self.assertEqual(stats['win_rate'], 100.0)

    def test_leaderboard_boards_do_not_inflate_across_duplicate_legs(self):
        """The rendered boards themselves -- not just filtered_winrate -- must show one
        effort and one win for a game that reaches the tournament through three legs."""
        extra1 = Round.objects.create(stage=self.stage_a, round_number=2, is_active=True)
        extra2 = Round.objects.create(stage=self.stage_a, round_number=3, is_active=True)
        self.game.extra_rounds.add(extra1, extra2)

        qs = self._winrate(self.tour_a)['qs']
        players = Profile.leaderboard(effort_qs=qs, limit=10, game_threshold=1)
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0].total_efforts, 1)
        self.assertEqual(players[0].win_count, 1)
        self.assertEqual(players[0].win_rate, 100.0)

        factions = Faction.leaderboard(effort_qs=qs, limit=10, game_threshold=1)
        self.assertEqual(len(factions), 1)
        self.assertEqual(factions[0].total_efforts, 1)
        self.assertEqual(factions[0].win_count, 1)

    def test_extra_round_only_still_counts(self):
        """A game whose ONLY link to tournament B is via extra_rounds still counts."""
        self.game.extra_rounds.add(self.round_b)

        stats = self._winrate(self.tour_b)
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['win_rate'], 100.0)

    def test_game_with_no_primary_round_counts_via_extra(self):
        """round=None is legitimate: a game can reach a tournament purely via extras."""
        self.game.round = None
        self.game.save()
        self.game.extra_rounds.add(self.round_b)

        stats = self._winrate(self.tour_b)
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['win_rate'], 100.0)

    def test_unrelated_tournament_counts_nothing(self):
        tour_c = Tournament.objects.create(name="Unrelated", is_active=True)
        stats = self._winrate(tour_c)
        self.assertEqual(stats['total'], 0)

    def test_roster_aggregate_matches_across_duplicate_legs(self):
        """The roster shape used by the series leaderboard page agrees with
        filtered_winrate even when the game reaches the tournament twice."""
        from django.db.models import Count, Q
        from the_warroom.models import effort_counts_for_tournament_q

        extra1 = Round.objects.create(stage=self.stage_a, round_number=2, is_active=True)
        self.game.extra_rounds.add(extra1)

        counts_q = effort_counts_for_tournament_q(self.tour_a, prefix='efforts__game')
        row = (Profile.objects.filter(counts_q).distinct().annotate(
            total_efforts=Count('efforts', distinct=True,
                                filter=counts_q & Q(efforts__game__final=True)),
            win_count=Count('efforts', distinct=True,
                            filter=counts_q & Q(efforts__win=True,
                                                efforts__game__final=True)),
        ).get(pk=self.player.pk))
        self.assertEqual(row.total_efforts, 1)
        self.assertEqual(row.win_count, 1)


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


class EloSeasonTests(TestCase):
    """Season boundaries reset/reseed the local Elo replay per the season's reset_mode,
    and games are numbered 0 (preseason) / 1 / 2... by date."""

    def setUp(self):
        from datetime import datetime, timezone as dt_tz
        from the_warroom.models import EloSystem, EloSeason

        self.dt = lambda m, d: datetime(2026, m, d, 12, 0, tzinfo=dt_tz.utc)

        self.system = EloSystem.objects.create(
            name="Local S", calculation_type=EloSystem.CalculationType.LOCAL,
            min_players=2, max_players=6, k_factor=32, k_provisional=32,
            provisional_games=0, initial_rating=1500,
        )
        self.tournament = Tournament.objects.create(name="T", elo_system=self.system)
        self.stage = Stage.objects.create(tournament=self.tournament, name="S", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)

        self.p1 = Profile.objects.create(discord="p1")
        self.p2 = Profile.objects.create(discord="p2")
        self.EloSeason = EloSeason

    def _make_game(self, month, day, winner, loser):
        """A final 2-player game on the given date; winner beats loser."""
        game = Game.objects.create(round=self.round, date_posted=self.dt(month, day),
                                   final=True, cached_player_count=2)
        Effort.objects.create(game=game, player=winner, win=True)
        Effort.objects.create(game=game, player=loser, win=False)
        return game

    def _recompute(self, cutoff):
        from the_warroom.services.elo_service import recompute_system_from
        recompute_system_from(self.system, cutoff)

    def test_no_seasons_is_plain_replay(self):
        from the_warroom.models import EloRating, EloParticipant
        self._make_game(1, 5, self.p1, self.p2)
        self._recompute(self.dt(1, 1))
        # p1 won, so p1 rating rose above and p2 fell below initial.
        self.assertGreater(EloParticipant.objects.get(elo_system=self.system, player=self.p1).rating, 1500)
        self.assertLess(EloParticipant.objects.get(elo_system=self.system, player=self.p2).rating, 1500)
        self.assertEqual(EloRating.objects.filter(elo_system=self.system).count(), 2)

    def test_hard_reset_starts_new_season_at_initial(self):
        from the_warroom.models import EloRating
        # Season 0: p1 beats p2 twice in January -> p1 climbs.
        self._make_game(1, 5, self.p1, self.p2)
        g_jan2 = self._make_game(1, 20, self.p1, self.p2)
        # HARD season 1 starts Feb 1.
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.HARD)
        g_feb = self._make_game(2, 10, self.p2, self.p1)
        self._recompute(self.dt(1, 1))

        # First season-1 game: both players enter at initial_rating (HARD reset).
        feb_rows = EloRating.objects.filter(game=g_feb)
        self.assertEqual(feb_rows.count(), 2)
        for r in feb_rows:
            self.assertEqual(r.rating_before, 1500)
        # Season-0 rows are preserved and did NOT reset (p1's 2nd Jan win builds on the 1st).
        jan2_p1 = EloRating.objects.get(game=g_jan2, player=self.p1)
        self.assertGreater(jan2_p1.rating_before, 1500)

    def test_soft_reset_seeds_from_prior_season(self):
        from the_warroom.models import EloRating
        # Season 0: p1 beats p2 -> p1 ends above 1500.
        self._make_game(1, 5, self.p1, self.p2)
        # SOFT season 1, factor 0.5 -> seed compresses halfway toward 1500.
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.SOFT,
                                      soft_reset_factor=0.5)
        g_feb = self._make_game(2, 10, self.p1, self.p2)
        self._recompute(self.dt(1, 1))

        end_s0 = EloRating.objects.get(game__date_posted=self.dt(1, 5), player=self.p1).rating_after
        seed = EloRating.objects.get(game=g_feb, player=self.p1).rating_before
        self.assertAlmostEqual(seed, end_s0 + 0.5 * (1500 - end_s0), places=6)
        self.assertNotAlmostEqual(seed, 1500, places=3)  # not a full reset

    def test_soft_reset_seeds_on_midseason_recompute(self):
        """The dirty-watermark path: a recompute whose cutoff lands INSIDE a SOFT season
        must still regenerate the seed from the prior season's final (not hard-reset)."""
        from the_warroom.models import EloRating
        self._make_game(1, 5, self.p1, self.p2)  # season 0
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.SOFT,
                                      soft_reset_factor=0.5)
        g_feb = self._make_game(2, 10, self.p1, self.p2)  # season 1
        # Establish full history first (as a from-scratch recompute would).
        self._recompute(self.dt(1, 1))
        # Then recompute from INSIDE season 1 (mimics an effort edit dated Feb 10).
        self._recompute(self.dt(2, 10))
        end_s0 = EloRating.objects.get(game__date_posted=self.dt(1, 5), player=self.p1).rating_after
        seed = EloRating.objects.get(game=g_feb, player=self.p1).rating_before
        self.assertAlmostEqual(seed, end_s0 + 0.5 * (1500 - end_s0), places=6)
        self.assertNotAlmostEqual(seed, 1500, places=3)

    def test_none_reset_carries_ratings_across(self):
        from the_warroom.models import EloRating
        self._make_game(1, 5, self.p1, self.p2)
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.NONE)
        g_feb = self._make_game(2, 10, self.p1, self.p2)
        self._recompute(self.dt(1, 1))
        end_s0 = EloRating.objects.get(game__date_posted=self.dt(1, 5), player=self.p1).rating_after
        seed = EloRating.objects.get(game=g_feb, player=self.p1).rating_before
        self.assertAlmostEqual(seed, end_s0, places=6)  # carried across unchanged

    def test_season_number_derivation(self):
        from the_warroom.services.elo_service import _season_number_for
        starts = [self.dt(2, 1), self.dt(5, 1)]
        self.assertEqual(_season_number_for(starts, self.dt(1, 15)), 0)  # preseason
        self.assertEqual(_season_number_for(starts, self.dt(2, 1)), 1)   # boundary inclusive
        self.assertEqual(_season_number_for(starts, self.dt(3, 1)), 1)
        self.assertEqual(_season_number_for(starts, self.dt(6, 1)), 2)

    def test_creating_hard_season_marks_system_dirty(self):
        from the_warroom.models import EloSystem
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.HARD)
        self.system.refresh_from_db()
        self.assertEqual(self.system.recompute_from, self.dt(2, 1))

    def test_matchapi_season_number(self):
        self.EloSeason.objects.create(elo_system=self.system, start_date=self.dt(2, 1),
                                      reset_mode=self.EloSeason.ResetMode.HARD)
        g_jan = self._make_game(1, 5, self.p1, self.p2)
        g_feb = self._make_game(2, 10, self.p1, self.p2)
        jan = dict(g_jan.get_elo_systems_with_seasons())
        feb = dict(g_feb.get_elo_systems_with_seasons())
        self.assertEqual(jan[self.system], 0)  # preseason
        self.assertEqual(feb[self.system], 1)


class EloEligibleGamesTests(TestCase):
    """`Game.objects.eligible_for_elo_system` is the one definition of "this system
    rates this game", shared by the rating engine, the download API's elo_system
    filter and the Elo games page. These lock the three together."""

    def setUp(self):
        from datetime import datetime, timezone as dt_tz
        from the_warroom.models import EloSystem

        self.dt = lambda m, d: datetime(2026, m, d, 12, 0, tzinfo=dt_tz.utc)
        self.system = EloSystem.objects.create(
            name="Bounded S", slug="bounded-s",
            calculation_type=EloSystem.CalculationType.LOCAL,
            min_players=2, max_players=4,
        )
        self.tournament = Tournament.objects.create(name="T", elo_system=self.system)
        self.stage = Stage.objects.create(tournament=self.tournament, name="S", order=1)
        self.round = Round.objects.create(stage=self.stage, round_number=1)

        # A second series feeding nothing, for the extra_rounds case.
        self.other = Tournament.objects.create(name="Other")
        self.other_stage = Stage.objects.create(tournament=self.other, name="OS", order=1)
        self.other_round = Round.objects.create(stage=self.other_stage, round_number=1)

    def _game(self, round=None, players=2, **kwargs):
        opts = {'final': True, 'test_match': False, 'date_posted': self.dt(1, 5)}
        opts.update(kwargs)
        return Game.objects.create(round=round, cached_player_count=players, **opts)

    def _eligible(self):
        return set(Game.objects.eligible_for_elo_system(self.system))

    def test_excludes_test_matches(self):
        """The engine has always skipped test matches; the API filter and
        game_is_eligible used not to. Regression guard for that fix -- note the
        rest of the suite cannot catch it, as its fixtures never set test_match."""
        from the_warroom.services.elo_service import _eligible_games_for_system

        real = self._game(round=self.round)
        test = self._game(round=self.round, test_match=True)

        self.assertEqual(self._eligible(), {real})
        self.assertNotIn(test, self._eligible())
        # The page queryset and the rating engine must agree exactly.
        self.assertEqual(self._eligible(), set(_eligible_games_for_system(self.system)))
        # ...and so must the per-game predicate behind the API's elo_systems field.
        self.assertTrue(self.system.game_is_eligible(real))
        self.assertFalse(self.system.game_is_eligible(test))

    def test_excludes_unfinished_games(self):
        self._game(round=self.round, final=False)
        self.assertEqual(self._eligible(), set())

    def test_respects_player_bounds(self):
        too_few = self._game(round=self.round, players=1)
        ok = self._game(round=self.round, players=4)
        too_many = self._game(round=self.round, players=5)
        self.assertEqual(self._eligible(), {ok})
        self.assertFalse(self.system.game_is_eligible(too_few))
        self.assertFalse(self.system.game_is_eligible(too_many))

    def test_includes_extra_round_games(self):
        """A game whose primary round belongs elsewhere still counts if an extra
        round points into a series using this system."""
        game = self._game(round=self.other_round)
        self.assertEqual(self._eligible(), set())
        game.extra_rounds.add(self.round)
        self.assertEqual(self._eligible(), {game})

    def test_no_duplicate_when_primary_and_extra_both_match(self):
        game = self._game(round=self.round)
        game.extra_rounds.add(self.round)
        self.assertEqual(len(Game.objects.eligible_for_elo_system(self.system)), 1)

    def test_api_filter_excludes_test_matches(self):
        from the_warroom.api.game_filters import GameFilter

        real = self._game(round=self.round)
        self._game(round=self.round, test_match=True)

        filtered = GameFilter.filter_elo_system(Game.objects.all(), 'elo_system', self.system)
        self.assertEqual(set(filtered), {real})

    def test_counting_is_structural_only(self):
        """counting_for_elo_system keeps the counting_for_* convention: attachment
        only, no eligibility gate."""
        test = self._game(round=self.round, test_match=True)
        unfinished = self._game(round=self.round, final=False)
        self.assertEqual(set(Game.objects.counting_for_elo_system(self.system)),
                         {test, unfinished})


class EloSystemPageTests(TestCase):
    """The three Elo system tabs render publicly."""

    def setUp(self):
        from the_warroom.models import EloSystem
        self.system = EloSystem.objects.create(name="Public S", slug="public-s")

    def test_tabs_render_anonymously(self):
        for name in ('elo-system-leaderboard-page', 'elo-system-games-page',
                     'elo-system-details-page'):
            with self.subTest(url=name):
                response = self.client.get(reverse(name, args=[self.system.slug]))
                self.assertEqual(response.status_code, 200)

    def test_get_absolute_url_is_the_leaderboard(self):
        """The bare-slug route is canonical. `elo-system-leaderboard-page` is a
        second name for the same view (the nav header links to it), so this pins
        the URL get_absolute_url actually builds and then checks both names land
        on the leaderboard -- asserting the two reverse() calls are equal would
        be false by design, since only one of them is canonical."""
        self.assertEqual(
            self.system.get_absolute_url(),
            reverse('elo-system-home-page', args=[self.system.slug]),
        )
        for name in ('elo-system-home-page', 'elo-system-leaderboard-page'):
            with self.subTest(url=name):
                response = self.client.get(reverse(name, args=[self.system.slug]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['active_page'], 'leaderboard')

    def test_get_absolute_url_is_none_without_a_slug(self):
        """The field is nullable, so templates guard on this rather than 500."""
        from the_warroom.models import EloSystem
        self.assertIsNone(EloSystem(name="Unslugged").get_absolute_url())


class GameApiTournamentTests(TestCase):
    """The api/games `tournament` field lists every round a game counts toward
    (primary + extra_rounds), and the tournament filter matches games linked to a
    tournament through any of those rounds."""

    def setUp(self):
        self.alpha = Tournament.objects.create(name="Alpha Cup")
        self.beta = Tournament.objects.create(name="Beta Cup")
        self.alpha_stage = Stage.objects.create(tournament=self.alpha, name="Groups", order=0)
        self.beta_stage = Stage.objects.create(tournament=self.beta, name="Finals", order=0)
        self.alpha_round = Round.objects.create(round_number=1, stage=self.alpha_stage, name="A01")
        self.beta_round = Round.objects.create(round_number=2, stage=self.beta_stage, name="B01")

    def _serialize(self, game):
        from the_warroom.api.game_serializers import GameSerializer
        return GameSerializer(game).data['tournament']

    def test_primary_round_only(self):
        game = Game.objects.create(round=self.alpha_round, final=True)
        data = self._serialize(game)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['series'], self.alpha.slug)
        self.assertEqual(data[0]['round'], self.alpha_round.slug)

    def test_no_round_returns_empty_list(self):
        game = Game.objects.create(final=True)
        self.assertEqual(self._serialize(game), [])

    def test_primary_and_extra_rounds_listed(self):
        game = Game.objects.create(round=self.alpha_round, final=True)
        game.extra_rounds.add(self.beta_round)
        data = self._serialize(game)
        # Primary first, then the extra round.
        self.assertEqual([e['series'] for e in data], [self.alpha.slug, self.beta.slug])
        self.assertEqual([e['round'] for e in data], [self.alpha_round.slug, self.beta_round.slug])

    def test_extra_round_duplicate_of_primary_not_repeated(self):
        game = Game.objects.create(round=self.alpha_round, final=True)
        game.extra_rounds.add(self.alpha_round)  # same as primary
        data = self._serialize(game)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['round'], self.alpha_round.slug)

    def _filter_pks(self, tournament):
        from the_warroom.api.game_filters import GameFilter
        f = GameFilter({'tournament': tournament.slug}, queryset=Game.objects.filter(final=True))
        return set(f.qs.values_list('pk', flat=True))

    def test_filter_matches_primary_round_tournament(self):
        game = Game.objects.create(round=self.alpha_round, final=True)
        self.assertIn(game.pk, self._filter_pks(self.alpha))

    def test_filter_matches_extra_round_tournament(self):
        # Linked to Beta ONLY via an extra round — must still match the Beta filter.
        game = Game.objects.create(round=self.alpha_round, final=True)
        game.extra_rounds.add(self.beta_round)
        self.assertIn(game.pk, self._filter_pks(self.beta))

    def test_filter_excludes_unrelated_tournament(self):
        game = Game.objects.create(round=self.alpha_round, final=True)
        self.assertNotIn(game.pk, self._filter_pks(self.beta))


class UndraftedPrefillTests(TestCase):
    """_prefill_undrafted seeds the game-level undrafted_* fields from the one
    drafted faction no seat took."""

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Faction)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Faction)
        post_save.disconnect(handle_image_resize, sender=Vagabond)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Vagabond)

        self.designer = Profile.objects.create(discord="undp", discord_id="900")
        self.factions = [
            Faction.objects.create(
                title=f"Undrafted Faction {i}", animal="Fox",
                designer=self.designer, status=StatusChoices.STABLE,
                official=True, component="Faction",
                type=Faction.TypeChoices.MILITANT)
            for i in range(3)
        ]
        # The real title is what GameCreateForm.clean() keys off.
        self.knaves = Faction.objects.create(
            title="Knaves of the Deepwood", animal="Mole", designer=self.designer,
            status=StatusChoices.STABLE, official=True, component="Faction",
            type=Faction.TypeChoices.INSURGENT)
        self.thread = LFGThread.objects.create(thread_id="1303834523347400001")

    def _vagabond(self, title, captain=False):
        return Vagabond.objects.create(
            title=title, animal="Fox", designer=self.designer,
            status=StatusChoices.STABLE, official=True, captain=captain)

    def _opts(self):
        return lfg_option_querysets(self.thread, None)

    def _seat(self, n, faction):
        p = Profile.objects.create(discord=f"unds{n}", discord_id=f"91{n}")
        return LFGSeat.objects.create(thread=self.thread, profile=p,
                                      seat_number=n, faction=faction)

    class _Form:
        def __init__(self):
            self.initial = {}

    def test_prefills_the_leftover_faction(self):
        draft = LFGDraft.objects.create(thread=self.thread)
        for i, f in enumerate(self.factions[:2], 1):
            LFGDraftPick.objects.create(draft=draft, faction=f, order=i)
        self._seat(1, self.factions[0])

        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertEqual(form.initial['undrafted_faction'], self.factions[1].pk)

    def test_prefills_the_leftover_vagabond(self):
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        vb = self._vagabond("Undrafted Prefill Ranger")
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[1],
                                    vagabond=vb, order=2)
        self._seat(1, self.factions[0])

        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertEqual(form.initial['undrafted_vagabond'], vb.pk)

    def test_prefills_four_captains_with_knaves_as_the_faction(self):
        """The faction prefill is load-bearing: clean() CLEARS undrafted_captains
        unless undrafted_faction is Knaves."""
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        caps = [self._vagabond(f"Undrafted Cap {i}", captain=True) for i in range(4)]
        pick = LFGDraftPick.objects.create(draft=draft, faction=self.knaves, order=2)
        pick.captains.set(caps)
        self._seat(1, self.factions[0])

        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertEqual(form.initial['undrafted_faction'], self.knaves.pk)
        self.assertEqual(set(form.initial['undrafted_captains']),
                         {c.pk for c in caps})

    def test_a_short_captain_roll_is_not_prefilled(self):
        """clean() requires exactly 4 or none, so seeding 3 would fail on submit."""
        draft = LFGDraft.objects.create(thread=self.thread)
        LFGDraftPick.objects.create(draft=draft, faction=self.factions[0], order=1)
        caps = [self._vagabond(f"Short Cap {i}", captain=True) for i in range(3)]
        pick = LFGDraftPick.objects.create(draft=draft, faction=self.knaves, order=2)
        pick.captains.set(caps)
        self._seat(1, self.factions[0])

        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertNotIn('undrafted_captains', form.initial)
        self.assertEqual(form.initial['undrafted_faction'], self.knaves.pk)

    def test_no_prefill_mid_pick(self):
        draft = LFGDraft.objects.create(thread=self.thread)
        for i, f in enumerate(self.factions, 1):
            LFGDraftPick.objects.create(draft=draft, faction=f, order=i)
        self._seat(1, self.factions[0])

        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertEqual(form.initial, {})

    def test_no_prefill_without_a_draft(self):
        self._seat(1, self.factions[0])
        form = self._Form()
        _prefill_undrafted(form, self.thread, self._opts())
        self.assertEqual(form.initial, {})


class CreateGameThreadsEndpointTests(TestCase):
    """The Create Game Threads endpoint posts into a Discord server, so it re-checks
    permission itself — the button being hidden is not access control."""

    def setUp(self):
        # force_login fires user_logged_in, whose handler builds absolute URLs from the
        # request and enqueues Discord work -- neither of which a bare test request
        # supports, and none of it is under test here.
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        post_save.disconnect(handle_image_resize, sender=Profile)
        self.guild = DiscordGuild.objects.create(guild_id="910100", name="Threads Guild",
                                                 bot_member=True)
        # The endpoint gates on Tournament.has_permission (designer/moderator/admin),
        # not on the stricter `can_manage` the template uses, so being the designer is
        # enough here.
        self.host = User.objects.create_user(username="host", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")

        self.tournament = Tournament.objects.create(
            name="Threads Cup", guild=self.guild, designer=self.host.profile,
            game_threads_channel="300000000000000033")
        self.stage = Stage.objects.create(tournament=self.tournament, name="S1", order=1)
        self.round = Round.objects.create(
            stage=self.stage, round_number=1,
            bracket_status=Round.BracketStatusChoices.FINALIZED)
        self.group = PlayerGroup.objects.create(
            round=self.round, group_number=1, name="Group A")
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)

    def _url(self, tournament=None, stage=None, round=None):
        return reverse('round-create-game-threads', kwargs={
            'tournament_slug': (tournament or self.tournament).slug,
            'stage_slug': (stage or self.stage).slug,
            'round_slug': (round or self.round).slug,
        })

    def _post(self, url=None):
        with mock.patch.object(create_match_threads_task, 'delay') as delay:
            response = self.client.post(url or self._url())
        return response, delay

    def test_moderator_queues_the_task(self):
        self.client.force_login(self.host)
        response, delay = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(response.json()['queued'], 1)
        delay.assert_called_once()

    def test_anonymous_is_rejected_and_queues_nothing(self):
        response, delay = self._post()
        self.assertIn(response.status_code, (302, 403))
        delay.assert_not_called()

    def test_non_moderator_is_forbidden(self):
        self.client.force_login(self.outsider)
        response, delay = self._post()
        self.assertEqual(response.status_code, 403)
        delay.assert_not_called()

    def test_get_is_not_allowed(self):
        """No state change via GET, so a crafted link can't trigger it."""
        self.client.force_login(self.host)
        with mock.patch.object(create_match_threads_task, 'delay') as delay:
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 405)
        delay.assert_not_called()

    def test_another_tournaments_round_is_not_reachable(self):
        """Chained scoping: the round must belong to the stage, the stage to the
        tournament. A mismatched slug 404s rather than acting."""
        other_t = Tournament.objects.create(name="Other Cup", designer=self.host.profile)
        other_s = Stage.objects.create(tournament=other_t, name="S1", order=1)
        other_r = Round.objects.create(stage=other_s, round_number=1)
        self.client.force_login(self.host)
        response, delay = self._post(self._url(round=other_r))
        self.assertEqual(response.status_code, 404)
        delay.assert_not_called()

    def test_missing_channel_is_rejected(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(game_threads_channel=None)
        self.client.force_login(self.host)
        response, delay = self._post()
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    def test_missing_guild_is_rejected(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(guild=None)
        self.client.force_login(self.host)
        response, delay = self._post()
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    def test_nothing_to_do_is_rejected(self):
        self.group.discord_thread = f"https://discord.com/channels/{self.guild.guild_id}/999"
        self.group.save()
        self.client.force_login(self.host)
        response, delay = self._post()
        self.assertEqual(response.status_code, 400)
        delay.assert_not_called()

    def test_count_uses_empty_string_not_isnull(self):
        """discord_thread is blank-not-null: an __isnull filter would match nothing
        and the button would always claim every match already has a thread."""
        from the_warroom.views import _threads_to_create_count
        self.assertEqual(_threads_to_create_count(self.round), 1)
        self.group.discord_thread = f"https://discord.com/channels/{self.guild.guild_id}/999"
        self.group.save()
        self.assertEqual(_threads_to_create_count(self.round), 0)


class GuildOnEveryClassificationTests(TestCase):
    """A guild used to be League-only; switching to Tournament or Game Group cleared
    it. Every classification may now link one."""

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)
        self.guild_a = DiscordGuild.objects.create(guild_id="930100", name="A")
        self.guild_b = DiscordGuild.objects.create(guild_id="930200", name="B")
        self.user = User.objects.create_user(username="cls", password="pw")
        self.user.profile.guilds.add(self.guild_a, self.guild_b)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)

    def test_player_settings_form_offers_guild_for_every_type(self):
        from the_warroom.forms import TournamentPlayerSettingsForm
        for cls in (Tournament.ClassificationTypes.LEAGUE,
                    Tournament.ClassificationTypes.TOURNAMENT,
                    Tournament.ClassificationTypes.GROUP):
            with self.subTest(classification=cls):
                t = Tournament.objects.create(name=f"T {cls}", classification=cls)
                form = TournamentPlayerSettingsForm(instance=t)
                self.assertIn('guild', form.fields)

    def test_non_admin_can_change_the_guild_on_a_tournament(self):
        """Previously the view silently reverted this for non-League types."""
        from the_warroom.forms import TournamentDynamicUpdateForm
        t = Tournament.objects.create(
            name="Cls Cup", guild=self.guild_a,
            classification=Tournament.ClassificationTypes.TOURNAMENT)
        form = TournamentDynamicUpdateForm(instance=t, user=self.user)
        self.assertIn(self.guild_b, form.fields['guild'].queryset)

    def test_switching_classification_keeps_the_guild(self):
        t = Tournament.objects.create(
            name="Keep Cup", guild=self.guild_a,
            classification=Tournament.ClassificationTypes.LEAGUE)
        t.classification = Tournament.ClassificationTypes.GROUP
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.guild, self.guild_a)


class ResultsChannelAnnounceTests(TestCase):
    """A submitted game is announced in the series' results_channel, naming whoever
    recorded it. Unit-level: the announce block's message + guard, without driving the
    whole record-game form."""

    CHANNEL = "200000000000000011"
    TEXT = [{"id": CHANNEL, "name": "results"}]

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)
        self.guild = DiscordGuild.objects.create(guild_id="940100", name="Res Guild",
                                                 bot_member=True)
        self.recorder = Profile.objects.create(discord="rec", display_name="Recorder Rita")
        self.tournament = Tournament.objects.create(
            name="Res Cup", guild=self.guild, results_channel=self.CHANNEL)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)

    def _post(self, message):
        from the_databot import tasks
        from the_warroom.services.channel_posts import post_to_tournament_channel
        with mock.patch("the_databot.services.discordservice.get_guild_text_channels",
                        return_value=self.TEXT), \
             mock.patch.object(tasks.post_channel_message_task, "delay") as delay:
            sent = post_to_tournament_channel(self.tournament, 'results_channel', message)
        return sent, delay

    def test_message_names_the_recorder_and_links_the_game(self):
        msg = (f'Game recorded by {self.recorder.name}. '
               f'See results [here](https://example.com/game/1/).')
        sent, delay = self._post(msg)
        self.assertTrue(sent)
        content = delay.call_args.args[1]
        self.assertIn("Game recorded by Recorder Rita.", content)
        self.assertIn("See results [here]", content)
        # A plain name, never a mention: these posts set no allowed_mentions.
        self.assertNotIn("<@", content)

    def test_profile_name_falls_back_when_no_display_name(self):
        bare = Profile.objects.create(discord="justdiscord")
        self.assertEqual(bare.name, "justdiscord")

    def test_no_results_channel_means_no_post(self):
        Tournament.objects.filter(pk=self.tournament.pk).update(results_channel=None)
        self.tournament.refresh_from_db()
        sent, delay = self._post("anything")
        self.assertFalse(sent)
        delay.assert_not_called()


class ResultsChannelViewAnnounceTests(TestCase):
    """Every game recorded into a tournament with a results_channel announces
    there once -- match, LFG, or standalone -- and never again on a later edit.

    View-level counterpart to ResultsChannelAnnounceTests above, which exercises
    post_to_tournament_channel directly. The bug these cover was mode gating:
    the announce block used to live inside the `elif match_mode` branch, so LFG
    and standalone games never reached it at all.
    """

    CHANNEL = "200000000000000022"

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)
        user_logged_in.disconnect(user_logged_in_handler)

        self.guild = DiscordGuild.objects.create(guild_id="950100", name="Res Guild",
                                                 bot_member=True)
        self.user = User.objects.create_user(username="recorder", password="x")
        # Bind the profile once: `user.profile` re-queries on each attribute
        # access, so assigning through it and saving separately loses the change.
        self.profile = self.user.profile
        self.profile.discord = "recorder"
        # group "A" backs both the `admin` and `player` properties -- the latter
        # is what @player_required checks, so without it the view redirects to
        # onboarding and the form never runs.
        self.profile.group = "A"
        self.profile.player_onboard = True
        self.profile.save()
        self.profile.guilds.add(self.guild)

        self.tournament = Tournament.objects.create(
            name="Res Cup", guild=self.guild, results_channel=self.CHANNEL,
            is_active=True)
        self.stage = Stage.objects.create(tournament=self.tournament, name="S1",
                                          order=1, is_active=True)
        self.round = Round.objects.create(
            stage=self.stage, round_number=1, is_active=True,
            bracket_status=Round.BracketStatusChoices.FINALIZED)

        # Post.save() builds a designers list, so these need one to exist.
        _d = self.profile
        self.map = Map.objects.create(title="Autumn", clearings=12, designer=_d)
        self.deck = Deck.objects.create(title="Standard", card_total=54, designer=_d)
        # `animal` is required: Faction.save() derives a default picture from it.
        self.faction_a = Faction.objects.create(title="Marquise", type="M", reach=10,
                                                animal="cat", designer=_d)
        self.faction_b = Faction.objects.create(title="Eyrie", type="M", reach=7,
                                                animal="bird", designer=_d)

        self.opponent = Profile.objects.create(discord="opponent")
        self.client.force_login(self.user)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)
        user_logged_in.connect(user_logged_in_handler)

    def _payload(self, **extra):
        """A minimal valid two-player game submission."""
        data = {
            'platform': 'Tabletop Simulator',
            'type': 'Live',
            'map': self.map.pk,
            'deck': self.deck.pk,
            'round': self.round.pk,
            'final': 'True',
            'date_posted': '2026-01-01 12:00:00',
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-faction': self.faction_a.pk,
            'form-0-player': self.profile.pk,
            'form-0-score': '30',
            'form-0-win': 'on',
            'form-1-faction': self.faction_b.pk,
            'form-1-player': self.opponent.pk,
            'form-1-score': '20',
        }
        data.update(extra)
        return data

    def _seat(self, series, profile, seat_number):
        """Seat a profile in a match series. Match mode restricts the effort
        formset's player choices to seated participants (_get_match_profiles),
        so a match submission needs the full Profile -> TournamentPlayer ->
        StageParticipant -> MatchSeat chain to validate."""
        tp = TournamentPlayer.objects.create(profile=profile,
                                             tournament=self.tournament)
        sp = StageParticipant.objects.create(stage=self.stage, tournament_player=tp)
        return MatchSeat.objects.create(series=series, stage_participant=sp,
                                        seat_number=seat_number)

    def _record_committed(self, url, payload):
        """POST a game with every Discord path patched, running the on_commit
        callbacks -- they otherwise never fire inside TestCase's transaction."""
        with mock.patch('the_warroom.views.post_to_tournament_channel') as announce, \
             mock.patch('the_warroom.views.post_channel_message_task') as thread_post, \
             mock.patch('the_warroom.views.send_rich_discord_message_task'):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(url, payload)
        return resp, announce, thread_post

    def test_standalone_game_announces(self):
        """The case that never fired before: no match, no LFG thread, just a
        round that belongs to a tournament."""
        url = reverse('record-game-v2')
        resp, announce, _ = self._record_committed(url, self._payload())
        self.assertEqual(Game.objects.count(), 1)
        announce.assert_called_once()
        args = announce.call_args.args
        self.assertEqual(args[0], self.tournament)
        self.assertEqual(args[1], 'results_channel')
        self.assertIn('See results [here]', args[2])

    def test_game_outside_a_tournament_does_not_announce(self):
        """A round with no tournament resolves to None and is skipped."""
        orphan = Round.objects.create(round_number=9, is_active=True)
        url = reverse('record-game-v2')
        _, announce, _ = self._record_committed(
            url, self._payload(round=orphan.pk))
        announce.assert_not_called()

    def test_editing_a_final_game_does_not_repost(self):
        """The repost guard: `game_was_final` is read AFTER any rebinding, so a
        second submission against an already-final game stays silent."""
        url = reverse('record-game-v2')
        _, announce, _ = self._record_committed(url, self._payload())
        announce.assert_called_once()

        game = Game.objects.get()
        edit_url = reverse('game-update-v2', kwargs={'id': game.id})
        efforts = list(game.efforts.order_by('seat'))
        edit_payload = self._payload(**{
            'form-INITIAL_FORMS': '2',
            'form-0-id': efforts[0].pk,
            'form-1-id': efforts[1].pk,
            'nickname': 'renamed',
        })
        _, announce2, _ = self._record_committed(edit_url, edit_payload)
        announce2.assert_not_called()

    def test_resubmitting_to_a_match_that_already_has_a_final_game_does_not_repost(self):
        """The guard's real teeth. Posting to ?match=<id> WITHOUT an id rebinds
        `obj` to the match's existing game at views.py:1218 -- after
        `initial_game_status` was read off a blank Game() and frozen at False.
        A guard keyed on initial_game_status would see "not final -> final" and
        announce a second time; `game_was_final`, read after the rebinding, sees
        the game as already final and stays quiet.
        """
        series = MatchSeries.objects.create(round=self.round)
        match = Match.objects.create(round=self.round, series=series)
        self._seat(series, self.profile, 1)
        self._seat(series, self.opponent, 2)

        match_url = f"{reverse('record-game-v2')}?match={match.pk}"
        _, announce, _ = self._record_committed(match_url, self._payload(
            match_id=match.pk))
        announce.assert_called_once()

        match.refresh_from_db()
        self.assertIsNotNone(match.game_id, "match should now hold the game")
        game = match.game
        efforts = list(game.efforts.order_by('seat'))

        # Same URL, still no `id` in the path -- this is the rebinding path.
        # INITIAL_FORMS must match the saved efforts or the stale-submission
        # guard redirects before the view ever reaches the announce block.
        _, announce2, _ = self._record_committed(match_url, self._payload(
            match_id=match.pk,
            **{
                'form-INITIAL_FORMS': '2',
                'form-0-id': efforts[0].pk,
                'form-1-id': efforts[1].pk,
            }))
        announce2.assert_not_called()
        self.assertEqual(Game.objects.count(), 1, "must not have created a 2nd game")

    def test_lfg_game_announces_in_both_the_thread_and_the_results_channel(self):
        """LFG games used to reach only their own thread: the results-channel
        post sat behind `elif match_mode`, which the LFG branch short-circuited.
        Both messages must now go out -- they have different audiences."""
        role = GuildLFGRole.objects.create(guild=self.guild, name="TTS LFG",
                                           tournament=self.tournament)
        thread = LFGThread.objects.create(thread_id="300000000000000033",
                                          guild=self.guild, lfg_role=role,
                                          host=self.profile)
        thread.players.add(self.profile, self.opponent)

        url = f"{reverse('record-game-v2')}?lfg={thread.pk}"
        _, announce, thread_post = self._record_committed(
            url, self._payload(lfg_id=thread.pk))

        announce.assert_called_once()
        self.assertEqual(announce.call_args.args[0], self.tournament)
        self.assertEqual(announce.call_args.args[1], 'results_channel')
        # ...and the LFG thread still got its own post.
        thread_post.delay.assert_called_once()
        self.assertEqual(thread_post.delay.call_args.args[0], thread.thread_id)

    def test_match_game_announces_in_both_the_group_thread_and_results_channel(self):
        """The pre-existing match behaviour must survive the lift: one results
        post AND the group-thread post, not one at the expense of the other."""
        # _match_thread_id reads the URL off the series' PlayerGroup and only
        # accepts it when the guild in the URL is the tournament's own guild.
        group = PlayerGroup.objects.create(
            round=self.round,
            discord_thread=f"https://discord.com/channels/{self.guild.guild_id}"
                           f"/400000000000000044")
        series = MatchSeries.objects.create(round=self.round, player_group=group)
        match = Match.objects.create(round=self.round, series=series)
        self._seat(series, self.profile, 1)
        self._seat(series, self.opponent, 2)

        url = f"{reverse('record-game-v2')}?match={match.pk}"
        _, announce, thread_post = self._record_committed(
            url, self._payload(match_id=match.pk))

        announce.assert_called_once()
        thread_post.delay.assert_called_once()


class MatchLinkGameTests(TestCase):
    """The moderator-only 'link an existing game to a scheduled match' endpoint.

    Covers the three writes the link performs (link, roster sync, nickname), the
    unlink that reverses only the link, and the permission/state gates -- the
    endpoint is reachable independently of the button that opens it.
    """

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)
        user_logged_in.disconnect(user_logged_in_handler)

        self.user = User.objects.create_user(username="mod", password="x")
        self.profile = self.user.profile
        self.profile.discord = "mod"
        # Group "A" backs both `admin` and `player`; the endpoint's gate mirrors
        # can_manage, which requires profile.player as well as has_permission.
        self.profile.group = "A"
        self.profile.player_onboard = True
        self.profile.save()

        self.tournament = Tournament.objects.create(name="Link Cup", is_active=True)
        self.stage = Stage.objects.create(tournament=self.tournament, name="S1",
                                          order=1, is_active=True)
        self.round = Round.objects.create(
            stage=self.stage, round_number=1, is_active=True,
            bracket_status=Round.BracketStatusChoices.FINALIZED)

        _d = self.profile
        self.map = Map.objects.create(title="Autumn", clearings=12, designer=_d)
        self.deck = Deck.objects.create(title="Standard", card_total=54, designer=_d)
        self.faction_a = Faction.objects.create(title="Marquise", type="M", reach=10,
                                                animal="cat", designer=_d)
        self.faction_b = Faction.objects.create(title="Eyrie", type="M", reach=7,
                                                animal="bird", designer=_d)

        self.group = PlayerGroup.objects.create(round=self.round, group_number=1,
                                                name="Group A")
        self.series = MatchSeries.objects.create(
            round=self.round, player_group=self.group, number_of_games=1)
        self.match = Match.objects.create(round=self.round, series=self.series)

        self.p1 = Profile.objects.create(discord="alice")
        self.p2 = Profile.objects.create(discord="bob")
        self._seat(self.p1, 1)
        self._seat(self.p2, 2)

        self.client.force_login(self.user)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)
        user_logged_in.connect(user_logged_in_handler)

    def _seat(self, profile, seat_number):
        tp = TournamentPlayer.objects.create(profile=profile,
                                             tournament=self.tournament)
        sp = StageParticipant.objects.create(stage=self.stage, tournament_player=tp)
        return MatchSeat.objects.create(series=self.series, stage_participant=sp,
                                        seat_number=seat_number)

    def _game(self, players, final=True):
        """A recorded game in the match's round that no match has claimed."""
        game = Game.objects.create(round=self.round, map=self.map, deck=self.deck,
                                   final=final)
        for i, (profile, faction) in enumerate(players, start=1):
            Effort.objects.create(game=game, player=profile, faction=faction,
                                  seat=i, win=(i == 1))
        return game

    def _url(self):
        return reverse('match-link-game', kwargs={'match_id': self.match.pk})

    def _link(self, game):
        return self.client.post(self._url(),
                                data=json.dumps({'game_id': game.pk}),
                                content_type='application/json')

    # --- GET: candidates ---

    def test_lists_unlinked_games_in_the_round(self):
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        body = json.loads(self.client.get(self._url()).content)
        self.assertTrue(body['success'])
        self.assertEqual([g['id'] for g in body['games']], [game.pk])

    def test_exact_roster_game_sorts_first(self):
        """The game whose players equal the seats is nearly always the wanted one,
        so it must outrank a newer game that merely overlaps."""
        self._game([(self.p1, self.faction_a)])          # partial, newer
        exact = self._game([(self.p1, self.faction_a),
                            (self.p2, self.faction_b)])
        body = json.loads(self.client.get(self._url()).content)
        self.assertEqual(body['games'][0]['id'], exact.pk)
        self.assertTrue(body['games'][0]['exact_roster'])

    def test_excludes_games_from_other_rounds_and_linked_games(self):
        other_round = Round.objects.create(
            stage=self.stage, round_number=2,
            bracket_status=Round.BracketStatusChoices.FINALIZED)
        Game.objects.create(round=other_round, map=self.map, deck=self.deck)

        taken = self._game([(self.p1, self.faction_a)])
        other_match = Match.objects.create(round=self.round, series=self.series,
                                           game=taken)
        self.assertTrue(other_match.pk)

        body = json.loads(self.client.get(self._url()).content)
        self.assertEqual(body['games'], [])

    def test_get_is_allowed(self):
        """Regression: the sibling endpoint is POST-only, and copying its
        decorator would 405 the candidate fetch."""
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    # --- GET: rendered candidate cards ---

    def _card(self, game_id=None):
        """The rendered HTML for one candidate (the first, unless given an id)."""
        body = json.loads(self.client.get(self._url()).content)
        for row in body['games']:
            if game_id is None or row['id'] == game_id:
                return row['html']
        self.fail(f'game {game_id} not among the candidates')

    def test_card_shows_faction_icons_and_the_winner_laurel(self):
        """The point of the card: each player's faction icon, with winner.png laid
        over the winner's."""
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        html = self._card(game.pk)

        self.assertIn(self.faction_a.small_icon.url, html)
        self.assertIn(self.faction_b.small_icon.url, html)
        self.assertIn('winner.png', html)
        # _game() marks only the first effort as the winner, so exactly one laurel.
        self.assertEqual(html.count('winner.png'), 1)
        self.assertIn(self.p1.display_name, html)
        self.assertIn(self.p2.display_name, html)

    def test_card_skips_an_effort_with_no_faction(self):
        """Effort.faction is nullable and cache_bust returns '' for an empty field,
        which would render a broken image."""
        game = Game.objects.create(round=self.round, map=self.map, deck=self.deck,
                                   final=True)
        Effort.objects.create(game=game, player=self.p1, faction=self.faction_a,
                              seat=1, win=True)
        Effort.objects.create(game=game, player=self.p2, faction=None, seat=2)

        html = self._card(game.pk)
        self.assertNotIn('src=""', html)
        self.assertIn(self.p1.display_name, html)
        # The factionless row is dropped entirely rather than rendered iconless.
        self.assertNotIn(self.p2.display_name, html)

    def test_card_shows_the_date_and_only_shows_a_nickname_when_set(self):
        """A nickname-less game gets the date alone -- no invented placeholder
        title, which would read identically on every card."""
        plain = self._game([(self.p1, self.faction_a)])
        self.assertEqual(plain.nickname, None)
        html = self._card(plain.pk)
        self.assertIn(plain.date_posted.strftime('%b'), html)

        plain.nickname = 'Semifinal rematch'
        plain.save(update_fields=['nickname'])
        html = self._card(plain.pk)
        self.assertIn('Semifinal rematch', html)
        self.assertIn(plain.date_posted.strftime('%b'), html)

    def test_card_marks_the_exact_roster_match_and_drafts(self):
        exact = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        self.assertIn('Roster match', self._card(exact.pk))

        draft = self._game([(self.p1, self.faction_a)], final=False)
        draft_html = self._card(draft.pk)
        self.assertIn('Draft', draft_html)
        self.assertNotIn('Roster match', draft_html)

    def test_card_escapes_user_entered_text(self):
        """Nicknames and display names are user-entered; the template autoescapes
        them, which is what replaced the JS textContent handling."""
        game = self._game([(self.p1, self.faction_a)])
        game.nickname = '<script>alert(1)</script>'
        game.save(update_fields=['nickname'])

        html = self._card(game.pk)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_candidate_cards_render_without_extra_queries(self):
        """The cards must render entirely from prefetched data.

        Faction is multi-table inheritance, so an unprefetched effort.faction costs
        a SELECT on the_keep_post per effort. Measured around the RENDER only --
        counting the whole helper would bury 4 extra queries under the candidate
        queryset's own cost, and counting a full request would add context-processor
        queries that move when the fixture creates Profiles.
        """
        # Distinct factions per seat: reusing two factions lets Django's per-queryset
        # instance cache dedupe the lookups, hiding the N+1 entirely.
        seats = []
        for i in range(4):
            profile = Profile.objects.create(discord=f"extra{i}")
            faction = Faction.objects.create(title=f"Extra {i}", type="M", reach=5,
                                             animal=f"beast{i}", designer=self.profile)
            seats.append((profile, faction))
        self._game(seats)

        games = list(
            Game.objects.filter(round_id=self.round.id, match__isnull=True)
            .prefetch_related(
                'efforts__player',
                Prefetch('efforts__faction',
                         queryset=Faction.objects.only(*Faction.LEADERBOARD_FIELDS))))

        with CaptureQueriesContext(connection) as ctx:
            for game in games:
                render_to_string('the_warroom/partials/link_game_candidate.html',
                                 {'game': game, 'exact_roster': False})

        self.assertEqual(
            len(ctx), 0,
            f'card rendering issued {len(ctx)} queries; it must read only '
            f'prefetched data')

    # --- POST: link ---

    def test_link_sets_game_status_nickname_and_roster(self):
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        response = self._link(game)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['success'])

        self.match.refresh_from_db()
        game.refresh_from_db()
        self.assertEqual(self.match.game_id, game.pk)
        self.assertEqual(self.match.status, CompetitionStatus.COMPLETED)
        # match.name is derived from the player group in Match.save(), and is what
        # record_game_v2.html posts as the nickname in match mode.
        self.assertEqual(game.nickname, self.match.name)
        self.assertEqual(game.nickname, "Group A")

    def test_link_returns_rerendered_card(self):
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        body = json.loads(self._link(game).content)
        self.assertEqual(body['series_id'], self.series.pk)
        self.assertIn('<div class="card', body['html'])

    def test_link_replaces_roster_with_the_games_players(self):
        """Seats mirror who actually played: a scheduled player who didn't play
        loses their seat, and a player who did gains one."""
        ringer = Profile.objects.create(discord="ringer")
        game = self._game([(self.p1, self.faction_a), (ringer, self.faction_b)])
        self._link(game)

        seated = set(MatchSeat.objects
                     .filter(series=self.series)
                     .values_list('stage_participant__tournament_player__profile_id',
                                  flat=True))
        self.assertEqual(seated, {self.p1.pk, ringer.pk})

    def test_link_registers_players_missing_from_the_tournament(self):
        outsider = Profile.objects.create(discord="outsider")
        game = self._game([(self.p1, self.faction_a), (outsider, self.faction_b)])
        self._link(game)

        tp = TournamentPlayer.objects.filter(tournament=self.tournament,
                                             profile=outsider).first()
        self.assertIsNotNone(tp)
        self.assertTrue(StageParticipant.objects.filter(
            stage=self.stage, tournament_player=tp).exists())

    def test_link_dedupes_a_player_holding_two_efforts(self):
        """Nothing constrains Effort.player to be unique within a game (coalition
        and coop games seat one profile twice), which would otherwise create two
        MatchSeat rows for the same person."""
        game = Game.objects.create(round=self.round, map=self.map, deck=self.deck,
                                   final=True)
        Effort.objects.create(game=game, player=self.p1, faction=self.faction_a,
                              seat=1, win=True)
        Effort.objects.create(game=game, player=self.p1, faction=self.faction_b,
                              seat=2)
        self._link(game)

        self.assertEqual(MatchSeat.objects.filter(series=self.series).count(), 1)

    def test_linking_a_draft_game_leaves_the_match_active(self):
        game = self._game([(self.p1, self.faction_a)], final=False)
        self._link(game)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, CompetitionStatus.ACTIVE)

    def test_link_rejects_a_match_that_already_has_a_game(self):
        first = self._game([(self.p1, self.faction_a)])
        self._link(first)
        second = self._game([(self.p2, self.faction_b)])

        response = self._link(second)
        self.assertEqual(response.status_code, 400)
        self.match.refresh_from_db()
        self.assertEqual(self.match.game_id, first.pk)

    def test_link_rejects_a_game_in_another_round(self):
        other_round = Round.objects.create(
            stage=self.stage, round_number=2,
            bracket_status=Round.BracketStatusChoices.FINALIZED)
        game = Game.objects.create(round=other_round, map=self.map, deck=self.deck)
        self.assertEqual(self._link(game).status_code, 400)

    # --- POST: unlink ---

    def _unlink(self):
        return self.client.post(self._url(),
                                data=json.dumps({'action': 'unlink'}),
                                content_type='application/json')

    def test_unlink_clears_the_link_but_keeps_nickname_and_roster(self):
        ringer = Profile.objects.create(discord="ringer")
        game = self._game([(self.p1, self.faction_a), (ringer, self.faction_b)])
        self._link(game)

        self.assertEqual(self._unlink().status_code, 200)

        self.match.refresh_from_db()
        game.refresh_from_db()
        self.assertIsNone(self.match.game_id)
        self.assertEqual(self.match.status, CompetitionStatus.PENDING)
        # Deliberately NOT reverted -- link kept no "before" state to restore.
        self.assertEqual(game.nickname, "Group A")
        seated = set(MatchSeat.objects
                     .filter(series=self.series)
                     .values_list('stage_participant__tournament_player__profile_id',
                                  flat=True))
        self.assertEqual(seated, {self.p1.pk, ringer.pk})

    def test_unlink_keeps_the_match_name(self):
        """Match.save() rewrites self.name from the player group regardless of
        update_fields, so the unlink must restrict the fields it saves."""
        game = self._game([(self.p1, self.faction_a)])
        self._link(game)
        original = Match.objects.get(pk=self.match.pk).name

        self._unlink()
        self.assertEqual(Match.objects.get(pk=self.match.pk).name, original)

    def test_unlink_clears_series_winners(self):
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        self._link(game)
        self.assertTrue(MatchSeries.objects.get(pk=self.series.pk).winners.exists())

        self._unlink()
        series = MatchSeries.objects.get(pk=self.series.pk)
        self.assertFalse(series.winners.exists())
        self.assertEqual(series.status, CompetitionStatus.PENDING)

    def test_unlink_reopens_a_completed_round_and_stage(self):
        """reevaluate_round_status never reopens a COMPLETED round, which is the
        opposite of what unlinking means -- reopen_round is the correct call."""
        game = self._game([(self.p1, self.faction_a), (self.p2, self.faction_b)])
        self._link(game)
        self.round.refresh_from_db()
        self.assertEqual(self.round.status, CompetitionStatus.COMPLETED)

        self._unlink()
        self.round.refresh_from_db()
        self.stage.refresh_from_db()
        self.assertEqual(self.round.status, CompetitionStatus.ACTIVE)
        self.assertEqual(self.stage.status, CompetitionStatus.ACTIVE)

    def test_unlink_leaves_a_multi_game_series_active(self):
        """A best-of series whose other games are still recorded stays Active,
        not Pending -- the other_completed branch."""
        self.series.number_of_games = 3
        self.series.save(update_fields=['number_of_games'])
        sibling = Match.objects.create(round=self.round, series=self.series,
                                       status=CompetitionStatus.COMPLETED)
        self.assertTrue(sibling.pk)

        game = self._game([(self.p1, self.faction_a)])
        self._link(game)
        self._unlink()

        self.assertEqual(MatchSeries.objects.get(pk=self.series.pk).status,
                         CompetitionStatus.ACTIVE)

    def test_unlink_rejects_a_match_with_no_game(self):
        self.assertEqual(self._unlink().status_code, 400)

    def test_unlink_relists_the_game_as_a_candidate(self):
        game = self._game([(self.p1, self.faction_a)])
        self._link(game)
        self._unlink()

        body = json.loads(self.client.get(self._url()).content)
        self.assertEqual([g['id'] for g in body['games']], [game.pk])

    # --- Gates ---

    def test_non_moderator_is_refused(self):
        other = User.objects.create_user(username="rando", password="x")
        p = other.profile
        p.group = "P"          # player, but not a tournament moderator
        p.player_onboard = True
        p.save()
        self.client.force_login(other)

        self.assertEqual(self.client.get(self._url()).status_code, 403)
        game = self._game([(self.p1, self.faction_a)])
        self.assertEqual(self._link(game).status_code, 403)

    def test_unfinalized_bracket_is_refused(self):
        self.round.bracket_status = Round.BracketStatusChoices.DRAFT
        self.round.save(update_fields=['bracket_status'])
        game = self._game([(self.p1, self.faction_a)])
        self.assertEqual(self._link(game).status_code, 400)


class BoxScoreImportNormalizeTests(TestCase):
    """`normalize_turns` -- the one place the wire format's turn entries are
    interpreted, shared by the JSON upload and the LFG prefill."""

    def test_cumulative_scores_pass_through(self):
        cells, notes = normalize_turns([
            {'turn': 1, 'score': 3},
            {'turn': 2, 'score': 8, 'dominance': True},
        ])
        self.assertEqual(cells, [
            {'turn': 1, 'value': 3, 'dominance': False},
            {'turn': 2, 'value': 8, 'dominance': True},
        ])
        self.assertEqual(notes, [])

    def test_missing_turns_backfill_with_the_previous_total(self):
        # A gap means the score didn't move -- the grid needs a contiguous run
        # from turn 1, matching the server's Phase-1 backfill.
        cells, _ = normalize_turns([{'turn': 1, 'score': 5}, {'turn': 4, 'score': 20}])
        self.assertEqual([c['value'] for c in cells], [5, 5, 5, 20])

    def test_delta_keys_accumulate_into_a_running_total(self):
        cells, notes = normalize_turns([
            {'turn': 1, 'generic_points': 3},
            {'turn': 2, 'battle_points': 5, 'generic_points': 1},
        ])
        self.assertEqual([c['value'] for c in cells], [3, 9])
        # Category detail can't be shown by the V2 grid, so say so.
        self.assertEqual(len(notes), 1)
        self.assertIn('collapsed', notes[0])

    def test_a_turns_data_row_pastes_in_unchanged(self):
        # ScoreCard.turns_data carries BOTH a cumulative game_points_total and
        # per-turn deltas; the cumulative key must win.
        cells, _ = normalize_turns([
            {'turn_number': 1, 'total_points': 4, 'battle_points': 4,
             'game_points_total': 4, 'dominance': False},
            {'turn_number': 2, 'total_points': 6, 'battle_points': 6,
             'game_points_total': 10, 'dominance': True},
        ])
        self.assertEqual([c['value'] for c in cells], [4, 10])
        self.assertTrue(cells[1]['dominance'])

    def test_empty_and_missing_turns_are_not_errors(self):
        self.assertEqual(normalize_turns(None)[0], [])
        self.assertEqual(normalize_turns([])[0], [])

    def test_malformed_turns_are_rejected(self):
        for bad in ([{'turn': 0, 'score': 1}],
                    [{'turn': 1, 'score': 'abc'}],
                    [{'score': 5}],
                    [{'turn': 99, 'score': 1}],
                    'not-a-list'):
            with self.assertRaises(BoxScoreImportError):
                normalize_turns(bad)

    def test_duplicate_seat_numbers_are_rejected(self):
        with self.assertRaises(BoxScoreImportError):
            validate_participants([{'turn_order': 1}, {'turn_order': 1}])


class BoxScoreImportParseTests(TestCase):
    def test_a_bare_participants_list_is_accepted(self):
        # So a thread's stored turns_data can be pasted in directly.
        payload = parse_box_score_json(json.dumps([{'turn_order': 1}]))
        self.assertEqual(payload['participants'], [{'turn_order': 1}])

    def test_unusable_files_report_why(self):
        for raw, fragment in [('', 'empty'),
                              ('not json{', 'valid JSON'),
                              ('{"title": "x"}', 'participants')]:
            with self.assertRaises(BoxScoreImportError) as caught:
                parse_box_score_json(raw)
            self.assertIn(fragment, str(caught.exception))


class BoxScoreImportResolveTests(TestCase):
    """Resolution against the querysets the form's dropdowns are built from.

    That is the whole legality mechanism: a faction outside the supplied
    queryset simply doesn't resolve, so no separate "is this allowed" rule
    exists here to drift from the three that already govern the form.
    """

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)
        self.designer = Profile.objects.create(discord="designer")
        self.marquise = Faction.objects.create(title="Marquise", type="M", reach=10,
                                               animal="cat", designer=self.designer)
        self.eyrie = Faction.objects.create(title="Eyrie Dynasties", type="M", reach=7,
                                            animal="bird", designer=self.designer)
        self.vagabond_faction = Faction.objects.create(
            title="Vagabond", type="M", reach=5, animal="vagabond", designer=self.designer)
        self.ranger = Vagabond.objects.create(title="Ranger", animal="Fox",
                                              designer=self.designer)
        self.deck = Deck.objects.create(title="Squires & Disciples", card_total=54,
                                        designer=self.designer)
        self.map = Map.objects.create(title="Autumn", clearings=12, designer=self.designer)
        self.player = Profile.objects.create(discord="alice")

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)

    def _buckets(self, factions=None, players=None):
        return {
            'factions': factions if factions is not None else Faction.objects.all(),
            'maps': Map.objects.all(),
            'decks': Deck.objects.all(),
            'vagabonds': Vagabond.objects.all(),
            'captains': Vagabond.objects.filter(captain=True),
            'landmarks': Landmark.objects.all(),
            'tweaks': Tweak.objects.all(),
            'hirelings': Hireling.objects.all(),
        }

    def _resolve(self, participants, *, factions=None, players=None, **kwargs):
        kwargs.setdefault('seat_limit', None)
        kwargs.setdefault('locked_indices', ())
        kwargs.setdefault('game_data', {})
        kwargs.setdefault('allow_game_fields', False)
        return resolve_import(
            participants,
            option_querysets=self._buckets(factions=factions),
            player_queryset=players if players is not None else Profile.objects.all(),
            **kwargs)

    def test_slugs_resolve_to_primary_keys(self):
        result = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                 'player': self.player.slug}])
        self.assertEqual(result.seats[0]['form_index'], 0)
        self.assertEqual(result.seats[0]['fields']['faction'], self.marquise.pk)
        self.assertEqual(result.seats[0]['fields']['player'], self.player.pk)
        self.assertEqual(result.skipped, [])

    def test_a_faction_outside_the_allowed_set_is_skipped_not_selected(self):
        result = self._resolve(
            [{'turn_order': 1, 'faction': self.marquise.slug,
              'turns': [{'turn': 1, 'score': 7}]}],
            factions=Faction.objects.filter(pk=self.eyrie.pk))
        self.assertNotIn('faction', result.seats[0]['fields'])
        self.assertIn('not playable here', result.skipped[0])
        # The rest of the seat still imports.
        self.assertEqual(result.seats[0]['cells'][0]['value'], 7)

    def test_an_unknown_slug_reads_differently_from_a_disallowed_one(self):
        result = self._resolve([{'turn_order': 1, 'faction': 'no-such-faction'}])
        self.assertIn('no faction matching', result.skipped[0])

    def test_a_player_off_the_roster_is_skipped(self):
        result = self._resolve([{'turn_order': 1, 'player': self.player.slug}],
                               players=Profile.objects.none())
        self.assertIn('not available for this game', result.skipped[0])

    def test_tournament_score_above_zero_marks_a_sub_30_win(self):
        # Without this a dominance/coalition/timed win would import with no
        # winner: the form only auto-checks Win at 30+.
        result = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                 'tournament_score': 1,
                                 'turns': [{'turn': 1, 'score': 11}]}])
        self.assertIs(result.seats[0]['fields']['win'], True)

    def test_a_coalition_half_point_still_counts_as_a_win(self):
        result = self._resolve([{'turn_order': 1, 'tournament_score': 0.5}])
        self.assertIs(result.seats[0]['fields']['win'], True)

    def test_a_zero_tournament_score_is_a_loss(self):
        result = self._resolve([{'turn_order': 1, 'tournament_score': 0}])
        self.assertIs(result.seats[0]['fields']['win'], False)

    def test_brazen_demagogue_needs_a_dominance(self):
        # Nothing else on this seat resolves, so it contributes no row at all --
        # only the reason it was dropped.
        result = self._resolve([{'turn_order': 1, 'brazen_demagogue': True}])
        self.assertEqual(result.seats, [])
        self.assertIn('needs a dominance', result.skipped[0])

    def test_brazen_without_dominance_leaves_the_rest_of_the_seat_intact(self):
        result = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                 'brazen_demagogue': True}])
        self.assertNotIn('brazen_demagogue', result.seats[0]['fields'])
        self.assertEqual(result.seats[0]['fields']['faction'], self.marquise.pk)

    def test_brazen_demagogue_survives_an_unset_deck(self):
        # The deck may not be chosen yet. The save path clears an invalid value,
        # so resolving it here loses nothing and keeps the data alive.
        result = self._resolve([{'turn_order': 1, 'dominance': 'Fox',
                                 'brazen_demagogue': True}])
        self.assertIs(result.seats[0]['fields']['brazen_demagogue'], True)

    def test_a_wrong_deck_keeps_brazen_but_warns(self):
        result = self._resolve(
            [{'turn_order': 1, 'dominance': 'Fox', 'brazen_demagogue': True}],
            game_data={'_current_deck_title': 'Base'})
        self.assertIs(result.seats[0]['fields']['brazen_demagogue'], True)
        self.assertTrue(any('will be cleared' in n for n in result.notes))

    def test_dependent_fields_follow_their_faction(self):
        # A vagabond only applies to the Vagabond faction, mirroring
        # EffortCreateForm.clean, so the importer can't build a rejected state.
        result = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                 'vagabond': self.ranger.slug}])
        self.assertNotIn('vagabond', result.seats[0]['fields'])
        self.assertIn('Vagabond faction', result.skipped[0])

        ok = self._resolve([{'turn_order': 1, 'faction': self.vagabond_faction.slug,
                             'vagabond': self.ranger.slug}])
        self.assertEqual(ok.seats[0]['fields']['vagabond'], self.ranger.pk)

    def test_starting_leader_only_applies_to_the_eyrie(self):
        result = self._resolve([{'turn_order': 1, 'faction': self.eyrie.slug,
                                 'starting_leader': 'Despot'}])
        self.assertEqual(result.seats[0]['fields']['starting_leader'], 'Despot')

        wrong = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                'starting_leader': 'Despot'}])
        self.assertNotIn('starting_leader', wrong.seats[0]['fields'])

    def test_an_unknown_dominance_is_rejected(self):
        result = self._resolve([{'turn_order': 1, 'faction': self.marquise.slug,
                                 'dominance': 'Platypus'}])
        self.assertNotIn('dominance', result.seats[0]['fields'])
        self.assertIn('unknown dominance', result.skipped[0].lower())

    def test_seats_past_the_limit_are_dropped(self):
        result = self._resolve(
            [{'turn_order': 1, 'faction': self.marquise.slug},
             {'turn_order': 5, 'faction': self.marquise.slug}],
            seat_limit=4)
        self.assertEqual([s['form_index'] for s in result.seats], [0])
        self.assertIn('only has 4 seats', result.skipped[0])

    def test_a_locked_row_keeps_its_scorecard(self):
        # A detailed scorecard is server-owned; the file's turns must not touch it.
        result = self._resolve(
            [{'turn_order': 1, 'faction': self.marquise.slug,
              'turns': [{'turn': 1, 'score': 5}]}],
            locked_indices={0})
        self.assertEqual(result.seats[0]['cells'], [])
        self.assertIn('locked', result.skipped[0])

    def test_game_fields_resolve_and_lowercased_timing_maps_back(self):
        result = self._resolve(
            [{'turn_order': 1}],
            allow_game_fields=True,
            game_data={'board_map': self.map.slug, 'deck': self.deck.slug,
                       'title': 'Round 3', 'random_suits': True,
                       'turn_timing': 'live'})
        self.assertEqual(result.game['map'], self.map.pk)
        self.assertEqual(result.game['deck'], self.deck.pk)
        self.assertEqual(result.game['nickname'], 'Round 3')
        self.assertIs(result.game['random_clearing'], True)
        # The API lowercases Game.type on the way out.
        self.assertEqual(result.game['type'], 'Live')

    def test_a_tournament_in_the_file_is_reported_not_applied(self):
        # The round comes from how the form was opened; importing an exported
        # game must never silently reassign it.
        result = self._resolve([{'turn_order': 1}], allow_game_fields=True,
                               game_data={'tournament': [{'round': 'r1'}]})
        self.assertNotIn('round', result.game)
        self.assertTrue(any('round' in n for n in result.notes))


class LFGThreadTurnsDataTests(TestCase):
    """`LFGThread.turns_data` -- storage for a thread's box score, and the
    validation that keeps the record form able to trust it."""

    def setUp(self):
        post_save.disconnect(handle_image_resize, sender=Profile)

    def tearDown(self):
        post_save.connect(handle_image_resize, sender=Profile)

    def test_a_malformed_box_score_is_refused_on_clean(self):
        thread = LFGThread(thread_id="t-bad")
        thread.turns_data = [{'turn_order': 1, 'turns': [{'turn': 0, 'score': 5}]}]
        with self.assertRaises(ValidationError) as caught:
            thread.clean()
        self.assertIn('turns_data', caught.exception.message_dict)

    def test_a_valid_box_score_passes(self):
        thread = LFGThread(thread_id="t-ok")
        thread.turns_data = [{'turn_order': 1, 'turns': [{'turn': 1, 'score': 5}]}]
        thread.clean()   # must not raise

    def test_the_field_defaults_to_empty(self):
        thread = LFGThread.objects.create(thread_id="t-empty")
        self.assertEqual(thread.turns_data, [])
        thread.clean()
