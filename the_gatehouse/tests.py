"""Tests for the_gatehouse: site concerns, Discord OAuth login, and webhooks.

The bot's own tests (slash commands, LFG, schedule polls, embeds) live in
the_databot/tests.py alongside the code they exercise.
"""
import io
import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from unittest import mock

from urllib.parse import quote

import requests
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
from django.test import Client, TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError

from the_keep.models import Faction, StatusChoices
from the_warroom.models import Game, Effort
from the_gatehouse.models import (DiscordGuild, Profile, DEFAULT_PROFILE_IMAGE,
                                  GUILDS_REFRESH_MAX_AGE, PlayerSchedule,
                                  general_schedule_for, schedule_for)
from the_gatehouse.services.availability import (local_to_utc_hours, utc_to_local_hours,
                                                 hours_to_bitmask, overlap_count,
                                                 format_hour_12, hour_labels,
                                                 availability_matrix, overlap_summary,
                                                 heat_bucket, reachable_buckets)
from the_gatehouse import views
from the_gatehouse.signals import user_logged_in_handler
from the_gatehouse.services.discord_oauth import update_discord_avatar
from the_gatehouse.services.steam_openid import (make_link_token, read_link_token,
                                                 verify_response)
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




# ── Steam account linking ────────────────────────────────────────────────────

STEAM_ID = "76561197960265728"
# Outside the "7656119" prefix: an individual SteamID64 is
# (1<<56)|(1<<52)|(1<<32)|account_id, so it crosses into 765612... as account ids
# grow, and Steam already issues these.
STEAM_ID_HIGH = "76561200107749376"


def _steam_callback_params(steam_id=STEAM_ID):
    """A well-formed Steam OpenID callback query string. Signature values are
    arbitrary here -- what makes a claim trustworthy is the check_authentication
    round trip, which every test below controls explicitly."""
    return {
        "openid.mode": "id_res",
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.sig": "not-checked-locally",
        "openid.signed": "signed,op_endpoint,claimed_id,identity",
    }


def _mock_steam_verify(is_valid=True):
    """Patch the outbound check_authentication POST."""
    body = "ns:http://specs.openid.net/auth/2.0\nis_valid:%s\n" % ("true" if is_valid else "false")
    response = mock.Mock(text=body)
    response.raise_for_status = mock.Mock()
    return mock.patch("the_gatehouse.services.steam_openid.requests.post",
                      return_value=response)


class SteamOpenIDVerifyTest(TestCase):
    """verify_response is the security boundary: request.GET is attacker-supplied
    until Steam confirms it."""

    def test_valid_response_returns_steam_id(self):
        with _mock_steam_verify(True):
            self.assertEqual(verify_response(_steam_callback_params()), STEAM_ID)

    def test_high_range_steam_id_is_accepted(self):
        """Regression: a "7656119" prefix match would reject real accounts."""
        with _mock_steam_verify(True):
            self.assertEqual(
                verify_response(_steam_callback_params(STEAM_ID_HIGH)), STEAM_ID_HIGH)

    def test_forged_claim_is_rejected_when_steam_says_invalid(self):
        with _mock_steam_verify(False):
            self.assertIsNone(verify_response(_steam_callback_params()))

    def test_lookalike_host_is_rejected(self):
        params = _steam_callback_params()
        params["openid.claimed_id"] = (
            f"https://steamcommunity.com.evil.tld/openid/id/{STEAM_ID}")
        with _mock_steam_verify(True):
            self.assertIsNone(verify_response(params))

    def test_network_error_returns_none(self):
        with mock.patch("the_gatehouse.services.steam_openid.requests.post",
                        side_effect=requests.RequestException("boom")):
            self.assertIsNone(verify_response(_steam_callback_params()))

    def test_junk_query_makes_no_outbound_request(self):
        with mock.patch("the_gatehouse.services.steam_openid.requests.post") as post:
            self.assertIsNone(verify_response({}))
            self.assertIsNone(verify_response({"openid.mode": "cancel"}))
            self.assertIsNone(verify_response({"openid.mode": "id_res"}))
            self.assertFalse(post.called)


class SteamLinkTokenTest(TestCase):
    def test_round_trip(self):
        self.assertEqual(read_link_token(make_link_token(7)), 7)

    def test_tampered_token_is_rejected(self):
        token = make_link_token(7)
        self.assertIsNone(read_link_token(token[:-4] + "aaaa"))

    def test_garbage_and_empty_are_rejected(self):
        self.assertIsNone(read_link_token("nonsense"))
        self.assertIsNone(read_link_token(None))
        self.assertIsNone(read_link_token(""))

    def test_expired_token_is_rejected(self):
        token = make_link_token(7)
        with mock.patch("the_gatehouse.services.steam_openid.STEAM_LINK_MAX_AGE", -1):
            self.assertIsNone(read_link_token(token))


class SteamLinkFlowTest(_NoLoginSignalMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="steamer", password="pw")
        self.profile = self.user.profile
        self.profile.discord = "steamer"
        self.profile.save()

    def _start_session(self, profile=None):
        """Put a profile pk in the session the way steam_link_start does."""
        session = self.client.session
        session[views.STEAM_LINK_SESSION_KEY] = (profile or self.profile).pk
        session.save()

    def _reload(self):
        return Profile.objects.get(pk=self.profile.pk)

    # -- start --------------------------------------------------------------

    def test_start_redirects_logged_in_user_to_steam(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("steam-link-start"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(
            "https://steamcommunity.com/openid/login?"))
        self.assertEqual(self.client.session[views.STEAM_LINK_SESSION_KEY],
                         self.profile.pk)

    def test_start_anonymous_without_token_goes_to_login(self):
        response = self.client.get(reverse("steam-link-start"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("steamcommunity.com", response["Location"])

    def test_start_with_valid_token_works_while_logged_out(self):
        """The whole point of the bot hand-off: no site login required."""
        token = make_link_token(self.profile.pk)
        response = self.client.get(reverse("steam-link-start"), {"t": token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(
            "https://steamcommunity.com/openid/login?"))
        self.assertEqual(self.client.session[views.STEAM_LINK_SESSION_KEY],
                         self.profile.pk)

    def test_start_with_expired_token_does_not_reach_steam(self):
        token = make_link_token(self.profile.pk)
        with mock.patch("the_gatehouse.services.steam_openid.STEAM_LINK_MAX_AGE", -1):
            response = self.client.get(reverse("steam-link-start"), {"t": token})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("steamcommunity.com", response["Location"])
        self.assertNotIn(views.STEAM_LINK_SESSION_KEY, self.client.session)

    def test_start_uses_canonical_site_url_not_request_host(self):
        """realm/return_to must not follow the Host header, or Steam re-prompts."""
        self.client.force_login(self.user)
        with override_settings(SITE_URL="https://www.therootdatabase.com"):
            response = self.client.get(reverse("steam-link-start"),
                                       HTTP_HOST="therootdatabase.com")
        self.assertIn(quote("https://www.therootdatabase.com/", safe=""),
                      response["Location"])

    # -- callback -----------------------------------------------------------

    def test_callback_stores_verified_steam_id(self):
        self.client.force_login(self.user)
        self._start_session()
        with _mock_steam_verify(True):
            response = self.client.get(reverse("steam-link-callback"),
                                       _steam_callback_params())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._reload().steam_id, STEAM_ID)

    def test_callback_ignores_forged_claim(self):
        """Someone hand-crafting the callback URL must not be able to claim an id."""
        self.client.force_login(self.user)
        self._start_session()
        with _mock_steam_verify(False):
            self.client.get(reverse("steam-link-callback"), _steam_callback_params())
        self.assertIsNone(self._reload().steam_id)

    def test_callback_without_session_writes_nothing(self):
        self.client.force_login(self.user)
        with _mock_steam_verify(True):
            self.client.get(reverse("steam-link-callback"), _steam_callback_params())
        self.assertIsNone(self._reload().steam_id)

    def test_callback_does_not_steal_an_id_linked_elsewhere(self):
        other = Profile.objects.create(discord="other", steam_id=STEAM_ID)
        self.client.force_login(self.user)
        self._start_session()
        with _mock_steam_verify(True):
            self.client.get(reverse("steam-link-callback"), _steam_callback_params())
        self.assertIsNone(self._reload().steam_id)
        self.assertEqual(Profile.objects.get(pk=other.pk).steam_id, STEAM_ID)

    def test_callback_relinking_same_id_to_same_profile_is_fine(self):
        self.profile.steam_id = STEAM_ID
        self.profile.save(update_fields=["steam_id"])
        self.client.force_login(self.user)
        self._start_session()
        with _mock_steam_verify(True):
            self.client.get(reverse("steam-link-callback"), _steam_callback_params())
        self.assertEqual(self._reload().steam_id, STEAM_ID)

    def test_anonymous_callback_lands_somewhere_public(self):
        """A logged-out user finishing the bot flow must not be bounced to login."""
        self._start_session()
        with _mock_steam_verify(True):
            response = self.client.get(reverse("steam-link-callback"),
                                       _steam_callback_params())
        self.assertEqual(self._reload().steam_id, STEAM_ID)
        followed = self.client.get(response["Location"])
        self.assertEqual(followed.status_code, 200)

    def test_callback_does_not_clobber_display_name(self):
        """save(update_fields=...) keeps Profile.save's display_name branch away."""
        self.profile.display_name = "Keep Me"
        self.profile.save()
        self.client.force_login(self.user)
        self._start_session()
        with _mock_steam_verify(True):
            self.client.get(reverse("steam-link-callback"), _steam_callback_params())
        self.assertEqual(self._reload().display_name, "Keep Me")

    # -- unlink -------------------------------------------------------------

    def test_unlink_clears_on_post(self):
        self.profile.steam_id = STEAM_ID
        self.profile.save(update_fields=["steam_id"])
        self.client.force_login(self.user)
        self.client.post(reverse("steam-unlink"))
        self.assertIsNone(self._reload().steam_id)

    def test_unlink_rejects_get(self):
        self.profile.steam_id = STEAM_ID
        self.profile.save(update_fields=["steam_id"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("steam-unlink"))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self._reload().steam_id, STEAM_ID)

    def test_unlink_requires_login(self):
        response = self.client.post(reverse("steam-unlink"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class AvailabilityConversionTests(TestCase):
    """Local <-> UTC hour-of-week conversion.

    These exercise the reasons the availability code uses ZoneInfo instead of a
    stored numeric offset: DST, sub-hour zones, and week wraparound.
    """

    def test_local_hour_converts_to_utc(self):
        # Monday 09:00 in New York (EST, UTC-5) is Monday 14:00 UTC.
        self.assertEqual(local_to_utc_hours([9], 'America/New_York'), [14])

    def test_round_trip_is_identity_for_whole_hour_zones(self):
        for tz_name in ('America/New_York', 'Europe/Berlin', 'Asia/Tokyo', 'UTC'):
            with self.subTest(tz=tz_name):
                original = [9, 10, 11, 38, 39, 167]
                utc = local_to_utc_hours(original, tz_name)
                self.assertEqual(utc_to_local_hours(utc, tz_name), original)

    def test_dst_zone_differs_between_january_and_july(self):
        """The regression a fixed numeric offset cannot catch.

        New York is UTC-5 in January and UTC-4 in July, so the same wall-clock hour
        maps to different UTC hours. A stored offset would apply one of them all
        year; ZoneInfo applies the one that actually holds at that moment.
        """
        january = datetime(2024, 1, 1, 9, tzinfo=ZoneInfo('America/New_York'))
        july = datetime(2024, 7, 1, 9, tzinfo=ZoneInfo('America/New_York'))
        self.assertNotEqual(
            january.astimezone(dt_timezone.utc).hour,
            july.astimezone(dt_timezone.utc).hour,
        )

    def test_wraps_forward_across_the_week_boundary(self):
        # Sunday 23:00 in New York (UTC-5) is Monday 04:00 UTC -> hour 4, not 171.
        sunday_23 = 6 * 24 + 23
        self.assertEqual(local_to_utc_hours([sunday_23], 'America/New_York'), [4])

    def test_wraps_backward_across_the_week_boundary(self):
        # Monday 00:00 in Tokyo (UTC+9) is Sunday 15:00 UTC -> hour 159, not -9.
        self.assertEqual(local_to_utc_hours([0], 'Asia/Tokyo'), [159])

    def test_half_hour_zone_rounds_down_to_the_containing_hour(self):
        # Kolkata is UTC+5:30, so Monday 09:00 local is 03:30 UTC -> hour 3.
        self.assertEqual(local_to_utc_hours([9], 'Asia/Kolkata'), [3])

    def test_unknown_timezone_falls_back_to_utc(self):
        self.assertEqual(local_to_utc_hours([9], 'Not/AZone'), [9])
        self.assertEqual(local_to_utc_hours([9], None), [9])

    def test_out_of_range_and_junk_values_are_dropped(self):
        self.assertEqual(local_to_utc_hours([999, -4, 'x', None, 5], 'UTC'), [5])

    def test_bitmask_overlap_counts_shared_hours(self):
        self.assertEqual(
            overlap_count(hours_to_bitmask([1, 2, 3, 4]), hours_to_bitmask([3, 4, 5])),
            2,
        )

    def test_bitmask_matches_set_intersection(self):
        a, b = [0, 5, 23, 100, 167], [5, 23, 99, 167]
        self.assertEqual(
            overlap_count(hours_to_bitmask(a), hours_to_bitmask(b)),
            len(set(a) & set(b)),
        )

    def test_twelve_hour_labels_handle_midnight_and_noon(self):
        """The off-by-one trap: hour 0 is 12 AM and hour 12 is 12 PM, not 0."""
        self.assertEqual(format_hour_12(0), '12:00 AM')
        self.assertEqual(format_hour_12(12), '12:00 PM')
        self.assertEqual(format_hour_12(11), '11:00 AM')
        self.assertEqual(format_hour_12(13), '1:00 PM')
        self.assertEqual(format_hour_12(23), '11:00 PM')

    def test_compact_hour_labels_keep_am_pm(self):
        self.assertEqual(format_hour_12(0, compact=True), '12am')
        self.assertEqual(format_hour_12(9, compact=True), '9am')
        self.assertEqual(format_hour_12(12, compact=True), '12pm')
        self.assertEqual(format_hour_12(23, compact=True), '11pm')

    def test_hour_labels_covers_the_whole_day(self):
        labels = hour_labels()
        self.assertEqual(len(labels), 24)
        self.assertEqual(labels[0], (0, '12am', '12:00 AM'))
        self.assertEqual(labels[12], (12, '12pm', '12:00 PM'))
        self.assertEqual(labels[23], (23, '11pm', '11:00 PM'))


class PlayerScheduleModelTests(TestCase):
    """The schedule model, its accessors, and the NULL-uniqueness gap."""

    def setUp(self):
        self.user = User.objects.create_user(username='scheduler', password='pw')
        self.profile = self.user.profile

    def test_general_schedule_accessor_is_idempotent(self):
        """unique_together cannot enforce this -- NULL is distinct from NULL in a
        unique index -- so the accessor is the only thing preventing duplicates."""
        first = general_schedule_for(self.profile)
        second = general_schedule_for(self.profile)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            PlayerSchedule.objects.filter(profile=self.profile, tournament=None).count(),
            1,
        )

    def test_as_bitmask_sets_a_bit_per_hour(self):
        schedule = general_schedule_for(self.profile)
        schedule.available_hours = [0, 3, 167]
        mask = schedule.as_bitmask()
        self.assertTrue(mask & (1 << 0))
        self.assertTrue(mask & (1 << 3))
        self.assertTrue(mask & (1 << 167))
        self.assertFalse(mask & (1 << 1))

    def test_schedule_for_falls_back_to_the_general_schedule(self):
        general = general_schedule_for(self.profile)
        general.available_hours = [1, 2, 3]
        general.save(update_fields=['available_hours'])
        self.assertEqual(schedule_for(self.profile).pk, general.pk)


class AvailabilityViewTests(_NoLoginSignalMixin, TestCase):
    """The /availability page: rendering, saving, and the Profile.save() trap."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='gridder', password='pw')
        self.profile = self.user.profile
        self.client.force_login(self.user)
        self.url = reverse('availability')

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_renders_the_grid(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'the_gatehouse/availability.html')

    def test_post_saves_utc_hours_and_updates_profile_timezone(self):
        response = self.client.post(self.url, {
            'timezone': 'America/New_York',
            'available_hours': '9,10',
            'action': 'save',
        })
        self.assertRedirects(response, self.url)

        schedule = PlayerSchedule.objects.get(profile=self.profile, tournament=None)
        # 09:00 and 10:00 EST -> 14:00 and 15:00 UTC.
        self.assertEqual(schedule.available_hours, [14, 15])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/New_York')

    def test_saved_hours_render_back_in_local_time(self):
        schedule = general_schedule_for(self.profile)
        schedule.available_hours = [14, 15]
        schedule.save(update_fields=['available_hours'])
        self.profile.timezone = 'America/New_York'
        self.profile.save(update_fields=['timezone'])

        response = self.client.get(self.url)
        self.assertEqual(response.context['selected_hours'], [9, 10])

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(self.url, {
            'timezone': 'Not/AZone',
            'available_hours': '9',
            'action': 'save',
        })
        self.assertEqual(response.status_code, 200)  # redisplayed, not saved
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.timezone)
        self.assertFalse(
            PlayerSchedule.objects.filter(profile=self.profile)
            .exclude(available_hours=[]).exists()
        )

    def test_empty_grid_clears_availability(self):
        schedule = general_schedule_for(self.profile)
        schedule.available_hours = [1, 2, 3]
        schedule.save(update_fields=['available_hours'])

        self.client.post(self.url, {
            'timezone': 'UTC', 'available_hours': '', 'action': 'save',
        })
        schedule.refresh_from_db()
        self.assertEqual(schedule.available_hours, [])

    def test_saving_does_not_delete_the_profile_avatar(self):
        """Profile.save() deletes the stored avatar unless update_fields excludes
        'image'. Every writer of profile.timezone must pass update_fields; this
        locks that in."""
        self.profile.image = 'profile_pics/real_avatar.png'
        self.profile.save(update_fields=['image'])

        self.client.post(self.url, {
            'timezone': 'Europe/Berlin', 'available_hours': '9', 'action': 'save',
        })

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.image.name, 'profile_pics/real_avatar.png')

    def test_timezone_change_keeps_unsaved_selection_and_does_not_save_hours(self):
        """Re-rendering under a new zone must not silently discard painted cells,
        and must not commit them either."""
        response = self.client.post(self.url, {
            'timezone': 'Asia/Tokyo',
            'drawn_timezone': 'UTC',
            'available_hours': '9,10',
            'action': 'change_timezone',
        })
        self.assertEqual(response.status_code, 200)
        # Same instants, relabelled: 09:00/10:00 UTC is 18:00/19:00 in Tokyo.
        self.assertEqual(response.context['selected_hours'], [18, 19])

        schedule = PlayerSchedule.objects.get(profile=self.profile, tournament=None)
        self.assertEqual(schedule.available_hours, [])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'Asia/Tokyo')

    def test_changing_timezone_moves_the_selection_not_the_hours(self):
        """Availability is ABSOLUTE. Viewing it in another zone relabels the same
        instants, so the lit rows shift -- 12am in UTC-7 must not stay lit at 12am
        once the grid is redrawn in UTC-8."""
        response = self.client.post(self.url, {
            'timezone': 'America/Anchorage',      # UTC-9 in January
            'drawn_timezone': 'America/Los_Angeles',  # UTC-8 in January
            'available_hours': '0',               # Monday 12am Pacific
            'action': 'change_timezone',
        })
        self.assertEqual(response.status_code, 200)
        # Monday 00:00 Pacific is 08:00 UTC, which is 23:00 Sunday in Anchorage.
        self.assertNotIn(0, response.context['selected_hours'])
        self.assertEqual(response.context['selected_hours'], [6 * 24 + 23])

    def test_saving_interprets_hours_in_the_zone_they_were_drawn_in(self):
        """The picker may differ from the zone the grid was rendered in; the hours
        mean what they meant when painted."""
        self.client.post(self.url, {
            'timezone': 'Asia/Tokyo',
            'drawn_timezone': 'America/New_York',
            'available_hours': '9',
            'action': 'save',
        })
        schedule = PlayerSchedule.objects.get(profile=self.profile, tournament=None)
        # 09:00 New York -> 14:00 UTC, NOT 09:00 Tokyo -> 00:00 UTC.
        self.assertEqual(schedule.available_hours, [14])

    def test_unknown_drawn_timezone_falls_back_to_the_submitted_one(self):
        """A junk hidden field must not reject the form and lose the grid."""
        response = self.client.post(self.url, {
            'timezone': 'UTC',
            'drawn_timezone': 'Not/AZone',
            'available_hours': '5',
            'action': 'save',
        })
        self.assertRedirects(response, self.url)
        schedule = PlayerSchedule.objects.get(profile=self.profile, tournament=None)
        self.assertEqual(schedule.available_hours, [5])

    def test_settings_page_shows_the_availability_card(self):
        # The settings cards (API key, Steam, Availability) sit behind
        # {% if user.profile.player %}, so the card only renders for a player.
        self.profile.group = Profile.GroupChoices.PLAYER
        schedule = general_schedule_for(self.profile)
        schedule.available_hours = [1, 2, 3]
        schedule.save(update_fields=['available_hours'])
        self.profile.save(update_fields=['group'])

        response = self.client.get(reverse('user-settings'))
        self.assertEqual(response.context['availability_hours_count'], 3)
        self.assertContains(response, reverse('availability'))


class AvailabilityTournamentSelectorTests(_NoLoginSignalMixin, TestCase):
    """/availability can edit tournament-specific schedules, not just the general one."""

    def setUp(self):
        super().setUp()
        from the_warroom.models import Tournament
        self.user = User.objects.create_user(username='selector', password='pw')
        self.profile = self.user.profile
        self.client.force_login(self.user)
        self.url = reverse('availability')
        self.tournament = Tournament.objects.create(name='Selector Cup', is_active=True)

    def _tournament_schedule(self, hours):
        return PlayerSchedule.objects.create(
            profile=self.profile, tournament=self.tournament, available_hours=hours
        )

    def test_no_selector_without_a_tournament_schedule(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['tournament_schedules']), [])
        self.assertIsNone(response.context['editing_tournament'])

    def test_selector_lists_tournaments_the_player_has_a_schedule_for(self):
        self._tournament_schedule([10, 11])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['tournament_schedules']), 1)
        self.assertContains(response, 'Selector Cup')

    def test_can_edit_a_tournament_schedule(self):
        schedule = self._tournament_schedule([10])
        self.profile.timezone = 'UTC'
        self.profile.save(update_fields=['timezone'])

        response = self.client.get(f'{self.url}?tournament={self.tournament.slug}')
        self.assertEqual(response.context['editing_tournament'], self.tournament)
        self.assertEqual(response.context['selected_hours'], [10])

        self.client.post(self.url, {
            'timezone': 'UTC', 'drawn_timezone': 'UTC',
            'available_hours': '20,21',
            'schedule_target': self.tournament.slug,
            'action': 'save',
        })
        schedule.refresh_from_db()
        self.assertEqual(schedule.available_hours, [20, 21])

    def test_editing_a_tournament_leaves_the_general_schedule_alone(self):
        general = general_schedule_for(self.profile)
        general.available_hours = [1, 2]
        general.save(update_fields=['available_hours'])
        self._tournament_schedule([10])

        self.client.post(self.url, {
            'timezone': 'UTC', 'drawn_timezone': 'UTC',
            'available_hours': '20',
            'schedule_target': self.tournament.slug,
            'action': 'save',
        })
        general.refresh_from_db()
        self.assertEqual(general.available_hours, [1, 2])

    def test_tournament_without_a_schedule_is_rejected(self):
        """The guard against offering availability where it means nothing."""
        from the_warroom.models import Tournament
        other = Tournament.objects.create(name='Not Mine', is_active=True)
        response = self.client.get(f'{self.url}?tournament={other.slug}')
        self.assertEqual(response.status_code, 404)

    def test_save_returns_to_the_same_schedule(self):
        self._tournament_schedule([10])
        response = self.client.post(self.url, {
            'timezone': 'UTC', 'drawn_timezone': 'UTC',
            'available_hours': '20',
            'schedule_target': self.tournament.slug,
            'action': 'save',
        })
        self.assertIn(f'tournament={self.tournament.slug}', response['Location'])


class AvailabilityMatrixTests(TestCase):
    """The comparison page's pure data functions."""

    HOURS = {1: [10, 11, 12, 13], 2: [11, 12, 13, 14], 3: [12, 13]}

    def test_matrix_maps_each_hour_to_who_is_free(self):
        matrix = availability_matrix(self.HOURS)
        self.assertEqual(sorted(matrix[12]), [1, 2, 3])
        self.assertEqual(matrix[10], [1])
        self.assertEqual(matrix[14], [2])

    def test_matrix_omits_hours_nobody_has(self):
        matrix = availability_matrix(self.HOURS)
        self.assertNotIn(9, matrix)
        self.assertNotIn(99, matrix)

    def test_matrix_of_nothing_is_empty(self):
        self.assertEqual(availability_matrix({}), {})
        self.assertEqual(availability_matrix({1: []}), {})

    def test_overlap_summary_on_a_known_fixture(self):
        summary = overlap_summary(self.HOURS)
        self.assertEqual(summary['overlap_hours'], [12, 13])
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['best_block'], 2)
        self.assertEqual(summary['days'], 1)

    def test_overlap_summary_with_no_shared_hours(self):
        summary = overlap_summary({1: [10], 2: [20]})
        self.assertEqual(summary['overlap_hours'], [])
        self.assertEqual(summary['total'], 0)

    def test_overlap_summary_of_nothing(self):
        self.assertEqual(overlap_summary({})['total'], 0)

    def test_overlap_summary_wraps_the_week_boundary(self):
        """Sunday 23:00 + Monday 00:00 is one 2-hour block, not two."""
        sunday_23, monday_0 = 6 * 24 + 23, 0
        summary = overlap_summary({1: [sunday_23, monday_0], 2: [sunday_23, monday_0]})
        self.assertEqual(summary['best_block'], 2)

    def test_heat_bucket_is_keyed_on_players_missing(self):
        self.assertEqual(heat_bucket(4, 4), 'heat-0')   # everyone free
        self.assertEqual(heat_bucket(3, 4), 'heat-1')
        self.assertEqual(heat_bucket(2, 4), 'heat-2')
        self.assertEqual(heat_bucket(1, 4), 'heat-3')

    def test_heat_bucket_collapses_far_misses(self):
        self.assertEqual(heat_bucket(1, 5), 'heat-far')
        self.assertEqual(heat_bucket(2, 9), 'heat-far')

    def test_hour_nobody_is_free_gets_no_bucket(self):
        """An empty cell is not the same as a grey one."""
        self.assertIsNone(heat_bucket(0, 4))

    def test_reachable_buckets_truncate_for_small_groups(self):
        """A group of 3 can never be missing 4, so the legend must not claim it."""
        self.assertEqual(reachable_buckets(2), ['heat-0', 'heat-1'])
        self.assertEqual(reachable_buckets(3), ['heat-0', 'heat-1', 'heat-2'])
        self.assertEqual(reachable_buckets(4), ['heat-0', 'heat-1', 'heat-2', 'heat-3'])
        self.assertEqual(
            reachable_buckets(6),
            ['heat-0', 'heat-1', 'heat-2', 'heat-3', 'heat-far'],
        )
        self.assertEqual(reachable_buckets(0), [])


class AvailabilityCompareLFGTests(_NoLoginSignalMixin, TestCase):
    """The compare page can compare an LFG thread's roster, and REFUSES by
    rendering rather than 403-ing."""

    def setUp(self):
        super().setUp()
        from the_databot.models import LFGThread
        self.url = reverse('availability-compare')

        self.members = []
        for i in range(3):
            user = User.objects.create_user(username=f'lfgp{i}', password='pw')
            profile = user.profile
            profile.timezone = 'UTC'
            profile.save(update_fields=['timezone'])
            PlayerSchedule.objects.create(
                profile=profile, tournament=None, available_hours=[10, 11, 12])
            self.members.append(profile)

        self.thread = LFGThread.objects.create(thread_id='cmp-thread-1',
                                               host=self.members[0])
        self.thread.players.set(self.members)

        outsider = User.objects.create_user(username='lfgout', password='pw')
        self.outsider = outsider.profile

    def _get(self, profile, **params):
        self.client.force_login(profile.user)
        return self.client.get(self.url, params)

    def test_a_roster_member_sees_the_comparison(self):
        response = self._get(self.members[1], lfg=self.thread.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_view'])
        self.assertEqual(response.context['player_count'], 3)

    def test_the_host_sees_the_comparison(self):
        response = self._get(self.members[0], lfg=self.thread.pk)
        self.assertTrue(response.context['can_view'])

    def test_an_outsider_gets_an_explanation_not_a_403(self):
        response = self._get(self.outsider, lfg=self.thread.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view'])

    def test_a_refusal_leaks_no_availability(self):
        """Rendering instead of raising must not become a way to read when people
        are FREE. Names are a deliberate exception -- they go in the link preview
        so a URL pasted into Discord unfurls as something useful -- but the hours
        behind them stay gated."""
        response = self._get(self.outsider, lfg=self.thread.pk)
        self.assertEqual(response.context['player_count'], 0)
        self.assertEqual(response.context['players'], [])
        self.assertEqual(response.context['player_hours_json'], {})
        self.assertFalse(response.context['has_any_availability'])

    def test_a_refusal_still_names_the_players_for_the_link_preview(self):
        """The cost of that exception, pinned so it stays a decision rather than
        a drift: anyone holding the URL can read the roster."""
        response = self._get(self.outsider, lfg=self.thread.pk)
        description = response.context['meta_description']
        for member in self.members:
            self.assertIn(member.name, description)

    def test_an_empty_roster_is_not_public(self):
        """_thread_actor_error fails OPEN on an empty roster, which is right in
        Discord and wrong on the web."""
        from the_databot.models import LFGThread
        empty = LFGThread.objects.create(thread_id='cmp-thread-empty')
        response = self._get(self.outsider, lfg=empty.pk)
        self.assertFalse(response.context['can_view'])

    def test_a_non_numeric_lfg_id_is_a_404(self):
        response = self._get(self.members[0], lfg='nope')
        self.assertEqual(response.status_code, 404)

    def test_a_missing_thread_is_a_404(self):
        response = self._get(self.members[0], lfg=999999)
        self.assertEqual(response.status_code, 404)

    # ── logged out ──────────────────────────────────────────────────────────

    def test_a_logged_out_visitor_gets_the_page_not_a_redirect(self):
        """The link is handed out in Discord, so bouncing an anonymous visitor
        through OAuth tells them nothing about what they followed."""
        response = self.client.get(self.url, {'lfg': self.thread.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view'])

    def test_a_logged_out_visitor_is_offered_a_login_back_to_this_page(self):
        response = self.client.get(self.url, {'lfg': self.thread.pk})
        body = response.content.decode()
        self.assertIn('Log in with Discord', body)
        # The return trip must survive the QUERY STRING: the ? and = are encoded
        # so they stay part of `next` instead of terminating it. (Django's
        # urlencode filter leaves / alone, which is harmless here.)
        self.assertIn(f'next=/availability/compare/%3Flfg%3D{self.thread.pk}', body)

    def test_a_logged_in_but_refused_viewer_is_not_told_to_log_in(self):
        """They already are. Only the logged-out branch gets the button."""
        response = self._get(self.outsider, lfg=self.thread.pk)
        self.assertNotIn('Log in with Discord', response.content.decode())

    def test_a_logged_out_visitor_sees_no_availability(self):
        response = self.client.get(self.url, {'lfg': self.thread.pk})
        self.assertEqual(response.context['player_count'], 0)
        self.assertEqual(response.context['player_hours_json'], {})

    def test_the_link_preview_names_the_players_anonymously(self):
        """The unfurler has no session, so this is the ONLY case that matters
        for a preview -- if it keyed on can_view every shared link would preview
        as the refusal notice."""
        response = self.client.get(self.url, {'lfg': self.thread.pk})
        body = response.content.decode()
        self.assertIn('og:description', body)
        for member in self.members:
            self.assertIn(member.name, response.context['meta_description'])

    def test_a_players_link_previews_generically_when_logged_out(self):
        """?players= scopes to tournaments the VIEWER shares, so it resolves
        nobody without a session -- there is no roster to name."""
        slugs = ','.join(p.slug for p in self.members)
        response = self.client.get(self.url, {'players': slugs})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view'])
        for member in self.members:
            self.assertNotIn(member.name, response.context['meta_description'])


class DismissNotificationTests(_NoLoginSignalMixin, TestCase):
    """Dismissals were silently failing, leaving is_dismissed False so the
    notification returned on the next page load.

    Two independent causes, both covered here:
      1. The base template never rendered {% csrf_token %}, so no csrftoken
         cookie existed for the X button's fetch to read -> 403.
      2. The View link fired a fetch from onclick and navigated immediately,
         so the request raced the page teardown.
    """

    def setUp(self):
        from the_gatehouse.models import UserNotification

        super().setUp()
        self.user = User.objects.create_user(username='notified', password='pw')
        self.other = User.objects.create_user(username='stranger', password='pw')
        self.notification = UserNotification.objects.create(
            profile=self.user.profile,
            message='Your match is scheduled.',
            related_url='/battlefield/',
        )
        self.url = reverse('dismiss-notification', args=[self.notification.id])

    def _refresh(self):
        self.notification.refresh_from_db()
        return self.notification

    def test_posting_dismisses_the_notification(self):
        """The X button's fetch path."""
        self.client.force_login(self.user)
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._refresh().is_dismissed)
        self.assertIsNotNone(self.notification.dismissed_at)

    def test_the_base_template_sets_a_csrf_cookie(self):
        """Regression test for bug 1. Without {% csrf_token %} in the base
        template Django has no reason to set the cookie, getCookie() returns
        null, and the dismissal 403s. This failed before the fix."""
        self.client.force_login(self.user)
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('csrftoken', response.cookies)

    def test_a_missing_csrf_token_is_rejected(self):
        """Pins that CSRF is genuinely enforced, so the fix above is doing real
        work rather than papering over a disabled check."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._refresh().is_dismissed)

    def test_get_with_next_dismisses_and_redirects(self):
        """The View link's path: no fetch, nothing to race."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'next': '/battlefield/'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/battlefield/')
        self.assertTrue(self._refresh().is_dismissed)

    def test_an_offsite_next_is_refused(self):
        """related_url is stored on the model and may be absolute, so an
        unvalidated next would be an open redirect."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'next': 'https://evil.example.com/'})

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example.com', response['Location'])
        self.assertTrue(self._refresh().is_dismissed)

    def test_another_users_notification_is_404(self):
        """Owner scoping must hold on the newly-allowed GET path too."""
        self.client.force_login(self.other)
        response = self.client.get(self.url, {'next': '/battlefield/'})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self._refresh().is_dismissed)

    def test_dismissing_twice_is_harmless(self):
        """A double-click or a retry must not error."""
        self.client.force_login(self.user)
        self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._refresh().is_dismissed)

    def test_a_dismissed_notification_stops_being_shown(self):
        """The point of all of it: it actually stops appearing.

        Asserts against the same is_dismissed=False filter active_user_data()
        feeds the alert stack from, rather than rendering a page -- the context
        processor wraps its whole body in `except Exception` and falls back to a
        stub context, so a missing fixture there would mask this assertion
        instead of failing it."""
        from the_gatehouse.models import UserNotification

        def shown():
            return list(UserNotification.objects.filter(
                profile=self.user.profile, is_dismissed=False))

        self.assertIn(self.notification, shown())

        self.client.force_login(self.user)
        self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertNotIn(self.notification, shown())
