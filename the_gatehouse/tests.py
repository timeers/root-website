"""Tests for the_gatehouse's own concerns.

The Discord-bot tests moved to the_databot/tests.py along with the code they
exercise; this file covers what stayed behind.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from the_keep.models import Faction, StatusChoices
from the_warroom.models import Game, Effort
from the_gatehouse.models import Profile
from the_gatehouse.tasks import update_post_status


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
