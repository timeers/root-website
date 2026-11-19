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


# ── Tabletop Simulator loader ───────────────────────────────────────────────

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from the_gatehouse.models import Profile
from the_keep.admin import generate_tts_json
from the_keep.models import Faction, Post, StatusChoices
from the_keep.services.tts import SiteURLRequest
from the_keep.utils import tts_json_hash, validate_tts_save

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _tts_save(nickname='Board', x=1.5, y=2, z=-3):
    return {'SaveName': 'Test', 'ObjectStates': [{
        'Name': 'Custom_Tile', 'Nickname': nickname,
        'Transform': {'posX': x, 'posY': y, 'posZ': z},
    }]}


class TTSJsonHelperTests(TestCase):
    def test_validator_accepts_a_save_file(self):
        validate_tts_save(_tts_save())

    def test_validator_rejects_other_shapes(self):
        for bad in ([1], {'Name': 'x'}, {'ObjectStates': []}, {'ObjectStates': [{'x': 1}]}, 'str'):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_tts_save(bad)

    def test_hash_ignores_key_order(self):
        # Postgres jsonb reorders keys, so a re-read value must hash the same.
        self.assertEqual(tts_json_hash({'a': 1, 'b': [{'c': 2, 'd': 3}]}),
                         tts_json_hash({'b': [{'d': 3, 'c': 2}], 'a': 1}))
        self.assertEqual(tts_json_hash(None), '')

    @override_settings(SITE_URL='https://example.test')
    def test_site_url_request_pins_host(self):
        self.assertEqual(SiteURLRequest().build_absolute_uri('/media/x.webp'),
                         'https://example.test/media/x.webp')


@override_settings(CACHES=_LOCMEM, SITE_URL='https://example.test')
class TTSLoaderApiTests(TestCase):
    def setUp(self):
        cache.clear()
        from the_forge.models import ForgedFaction
        self.designer = Profile.objects.create(discord='ttsdesigner', display_name='TTS Designer')
        self.listed = Faction.objects.create(title='Listed Fox', animal='Fox', designer=self.designer,
                                             component='Faction', status=StatusChoices.STABLE,
                                             tts_json=_tts_save())
        # Has JSON but isn't publicly active, so the library must hide it.
        self.rejected = Faction.objects.create(title='Rejected Fox', animal='Fox', designer=self.designer,
                                               component='Faction', status=StatusChoices.REJECTED,
                                               tts_json=_tts_save())
        Faction.objects.create(title='Submitted Fox', animal='Fox', designer=self.designer,
                               component='Faction', status=StatusChoices.SUBMITTED, tts_json=_tts_save())
        self.unlisted = Faction.objects.create(title='Plain Fox', animal='Fox', designer=self.designer,
                                               component='Faction')
        self.published = ForgedFaction.objects.create(designer=self.designer, faction_name='Forge Pub',
                                                      tts_status=ForgedFaction.TTSStatus.APPROVED,
                                                      tts_json=_tts_save('Forge Board'))
        # Submitted and rejected factions have snapshots but aren't approved.
        self.hidden = ForgedFaction.objects.create(designer=self.designer, faction_name='Forge Hidden',
                                                   tts_status=ForgedFaction.TTSStatus.SUBMITTED,
                                                   tts_json=_tts_save())
        ForgedFaction.objects.create(designer=self.designer, faction_name='Forge Rejected',
                                     tts_status=ForgedFaction.TTSStatus.REJECTED, tts_json=_tts_save())

    def test_catalog_lists_only_loadable_items_without_blobs(self):
        response = self.client.get(reverse('api-tts-catalog'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([p['slug'] for p in data['keep']], [self.listed.slug])
        self.assertEqual([f['id'] for f in data['forge']], [self.published.pk])
        # The Forge tab's type buttons are built from this field.
        self.assertEqual(data['forge'][0]['component'], 'Faction')
        self.assertNotIn('ObjectStates', response.content.decode())

    def test_keep_detail_returns_pre_encoded_objects_with_positions(self):
        response = self.client.get(reverse('api-tts-keep-detail', args=[self.listed.slug]))
        self.assertEqual(response.status_code, 200)
        obj = response.json()['objects'][0]
        self.assertEqual((obj['x'], obj['y'], obj['z']), (1.5, 2.0, -3.0))
        self.assertEqual(json.loads(obj['json'])['Nickname'], 'Board')

    def test_unlisted_items_404(self):
        self.assertEqual(self.client.get(reverse('api-tts-keep-detail', args=[self.unlisted.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('api-tts-keep-detail', args=[self.rejected.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('api-tts-forge-detail', args=[self.hidden.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('api-tts-forge-detail', args=[self.published.pk])).status_code, 200)

    def test_throttled_for_anonymous_callers(self):
        # DRF reads THROTTLE_RATES at import, so patch the dict, not settings.
        from rest_framework.throttling import ScopedRateThrottle
        url = reverse('api-tts-catalog')
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {'tts_loader': '2/hour'}):
            codes = [self.client.get(url).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class GenerateTTSJsonActionTests(TestCase):
    def setUp(self):
        self.designer = Profile.objects.create(discord='actiondesigner')
        self.modeladmin = mock.Mock()

    def test_skips_forge_linked_and_empty_posts(self):
        from the_forge.models import ForgedFaction
        linked = Faction.objects.create(title='Linked Fox', animal='Fox', designer=self.designer,
                                        component='Faction')
        ForgedFaction.objects.create(designer=self.designer, faction_name='Linked Fox',
                                     published_post=linked)
        # A Tweak has nothing for the keep builder to export.
        from the_keep.models import Tweak
        tweak = Tweak.objects.create(title='Rule', designer=self.designer, component='Tweak')

        generate_tts_json(self.modeladmin, mock.Mock(), Post.objects.filter(pk__in=[linked.pk, tweak.pk]))

        self.assertIsNone(Post.objects.get(pk=linked.pk).tts_json)
        self.assertIsNone(Post.objects.get(pk=tweak.pk).tts_json)
        message = self.modeladmin.message_user.call_args_list[-1].args[1]
        self.assertIn('update via forge sync', message)
        self.assertIn('nothing to export', message)


# ── TTS Library object served from tts/loader.lua ───────────────────────────

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from the_keep import api_views

_LOADER_FIXTURE = '''-- fixture
LOADER_VERSION = "dev"
BASE_URL = "https://www.therootdatabase.com"
function onLoad() end
'''


@override_settings(CACHES=_LOCMEM, SITE_URL='https://example.test', DEBUG=False)
class TTSLoaderObjectTests(TestCase):
    def setUp(self):
        cache.clear()
        api_views._loader_cache.clear()
        self.base = Path(tempfile.mkdtemp(prefix='tts-loader-test-'))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        (self.base / 'tts').mkdir()
        self.script_path = self.base / 'tts' / 'loader.lua'
        self.script_path.write_text(_LOADER_FIXTURE)
        override = override_settings(BASE_DIR=self.base)
        override.enable()
        self.addCleanup(override.disable)

    def _version(self):
        return self.client.get(reverse('api-tts-loader-version')).json()['version']

    def test_version_follows_the_file(self):
        first = self._version()
        self.assertRegex(first, r'^[0-9a-f]{12}$')
        self.script_path.write_text(_LOADER_FIXTURE + '-- edited\n')
        os.utime(self.script_path, (1, 1))  # force a new mtime even within the same second
        self.assertNotEqual(self._version(), first)

    def test_script_has_version_and_site_url_filled_in(self):
        version = self._version()
        response = self.client.get(reverse('api-tts-loader-script'))
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        body = response.content.decode()
        self.assertIn(f'LOADER_VERSION = "{version}"', body)
        # Always SITE_URL, never the request host (TTS can't use a dev server).
        self.assertIn('BASE_URL = "https://example.test"', body)
        self.assertNotIn('"dev"', body)

    def test_dev_download_points_at_site_url_and_never_self_updates(self):
        with override_settings(DEBUG=True):
            save = json.loads(self.client.get(reverse('api-tts-loader-object')).content)
        tile = save['ObjectStates'][0]
        self.assertIn('LOADER_VERSION = "dev"', tile['LuaScript'])
        self.assertIn('BASE_URL = "https://example.test"', tile['LuaScript'])
        self.assertTrue(tile['CustomImage']['ImageURL'].startswith('https://example.test/static/'))

    def test_object_carries_the_served_script(self):
        script = self.client.get(reverse('api-tts-loader-script')).content.decode()
        response = self.client.get(reverse('api-tts-loader-object'))
        self.assertIn('attachment', response['Content-Disposition'])
        save = json.loads(response.content)
        validate_tts_save(save)
        tile = save['ObjectStates'][0]
        self.assertEqual(tile['LuaScript'], script)
        self.assertEqual(tile['Nickname'], 'Root Database Library')
        self.assertTrue(tile['Locked'])

    def test_missing_marker_is_never_served(self):
        self.script_path.write_text('BASE_URL = "x"\n')
        with self.assertRaises(ImproperlyConfigured):
            api_views._loader_source()


class RealLoaderScriptTests(TestCase):
    def test_repo_script_keeps_both_markers(self):
        # Editing tts/loader.lua must not break the self-update's substitution.
        api_views._loader_cache.clear()
        version, raw = api_views._loader_source()
        self.assertIn('LOADER_VERSION = "dev"', raw)
        self.assertRegex(version, r'^[0-9a-f]{12}$')


@override_settings(CACHES=_LOCMEM)
class TTSVPPanelTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_detail_lists_vp_rows_for_faction_markers(self):
        designer = Profile.objects.create(discord='vprows')
        save = {'ObjectStates': [
            {'Name': 'Custom_Tile', 'Nickname': 'Fox Folk', 'Transform': {'posX': 0, 'posY': 0, 'posZ': 0}},
            {'Name': 'Custom_Token', 'Nickname': 'Fox Folk VP', 'Transform': {'posX': 13, 'posY': 1, 'posZ': 12}},
            {'Name': 'Custom_Token', 'Nickname': 'Fox Folk Relationship', 'Transform': {'posX': 14.5, 'posY': 1, 'posZ': 12}},
        ]}
        post = Faction.objects.create(title='Fox Folk', animal='Fox', designer=designer, component='Faction',
                                      status=StatusChoices.STABLE, tts_json=save)
        data = self.client.get(reverse('api-tts-keep-detail', args=[post.slug])).json()
        self.assertEqual(data['vp_rows'], [{'row': 'Fox Folk', 'x': 13.0, 'y': 1.0, 'z': 12.0, 'board': None}])
        self.assertEqual(len(data['objects']), 3)

    def test_panel_template_is_rtt_panel_with_placeholder(self):
        response = self.client.get(reverse('api-tts-vp-panel'))
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        panel = json.loads(response.content)
        self.assertEqual(panel['LuaScriptState'], '__VP_ROW__')
        self.assertNotIn('GUID', panel)
        self.assertIn('RTT VP Panel', panel['Tags'])
        # Unchanged RTT script: relays to the coordinator the loader checks for.
        self.assertIn('RTT_COORD_GUID = "bab7e1"', panel['LuaScript'])

    def test_panel_template_has_no_faction_specific_assets(self):
        # The panel builds its "vp<row>" asset from its own marker at load time.
        panel = json.loads(self.client.get(reverse('api-tts-vp-panel')).content)
        self.assertEqual([a['Name'] for a in panel['CustomUIAssets']], ['pnlframe'])

    def test_vp_row_points_at_its_faction_board(self):
        designer = Profile.objects.create(discord='vpboard')
        save = {'ObjectStates': [
            {'Name': 'Custom_Token', 'Nickname': 'Fox Folk VP', 'Transform': {'posX': 13, 'posY': 1, 'posZ': 12}},
            {'Name': 'Custom_Tile', 'Nickname': 'Fox Folk - Faction Board', 'Transform': {'posX': 0, 'posY': 0, 'posZ': 0}},
        ]}
        post = Faction.objects.create(title='Fox Folk', animal='Fox', designer=designer, component='Faction',
                                      status=StatusChoices.STABLE, tts_json=save)
        data = self.client.get(reverse('api-tts-keep-detail', args=[post.slug])).json()
        self.assertEqual(data['vp_rows'][0]['board'], 2)  # 1-based, as Lua indexes the spawned objects
