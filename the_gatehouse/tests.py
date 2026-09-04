"""Tests for the_gatehouse: site concerns, Discord OAuth login, and webhooks.

The bot's own tests (slash commands, LFG, schedule polls, embeds) live in
the_databot/tests.py alongside the code they exercise.
"""
import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from PIL import Image
from celery.exceptions import Retry
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.core.management import call_command
from django.db import transaction
from django.http import HttpResponse
from django.template import Context, Template
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError

from the_keep.models import Faction, StatusChoices
from the_warroom.models import Game, Effort
from the_gatehouse.models import (DiscordGuild, Profile, DEFAULT_PROFILE_IMAGE,
                                  GUILDS_REFRESH_MAX_AGE)
from the_gatehouse import views
from the_gatehouse.signals import user_logged_in_handler
from the_gatehouse.services.discord_oauth import update_discord_avatar
from the_gatehouse.tasks import update_post_status


class _NoLoginSignalMixin:
    """force_login fires user_logged_in, whose handler builds absolute URLs from the
    request and enqueues Discord work — neither of which a bare test request supports.
    None of that is under test here, so disconnect it for the duration.

    Duplicated from the_databot/tests.py rather than shared: both suites need it and
    neither app should import the other's tests."""

    def setUp(self):
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        super().setUp()


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
        # The handler now consults discord_refresh_capability before raising the flag or
        # enqueuing. These users have no SocialAccount, so the real predicate returns
        # 'no_account' and nothing would be enqueued at all. Tests that specifically
        # exercise the no-capability path override this locally.
        # Patched where signals BOUND it (module-level import at signals.py:22), not at
        # its definition -- tasks.py imports it inside the function body, so that call
        # site is patched on the discord_oauth path instead.
        self.capability = mock.patch(
            'the_gatehouse.signals.discord_refresh_capability', return_value='ok')
        self.capability.start()
        self.addCleanup(self._stop_capability)

    def _stop_capability(self):
        """Idempotent: tests that stop the patch early to test the no-token path."""
        try:
            self.capability.stop()
        except RuntimeError:
            pass
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
            'the_gatehouse.services.discord_oauth.get_user_guilds', return_value=guilds)
        name_p = mock.patch(
            'the_gatehouse.services.discord_oauth.get_discord_display_name',
            return_value=display_name)
        derive_p = mock.patch(
            'the_gatehouse.services.discord_oauth.derive_guild_membership',
            return_value=(bool(guilds), False, False))
        update_p = mock.patch(
            'the_gatehouse.services.discord_oauth.update_user_guilds')
        # refresh_user_guilds imports the predicate INSIDE the function, so it resolves
        # on the discord_oauth module -- separate from the signals-level patch in setUp
        # that gates whether the flag is raised at all. Both are needed.
        cap_p = mock.patch(
            'the_gatehouse.services.discord_oauth.discord_refresh_capability',
            return_value='ok')
        patches = (guilds_p, name_p, derive_p, update_p, cap_p)
        mocks = [p.start() for p in patches]
        for p in patches:
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
        # Change 4: a returning player keeps the background refresh but is never shown
        # the spinner -- they were never blocked on the result.
        self.assertFalse(self.profile.guilds_refreshing)

    def test_discord_failure_falls_back_to_async(self):
        """None means API failure: never demote, keep the spinner, hand off to Celery."""
        self._patch_discord(None)
        task = self._login()

        task.delay.assert_called_once_with(self.user.id)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, 'O')
        self.assertTrue(self.profile.guilds_refreshing)
        # A True flag with a NULL timestamp now reads as instantly stale, so the raise
        # must stamp it or the spinner it exists for never renders.
        self.assertIsNotNone(self.profile.guilds_refresh_started_at)

    def test_login_survives_discord_exception(self):
        """A Discord outage must never break login itself."""
        with mock.patch('the_gatehouse.services.discord_oauth.get_user_guilds',
                        side_effect=RuntimeError('discord down')):
            task = self._login()

        task.delay.assert_called_once_with(self.user.id)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.guilds_refreshing)
        self.assertIsNotNone(self.profile.guilds_refresh_started_at)

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
        # These users have no SocialAccount, so the real predicate returns 'no_account'
        # and every refresh below would short-circuit to NO_TOKEN before any HTTP.
        cap_p = mock.patch(
            'the_gatehouse.services.discord_oauth.discord_refresh_capability',
            return_value='ok')
        cap_p.start()
        self.addCleanup(cap_p.stop)

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
             mock.patch('the_gatehouse.services.discord_oauth.get_user_guilds',
                        side_effect=slow_guilds), \
             mock.patch('the_gatehouse.services.discord_oauth.update_user_guilds'), \
             mock.patch('the_gatehouse.services.discord_oauth.derive_guild_membership',
                        return_value=(True, False, False)), \
             mock.patch('the_gatehouse.services.discord_oauth.get_discord_display_name'
                        ) as name:
            ok = tasks.refresh_user_guilds(self.user, budget=6)

        # The group promotion still landed and was saved...
        self.assertIs(ok, tasks.GuildSyncResult.OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.group, 'P')
        # ...but the optional display-name lookup was skipped.
        name.assert_not_called()

    def test_exhausted_budget_before_first_call_is_transient(self):
        from the_gatehouse import tasks

        clock = self._fake_clock()
        clock.now = 99   # already past the deadline when we start

        with mock.patch.object(tasks.time, 'monotonic', clock), \
             mock.patch('the_gatehouse.services.discord_oauth.get_user_guilds'
                        ) as get_guilds:
            ok = tasks.refresh_user_guilds(self.user, budget=-1)

        # TRANSIENT, not NO_TOKEN: a spent budget is exactly what a retry fixes.
        self.assertIs(ok, tasks.GuildSyncResult.TRANSIENT)
        get_guilds.assert_not_called()

    def test_no_budget_means_no_timeout_override(self):
        """The async task path must keep the historical per-call 5s defaults."""
        from the_gatehouse import tasks

        with mock.patch('the_gatehouse.services.discord_oauth.get_user_guilds',
                        return_value=[]) as get_guilds, \
             mock.patch('the_gatehouse.services.discord_oauth.update_user_guilds'), \
             mock.patch('the_gatehouse.services.discord_oauth.derive_guild_membership',
                        return_value=(False, False, False)), \
             mock.patch('the_gatehouse.services.discord_oauth.get_discord_display_name',
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
        self.profile.guilds_refresh_started_at = timezone.now()
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])
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
        self.profile.guilds_refresh_started_at = timezone.now()
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])
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


class GuildsRefreshStalenessTests(TestCase):
    """guilds_refresh_in_progress expires a flag nothing ever cleared.

    This is the safety net the whole fix rests on: no sweep task, no admin action, no
    migration backfill -- a stranded profile simply reads as not-refreshing once the
    window passes.
    """

    def setUp(self):
        self.profile = User.objects.create_user(username='stale', password='pw').profile

    def test_a_fresh_flag_reads_as_in_progress(self):
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now() - timedelta(seconds=10)
        self.assertTrue(self.profile.guilds_refresh_in_progress)

    def test_an_old_flag_reads_as_stale(self):
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now() - timedelta(minutes=10)
        self.assertFalse(self.profile.guilds_refresh_in_progress)

    def test_a_flag_with_no_timestamp_reads_as_stale(self):
        """The production-recovery case: rows stranded BEFORE the timestamp field existed
        got NULL from the AddField default, so the migration alone freed them."""
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = None
        self.assertFalse(self.profile.guilds_refresh_in_progress)

    def test_a_cleared_flag_is_never_in_progress(self):
        self.profile.guilds_refreshing = False
        self.profile.guilds_refresh_started_at = timezone.now()
        self.assertFalse(self.profile.guilds_refresh_in_progress)


class RefreshUserGuildsTaskTerminalTests(TestCase):
    """Every terminal path in refresh_user_guilds_task clears the flag -- and only the
    terminal ones do. A pending retry must KEEP it, or the spinner drops while work is
    still queued."""

    def setUp(self):
        self.user = User.objects.create_user(username='terminal', password='pw')
        self.profile = self.user.profile
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now()
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])

    def test_a_no_token_user_terminates_instead_of_retrying(self):
        """Headline regression: strand route #1. A permanent failure must not be routed
        through the transient-retry path, where a deploy could drop it forever."""
        from the_gatehouse import tasks

        with mock.patch.object(tasks.refresh_user_guilds_task, 'retry') as retry, \
             mock.patch('the_gatehouse.tasks.refresh_user_guilds',
                        return_value=tasks.GuildSyncResult.NO_TOKEN):
            tasks.refresh_user_guilds_task(self.user.id)

        retry.assert_not_called()
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNone(self.profile.guilds_refresh_started_at)
        # A failed refresh must NOT claim a sync: guilds_synced_at gates needs_sync_now,
        # so stamping it here would suppress the inline retry on the next login.
        self.assertIsNone(self.profile.guilds_synced_at)

    def test_a_pending_retry_does_not_clear_the_flag(self):
        from the_gatehouse import tasks

        with mock.patch('the_gatehouse.tasks.refresh_user_guilds',
                        return_value=tasks.GuildSyncResult.TRANSIENT):
            with self.assertRaises(Retry):
                tasks.refresh_user_guilds_task(self.user.id)

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.guilds_refreshing)

    def test_a_queued_retry_re_stamps_the_timestamp(self):
        """Otherwise the retry ladder (30+60+90s) races GUILDS_REFRESH_MAX_AGE and the
        flag can age out mid-flight, dropping the spinner while work is still queued."""
        from the_gatehouse import tasks

        old = timezone.now() - timedelta(minutes=4)
        self.profile.guilds_refresh_started_at = old
        self.profile.save(update_fields=['guilds_refresh_started_at'])

        with mock.patch('the_gatehouse.tasks.refresh_user_guilds',
                        return_value=tasks.GuildSyncResult.TRANSIENT):
            with self.assertRaises(Retry):
                tasks.refresh_user_guilds_task(self.user.id)

        self.profile.refresh_from_db()
        self.assertGreater(self.profile.guilds_refresh_started_at, old)
        self.assertTrue(self.profile.guilds_refresh_in_progress)

    def test_exhausted_retries_clear_without_claiming_a_sync(self):
        from the_gatehouse import tasks

        task = tasks.refresh_user_guilds_task
        # Task.request is a read-only property backed by a stack; push_request is the
        # supported way to stage a request state for a direct (non-worker) call.
        task.push_request(retries=task.max_retries)
        self.addCleanup(task.pop_request)
        with mock.patch('the_gatehouse.tasks.refresh_user_guilds',
                        return_value=tasks.GuildSyncResult.TRANSIENT):
            task(self.user.id)

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNone(self.profile.guilds_refresh_started_at)
        self.assertIsNone(self.profile.guilds_synced_at)

    def test_an_unexpected_error_is_terminal_and_clears(self):
        from the_gatehouse import tasks

        with mock.patch('the_gatehouse.tasks.refresh_user_guilds',
                        side_effect=RuntimeError('boom')):
            tasks.refresh_user_guilds_task(self.user.id)

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)

    def test_a_profile_whose_user_cannot_be_loaded_is_cleared(self):
        """A bare return here would strand the flag with no task left to clear it."""
        from the_gatehouse import tasks

        # The profile row still points at the user; the task just can't load it.
        with mock.patch('django.contrib.auth.models.UserManager.get_queryset',
                        return_value=User.objects.none()):
            tasks.refresh_user_guilds_task(self.user.id)

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNone(self.profile.guilds_refresh_started_at)

    def test_a_deleted_user_detaches_its_profile_and_the_window_frees_it(self):
        """Profile.user is on_delete=SET_NULL, so deleting the user leaves a profile the
        orphan lookup can no longer find. The staleness window is what frees those --
        documented here so the dead-looking lookup above isn't mistaken for the fix."""
        user_id = self.user.id
        self.user.delete()

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.user_id)
        self.assertFalse(Profile.objects.filter(user_id=user_id).exists())
        # Still flagged in the DB, but it reads as stale once the window passes.
        self.assertTrue(self.profile.guilds_refreshing)
        self.profile.guilds_refresh_started_at = timezone.now() - timedelta(minutes=10)
        self.assertFalse(self.profile.guilds_refresh_in_progress)


class UnusableTokenReportTests(TestCase):
    """A Discord account whose token stopped working is a real fault and must surface --
    but at most once a day, since it would otherwise re-report on every single login."""

    def setUp(self):
        self.user = User.objects.create_user(username='revoked', password='pw')
        cache.clear()
        self.addCleanup(cache.clear)

    def _refresh(self, capability):
        from the_gatehouse import tasks
        with mock.patch(
            'the_gatehouse.services.discord_oauth.discord_refresh_capability',
            return_value=capability), \
             mock.patch('the_gatehouse.tasks.send_discord_message_task') as send:
            result = tasks.refresh_user_guilds(self.user)
        return result, send

    def test_a_revoked_token_is_reported(self):
        from the_gatehouse import tasks

        result, send = self._refresh('no_token')

        self.assertIs(result, tasks.GuildSyncResult.NO_TOKEN)
        send.delay.assert_called_once()
        self.assertEqual(send.delay.call_args.kwargs['category'], 'report')

    def test_an_admin_password_login_is_not_reported(self):
        """'no_account' is the ordinary ModelBackend login (Django admin), not a fault."""
        from the_gatehouse import tasks

        result, send = self._refresh('no_account')

        self.assertIs(result, tasks.GuildSyncResult.NO_TOKEN)
        send.delay.assert_not_called()

    def test_the_report_is_sent_at_most_once_a_day(self):
        """An alert that repeats on every login is an alert that gets muted."""
        _, first = self._refresh('no_token')
        _, second = self._refresh('no_token')

        first.delay.assert_called_once()
        second.delay.assert_not_called()


class LoginFlagIsRaisedOnlyWhenNeededTests(TestCase):
    """Change 4: the flag is raised only for users whose cached group can't be trusted
    AND whose refresh could actually start."""

    def setUp(self):
        self.user = User.objects.create_user(username='gatekeep', password='pw')
        self.profile = self.user.profile
        for target in ('send_discord_message_task', 'update_discord_avatar_task'):
            p = mock.patch(f'the_gatehouse.signals.{target}')
            p.start()
            self.addCleanup(p.stop)

    def _login(self, delay_side_effect=None):
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request._messages = FallbackStorage(request)
        with mock.patch('the_gatehouse.signals.refresh_user_guilds_task') as task:
            if delay_side_effect is not None:
                task.delay.side_effect = delay_side_effect
            with self.captureOnCommitCallbacks(execute=True):
                auth_login(request, self.user,
                           backend='django.contrib.auth.backends.ModelBackend')
        request.session.save()
        return task

    def test_an_admin_password_login_never_raises_the_flag(self):
        """Strand route #1: no SocialAccount means nothing can ever clear a flag, and the
        old code still enqueued a task that burned its whole 180s ladder finding out."""
        with mock.patch('the_gatehouse.signals.discord_refresh_capability',
                        return_value='no_account'), \
             mock.patch('the_gatehouse.tasks.send_discord_message_task') as send:
            task = self._login()

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNone(self.profile.guilds_refresh_started_at)
        task.delay.assert_not_called()
        send.delay.assert_not_called()

    def test_a_dead_broker_at_login_does_not_strand_anyone(self):
        """The flag commits True before the post-commit enqueue runs. Without the catch,
        a Redis outage strands every user who logs in during it -- and 500s the login."""
        with mock.patch('the_gatehouse.signals.discord_refresh_capability',
                        return_value='ok'), \
             mock.patch('the_gatehouse.signals.refresh_user_guilds',
                        return_value=None):
            self._login(delay_side_effect=KombuOperationalError('redis down'))

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.guilds_refreshing)
        self.assertIsNone(self.profile.guilds_refresh_started_at)

    def test_a_discord_id_resolved_on_a_no_sync_login_still_persists(self):
        """dirty_fields can now be empty, and Django SKIPS a save() with an empty
        update_fields -- which would silently drop this write."""
        self.profile.group = 'P'
        self.profile.guilds_synced_at = timezone.now()   # needs_sync_now is False
        self.profile.save(update_fields=['group', 'guilds_synced_at'])

        with mock.patch('the_gatehouse.signals.discord_refresh_capability',
                        return_value='ok'), \
             mock.patch('the_gatehouse.signals.get_discord_id', return_value='4242'):
            self._login()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.discord_id, '4242')
        self.assertFalse(self.profile.guilds_refreshing)


class GuildsRefreshingPollerRenderTests(TestCase):
    """The poller must disappear for a stale flag -- this is what protects the E/P cohort
    from an unbounded every-2s XHR on every page."""

    TEMPLATE = "{% include 'includes/_guilds_refreshing_poller.html' %}"

    def setUp(self):
        self.user = User.objects.create_user(username='poller', password='pw')
        self.profile = self.user.profile

    def _render(self):
        return Template(self.TEMPLATE).render(Context({'user': self.user}))

    def test_the_poller_renders_for_a_fresh_flag(self):
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now()
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])
        self.assertIn('/profile/guilds-status/', self._render())

    def test_the_poller_is_absent_for_a_stale_flag(self):
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now() - timedelta(minutes=10)
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])
        self.assertNotIn('/profile/guilds-status/', self._render())

    def test_the_poller_carries_a_cap_longer_than_the_staleness_window(self):
        """A 3m cap would strip the poller before a legitimately slow 5m sync goes stale,
        leaving a spinner that never clears. The cap must outlast the window."""
        self.profile.guilds_refreshing = True
        self.profile.guilds_refresh_started_at = timezone.now()
        self.profile.save(update_fields=['guilds_refreshing',
                                         'guilds_refresh_started_at'])
        self.assertIn('wait 6m then remove me', self._render())
        self.assertGreater(timedelta(minutes=6), GUILDS_REFRESH_MAX_AGE)


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
            'the_gatehouse.services.discord_oauth.SocialAccount'
        ) as social_mock, mock.patch(
            'the_gatehouse.services.discord_oauth.requests.get',
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


