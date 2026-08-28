from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import TestCase
from django.urls import reverse

from the_gatehouse.models import (
    DiscordGuild, Profile, LFGThread, LFGSeat, LFGDraft, LFGDraftPick,
)
from the_gatehouse.services.lfg_game import lfg_option_querysets
from the_keep.models import Faction, StatusChoices, Vagabond
from the_gatehouse.signals import handle_image_resize
from the_warroom.forms import GameCreateForm
from the_warroom.models import (
    Effort, Game, Match, MatchSeries, Round, Stage, Tournament,
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
        self.assertEqual(
            self.system.get_absolute_url(),
            reverse('elo-system-leaderboard-page', args=[self.system.slug]),
        )

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
