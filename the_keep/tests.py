import hashlib
import json
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from PIL import Image

from the_keep.forms import discord_thread_guild_error
from the_keep.utils import resize_image_in_place

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


class DiscordThreadGuildErrorTests(TestCase):
    """The ww_link / wr_link / fr_link rule.

    This replaced a bare `f"discord.com/channels/{guild}" in url` substring
    test. The rejection cases below are the point of the change -- each one
    PASSED under the old rule.
    """

    GUILD = str(config['WW_GUILD_ID'])
    OTHER_GUILD = "111122223333444455"
    SERVER = "Woodland Warriors"

    def check(self, url):
        return discord_thread_guild_error(url, self.GUILD, self.SERVER)

    def assertAccepted(self, url):
        self.assertIsNone(self.check(url), f"should have been accepted: {url!r}")

    def assertRejected(self, url):
        self.assertIsNotNone(self.check(url), f"should have been rejected: {url!r}")

    # --- accepted -------------------------------------------------------
    def test_canonical_thread_link(self):
        self.assertAccepted(f"https://discord.com/channels/{self.GUILD}/123456789")

    def test_link_with_a_message_id(self):
        """What Discord's "Copy Message Link" produces."""
        self.assertAccepted(
            f"https://discord.com/channels/{self.GUILD}/123456789/987654321")

    def test_discordapp_com_is_accepted(self):
        """A legitimate Discord domain the old substring rule REJECTED."""
        self.assertAccepted(f"https://discordapp.com/channels/{self.GUILD}/123456789")

    def test_trailing_slash(self):
        self.assertAccepted(f"https://discord.com/channels/{self.GUILD}/123456789/")

    def test_surrounding_whitespace(self):
        self.assertAccepted(f"  https://discord.com/channels/{self.GUILD}/123456789 ")

    def test_empty_is_not_an_error(self):
        """Required-ness belongs to the field, not this check."""
        self.assertAccepted("")
        self.assertAccepted(None)

    # --- rejected (each of these passed the OLD substring rule) ---------
    def test_rejects_a_spoofed_host(self):
        """The old rule searched the whole string for the guild id, so any host
        carrying that path passed."""
        self.assertRejected(
            f"https://evil.example.com/discord.com/channels/{self.GUILD}/1")

    def test_rejects_a_link_with_no_thread_id(self):
        """The error says "not a valid thread", so a bare guild link is wrong
        even though the old rule allowed it."""
        self.assertRejected(f"https://discord.com/channels/{self.GUILD}")

    def test_rejects_the_guild_id_appearing_elsewhere(self):
        self.assertRejected(f"https://example.com/?ref=discord.com/channels/{self.GUILD}/1")

    # --- rejected (rejected before too) --------------------------------
    def test_rejects_another_guild(self):
        self.assertRejected(
            f"https://discord.com/channels/{self.OTHER_GUILD}/123456789")

    def test_rejects_an_invite_link(self):
        self.assertRejected("https://discord.com/invite/abcdef")

    def test_rejects_a_non_discord_url(self):
        self.assertRejected("https://ledergames.com/products/root")

    # --- the messages differ, so the user knows which thing is wrong ----
    def test_wrong_guild_and_wrong_shape_give_different_advice(self):
        wrong_shape = self.check("https://discord.com/invite/abcdef")
        wrong_guild = self.check(
            f"https://discord.com/channels/{self.OTHER_GUILD}/123456789")
        self.assertIn("Copy", wrong_shape)
        self.assertIn("correct Discord server", wrong_guild)
        self.assertIn(self.SERVER, wrong_shape)
        self.assertIn(self.SERVER, wrong_guild)


class ResizeImageInPlaceTests(TestCase):
    """resize_image_in_place must never damage the file it is given.

    Both guards here are regressions, not hypotheticals: media/default_images/
    is tracked in git, and default_images/animals/fox.webp was repeatedly
    committed rewritten -- and several times truncated to ZERO bytes -- by test
    runs that saved a Faction without a picture.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='resize_test_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _image(self, name, size=(900, 900)):
        """An oversized WEBP, so every code path below wants to rewrite it."""
        path = os.path.join(self.tmp, name)
        Image.new("RGBA", size, (255, 0, 0, 255)).save(path, format="WEBP")
        return path

    def _field(self, name, path):
        return SimpleNamespace(name=name, path=path)

    def _digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha1(handle.read()).hexdigest()

    def test_a_default_image_is_never_rewritten(self):
        """A shipped default is shared by every object with no image of its own,
        so re-encoding one rewrites a committed file for everybody."""
        path = self._image('fox.webp')
        before = self._digest(path)

        resize_image_in_place(
            self._field('default_images/animals/fox.webp', path), max_size=100)

        self.assertEqual(self._digest(path), before)

    def test_a_normal_image_is_still_resized(self):
        """The guard above must not disable the feature for real uploads."""
        path = self._image('upload.webp')
        before = self._digest(path)

        resize_image_in_place(self._field('uploads/upload.webp', path),
                              max_size=400)

        self.assertNotEqual(self._digest(path), before)
        self.assertEqual(Image.open(path).size, (400, 400))

    def test_a_resize_failure_leaves_the_original_intact(self):
        """THE zero-byte regression. Encoding used to write straight to the
        destination -- the same file being read -- so a failure part-way left a
        truncated image, and the except clause swallowed the reason."""
        path = self._image('upload.webp')
        before = self._digest(path)
        size_before = os.path.getsize(path)

        with mock.patch.object(Image.Image, 'save', side_effect=OSError("disk full")):
            resize_image_in_place(self._field('uploads/upload.webp', path),
                                  max_size=400)

        self.assertEqual(self._digest(path), before)
        self.assertEqual(os.path.getsize(path), size_before)

    def test_a_failed_resize_leaves_no_temp_file_behind(self):
        """The temp file is an implementation detail and must not become litter
        in MEDIA_ROOT when an encode fails."""
        path = self._image('upload.webp')

        with mock.patch.object(Image.Image, 'save', side_effect=OSError("disk full")):
            resize_image_in_place(self._field('uploads/upload.webp', path),
                                  max_size=400)

        self.assertEqual(os.listdir(self.tmp), ['upload.webp'])
