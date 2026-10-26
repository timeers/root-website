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


class AboutArtCreditsTests(TestCase):
    """The About page's art credits: one card per artist, their pieces in a
    carousel that only shows chevrons when there is more than one, and the
    whole card linking to the artist's credit_url."""

    def setUp(self):
        from django.test import override_settings
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings_override = override_settings(MEDIA_ROOT=media)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        from the_gatehouse.models import Profile
        self.alice = Profile.objects.create(discord="artalice", display_name="Alice",
                                            credit_link="https://alice.example/art")
        self.bob = Profile.objects.create(discord="artbob", display_name="Bob")

    def _art(self, artist, title, order, show=True):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from io import BytesIO
        from the_gatehouse.models import SiteArtwork
        buf = BytesIO()
        Image.new("RGB", (64, 18), "red").save(buf, format="PNG")
        return SiteArtwork.objects.create(
            artist=artist, title=title, order=order, show=show,
            image=SimpleUploadedFile(f"{title}.png", buf.getvalue(), content_type="image/png"))

    def _page(self):
        from django.urls import reverse
        return self.client.get(reverse('keep-about')).content.decode()

    def _cards(self, body):
        """Each card's HTML, in page order -- each cut at the end of its artist
        row, so the last one doesn't run on into the rest of the page."""
        cards = []
        for chunk in body.split("class='artwork-card h-100'")[1:]:
            row = chunk.find("class='artwork-card-artist'")
            cards.append(chunk[:chunk.find("</div>", row) + len("</div>")])
        return cards

    def test_one_card_per_artist_with_their_pieces_in_a_carousel(self):
        self._art(self.alice, "Alice One", 1)
        self._art(self.bob, "Bob One", 2)        # between Alice's pieces
        self._art(self.alice, "Alice Two", 3)
        self._art(self.alice, "Alice Hidden", 4, show=False)

        cards = self._cards(self._page())
        self.assertEqual(len(cards), 2)
        alice, bob = cards
        self.assertIn("Alice One by Alice", alice)
        self.assertIn("Alice Two by Alice", alice)
        self.assertNotIn("Alice Hidden", alice)
        self.assertEqual(alice.count("carousel-item"), 2)
        self.assertIn("carousel-control-prev", alice)
        self.assertIn("carousel-control-next", alice)
        self.assertEqual(bob.count("carousel-item"), 1)
        self.assertNotIn("carousel-control-prev", bob)

    def test_the_card_links_to_the_credit_url(self):
        self._art(self.alice, "Alice One", 1)
        self._art(self.bob, "Bob One", 2)
        alice, bob = self._cards(self._page())
        # Alice's own link, in a new tab.
        self.assertIn("href='https://alice.example/art' target='_blank'", alice)
        # Bob has none: his profile here, in the same tab.
        self.assertIn(f"href='{self.bob.get_absolute_url()}'>", bob)
        self.assertNotIn("_blank", bob)

    def test_the_resized_copy_is_served(self):
        art = self._art(self.alice, "Alice One", 1)
        art.refresh_from_db()
        self.assertTrue(art.small_image)
        self.assertIn(art.small_image.url, self._page())

    def test_the_art_section_has_its_heading_and_component_credit(self):
        """Without any art rows there are no cards, but the section -- its
        heading and the credit for the component art -- is still there."""
        body = self._page()
        self.assertNotIn("artwork-card h-100", body)
        self.assertIn(">Artwork</h2>", body)
        self.assertIn("Kyle Ferrin", body)


class ProfileCreditUrlTests(TestCase):
    def test_credit_link_then_profile_then_none(self):
        from the_gatehouse.models import Profile
        linked = Profile.objects.create(discord="cuone", credit_link="https://x.example")
        plain = Profile.objects.create(discord="cutwo")
        self.assertEqual(linked.credit_url, "https://x.example")
        self.assertEqual(plain.credit_url, plain.get_absolute_url())
        Profile.objects.filter(pk=plain.pk).update(slug=None)
        plain.refresh_from_db()
        self.assertIsNone(plain.credit_url)
