import hashlib
import json
import re
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


@mock.patch('the_keep.services.tts.generate_sprite_sheet', return_value='/media/sheet.png')
class TTSDeckBuilderTests(TestCase):
    """Decks built like the official RTT decks (see tts/RTT_EXTERNAL_API.md)."""

    @staticmethod
    def _sprite_deck(cards, deck_id=1, group_name='Cards'):
        from the_keep.services.tts import TTSSpriteDeck
        group = SimpleNamespace(name=group_name, back_image='/media/back.png')
        carddeck = SimpleNamespace(group=group, cards_in_deck=cards, card_count=len(cards))
        return TTSSpriteDeck(carddeck, deck_id)

    @staticmethod
    def _card(name, *tags):
        return SimpleNamespace(name=name, tags=list(tags))

    def test_deck_matches_official_conventions(self, _sheet):
        deck = self._sprite_deck([self._card('Sword', 'Mouse'), self._card('Ambush', 'Bird')]).to_object()
        self.assertEqual(deck['Name'], 'Deck')
        self.assertEqual(deck['Nickname'], 'Cards')
        self.assertEqual([c['Nickname'] for c in deck['ContainedObjects']], ['Sword', 'Ambush'])
        self.assertEqual([c['Description'] for c in deck['ContainedObjects']], ['Mouse', 'Bird'])
        self.assertEqual(deck['DeckIDs'], [100, 101])

    def test_description_lists_only_suits_in_fixed_order(self, _sheet):
        cards = self._sprite_deck([self._card('Mixed', 'Mouse', 'Fox'), self._card('Blank')]).card_objects()
        self.assertEqual([c['Description'] for c in cards], ['Fox Mouse', ''])

    def test_dominance_card_is_named_dominance(self, _sheet):
        cards = self._sprite_deck([self._card('Fox Dominance', 'Fox', 'Dominance'),
                                   self._card('Bird Dom', 'Dominance', 'Bird')]).card_objects()
        self.assertEqual([(c['Nickname'], c['Description']) for c in cards],
                         [('Dominance', 'Fox'), ('Dominance', 'Bird')])

    def test_single_card_is_a_bare_card(self, _sheet):
        card = self._sprite_deck([self._card('', 'Fox')]).to_object()
        self.assertEqual((card['Name'], card['Nickname']), ('Card', 'Cards'))

    def test_group_over_99_cards_is_one_deck(self, _sheet):
        from the_keep.services.tts import TTSDeckGroup
        first = self._sprite_deck([self._card(f'A{i}', 'Fox') for i in range(99)], deck_id=3)
        second = self._sprite_deck([self._card(f'B{i}', 'Fox') for i in range(21)], deck_id=4)
        group = SimpleNamespace(name='Cards')
        with mock.patch.object(TTSDeckGroup, 'build', return_value=[first, second]):
            deck, next_id = TTSDeckGroup(group).to_object(3)
        self.assertEqual(next_id, 5)
        self.assertEqual(deck['Name'], 'Deck')
        self.assertEqual(set(deck['CustomDeck']), {'3', '4'})
        self.assertEqual(len(deck['ContainedObjects']), 120)
        self.assertEqual((deck['DeckIDs'][98], deck['DeckIDs'][99]), (398, 400))

    def test_empty_group_gives_no_object(self, _sheet):
        from the_keep.services.tts import TTSDeckGroup
        with mock.patch.object(TTSDeckGroup, 'build', return_value=[]):
            self.assertEqual(TTSDeckGroup(SimpleNamespace(name='x')).to_object(7), (None, 7))


@override_settings(CACHES=_LOCMEM, SITE_URL='https://example.test')
class TTSLoaderApiTests(TestCase):
    def setUp(self):
        cache.clear()
        from the_forge.models import ForgedFaction
        self.designer = Profile.objects.create(discord='ttsdesigner', display_name='TTS Designer')
        self.listed = Faction.objects.create(title='Listed Fox', animal='Fox', designer=self.designer,
                                             component='Faction', status=StatusChoices.STABLE,
                                             tts_json=_tts_save())
        # Titles sort before "Listed Fox", so the catalog order proves it groups
        # by status (Stable, Testing, ..., Inactive), not alphabetically.
        self.testing = Faction.objects.create(title='Aardvark Folk', animal='Fox', designer=self.designer,
                                              component='Faction', status=StatusChoices.TESTING,
                                              tts_json=_tts_save())
        self.inactive = Faction.objects.create(title='Ant Folk', animal='Fox', designer=self.designer,
                                               component='Faction', status=StatusChoices.INACTIVE,
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
        self.assertEqual([p['slug'] for p in data['keep']],
                         [self.listed.slug, self.testing.slug, self.inactive.slug])
        self.assertEqual([(p['status'], p['status_label']) for p in data['keep']],
                         [('1', 'Stable'), ('2', 'Testing'), ('4', 'Inactive')])
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
        self.assertIn('Forge sync page', message)
        self.assertIn('Nothing to generate', message)


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


@override_settings(CACHES=_LOCMEM)
class TTSRTTPayloadTests(TestCase):
    """Detail payloads: stored JSON served as is, plus what tts_info worked out."""

    def setUp(self):
        cache.clear()
        self.designer = Profile.objects.create(discord='rttpayload')

    def _detail(self, post):
        return self.client.get(reverse('api-tts-keep-detail', args=[post.slug])).json()

    @staticmethod
    def _obj(payload, i):
        return json.loads(payload['objects'][i]['json'])

    def _stored(self, model, states, **kwargs):
        from the_keep.services.tts_store import store_tts_json
        post = model.objects.create(designer=self.designer, status=StatusChoices.STABLE, **kwargs)
        store_tts_json(post, {'ObjectStates': states, **kwargs.pop('extra', {})}, source='uploaded')
        return post

    def test_map_is_served_as_stored_with_board_and_shuffle(self):
        from the_keep.models import Map
        snaps = [{'Position': {'x': 0, 'y': 0, 'z': 0}}] * 3
        states = [
            {'Name': 'Custom_Token', 'Nickname': 'Ruin', 'Tags': ['Ruin'], 'Transform': {'posX': 5}},
            {'Name': 'Custom_Tile', 'Nickname': 'Board', 'AttachedSnapPoints': snaps, 'Transform': {'posX': 0}},
        ]
        post = self._stored(Map, states, title='Fall Map')
        data = self._detail(post)
        self.assertEqual((data['component'], data['slug'], data['map_board']), ('Map', post.slug, 2))
        self.assertTrue(data['shuffle'])
        self.assertNotIn('layout', data)
        # Nothing is added when serving.
        self.assertEqual(self._obj(data, 0), states[0])
        self.assertEqual(self._obj(data, 1), states[1])

    def test_board_layout_is_passed_through(self):
        from the_keep.models import Map
        from the_keep.services.tts_store import store_tts_json
        layout = {'clearings': [[1.5, -2.0], [3, 4]], 'relics': [[0, 0]]}
        post = Map.objects.create(title='Lake', designer=self.designer, status=StatusChoices.STABLE)
        store_tts_json(post, {'ObjectStates': [{'Name': 'Custom_Tile'}], 'BoardLayout': layout}, source='uploaded')
        self.assertEqual(self._detail(post)['layout'], layout)

    def test_deck_roles_and_dominance_come_from_tts_info(self):
        from the_keep.models import Deck
        cards = lambda n, name='Sword': [{'Name': 'Card', 'Nickname': name} for _ in range(n)]
        states = [
            {'Name': 'Deck', 'ContainedObjects': cards(3)},
            {'Name': 'Deck', 'ContainedObjects': cards(20) + cards(1, 'Dominance')},
            {'Name': 'Custom_Tile'},
        ]
        post = self._stored(Deck, states, title='Big Deck', card_total=54)
        data = self._detail(post)
        self.assertEqual([o.get('deck_role') for o in data['objects']], ['accessory', 'main', None])
        self.assertTrue(data['dominance'])
        self.assertEqual(self._obj(data, 1), states[1])   # no tags added

    def test_missing_tts_info_leaves_extras_out(self):
        from the_keep.models import Deck
        post = Deck.objects.create(title='Old Deck', designer=self.designer, status=StatusChoices.STABLE,
                                   card_total=54, tts_json={'ObjectStates': [{'Name': 'Deck'}]})
        data = self._detail(post)
        self.assertNotIn('deck_role', data['objects'][0])
        self.assertNotIn('dominance', data)

    def test_factions_are_served_as_stored(self):
        post = Faction.objects.create(title='Fox Folk', animal='Fox', designer=self.designer, component='Faction',
                                      status=StatusChoices.STABLE, tts_json=_tts_save())
        data = self._detail(post)
        self.assertEqual(data['component'], 'Faction')
        self.assertNotIn('Tags', json.loads(data['objects'][0]['json']))


class TTSInspectTests(TestCase):
    """Read-only analysis: tts_info and the upload review's warnings."""

    def setUp(self):
        self.designer = Profile.objects.create(discord='inspector')

    def _post(self, component, **kwargs):
        return SimpleNamespace(component=component, slug=kwargs.get('slug', 'my-post'))

    def test_describe_deck_roles_by_tag_and_size(self):
        from the_keep.services.tts_inspect import describe_tts_json
        cards = [{'Name': 'Card'}] * 5
        save = {'ObjectStates': [
            {'Name': 'Deck', 'ContainedObjects': cards * 4, 'Tags': ['Deck Accessory']},
            {'Name': 'Deck', 'ContainedObjects': cards},
            {'Name': 'Custom_Token'},
        ]}
        info = describe_tts_json(self._post('Deck'), save, source='generated')
        # The tagged accessory is never main, even with more cards.
        self.assertEqual(info['deck_roles'], ['accessory', 'main', None])
        self.assertFalse(info['dominance'])
        self.assertEqual((info['source'], info['object_count']), ('generated', 3))

    def test_describe_map_board_and_shuffle(self):
        from the_keep.services.tts_inspect import describe_tts_json
        save = {'ObjectStates': [{'Name': 'a'}, {'Name': 'board', 'AttachedSnapPoints': [{}]}]}
        info = describe_tts_json(self._post('Map'), save, source='uploaded', filename='m.json')
        self.assertEqual((info['map_board'], info['shuffle'], info['filename']), (2, False, 'm.json'))

    def test_recheck_keeps_previous_source(self):
        from the_keep.services.tts_inspect import describe_tts_json
        previous = {'source': 'uploaded', 'updated_by': 'Tim', 'updated_at': 'x', 'filename': 'f.json'}
        info = describe_tts_json(self._post('Tweak'), {'ObjectStates': [{'Name': 'a'}]}, previous=previous)
        self.assertEqual({k: info[k] for k in previous}, previous)
        self.assertNotIn('source', describe_tts_json(self._post('Tweak'), {'ObjectStates': [{'Name': 'a'}]}))

    def test_scripts_found_at_every_depth(self):
        from the_keep.services.tts import LOCK_ON_REST_LUA
        from the_keep.services.tts_inspect import inspect_tts_json
        save = {
            'LuaScript': 'print("global")',
            'ObjectStates': [
                {'Name': 'Custom_Tile', 'Nickname': 'Board', 'LuaScript': LOCK_ON_REST_LUA},
                {'Name': 'Deck', 'ContainedObjects': [{'Name': 'Card', 'Nickname': 'Sneaky', 'LuaScript': 'x()'}]},
                {'Name': 'Custom_Tile', 'States': {'2': {'Name': 'Custom_Tile', 'Nickname': 'Back', 'LuaScript': 'y()'}}},
                {'Name': 'Custom_Model', 'ChildObjects': [{'Name': 'Child', 'LuaScript': 'z()'}]},
                {'Name': 'Blank', 'LuaScript': '   '},
            ],
        }
        scripts = inspect_tts_json(self._post('Map'), save)['scripts']
        self.assertEqual([(s['name'], s['known']) for s in scripts], [
            ('Save file (global script)', False), ('Board', True), ('Sneaky', False),
            ('Back', False), ('Child', False)])

    @override_settings(SITE_URL='https://www.example.test')
    def test_untrusted_hosts(self):
        from the_keep.services.tts_inspect import inspect_tts_json
        save = {'ObjectStates': [
            {'Name': 'a', 'CustomImage': {'ImageURL': '{verifycache}https://example.test/media/a.png',
                                          'ImageSecondaryURL': 'https://www.example.test/b.png'}},
            {'Name': 'b', 'CustomDeck': {'1': {'FaceURL': 'https://cloud-3.steamusercontent.com/ugc/1/',
                                               'BackURL': 'https://i.imgur.com/x.png'}}},
            {'Name': 'c', 'CustomMesh': {'MeshURL': 'https://mrdrouf.github.io/art/1'},
             'XmlUI': '<Image image="https://evil.example/pixel.gif" />'},
            {'Name': 'd', 'CustomImage': {'ImageURL': 'https://i.imgur.com/y.png'}},
        ]}
        hosts = inspect_tts_json(self._post('Map'), save)['untrusted_hosts']
        self.assertEqual([(h['host'], h['count']) for h in hosts], [('i.imgur.com', 2), ('evil.example', 1)])

    def test_deck_notes(self):
        from the_keep.services.tts_inspect import inspect_tts_json
        save = {'ObjectStates': [{'Name': 'Deck', 'ContainedObjects': [
            {'Name': 'Card', 'Nickname': 'Fox Dominance', 'Description': 'Fox'},
            {'Name': 'Card', 'Nickname': 'Sword', 'Description': 'Mouse craftable'},
        ]}]}
        notes = ' '.join(inspect_tts_json(self._post('Deck'), save)['notes'])
        self.assertIn('Deck Object', notes)
        self.assertIn('rdb:deck:my-post', notes)
        self.assertIn('Fox Dominance', notes)
        self.assertIn('Sword', notes)

    def test_map_notes(self):
        from the_keep.services.tts_inspect import inspect_tts_json
        save = {'ObjectStates': [{'Name': 'Custom_Token', 'Tags': ['Clearing Marker']}]}
        notes = ' '.join(inspect_tts_json(self._post('Map'), save, fixed_clearings=True)['notes'])
        self.assertIn('Map Object', notes)
        self.assertIn('RTT Map', notes)
        self.assertIn('fixed clearings', notes)


class TTSJsonAdminMixinTests(TestCase):
    def test_tts_json_is_not_editable_in_the_admin(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from the_keep.admin import DeckAdmin, MapAdmin, FactionAdmin
        from the_keep.models import Deck, Map
        request = RequestFactory().get('/')
        request.user = mock.Mock(is_superuser=True)
        for admin_class, model in ((DeckAdmin, Deck), (MapAdmin, Map), (FactionAdmin, Faction)):
            with self.subTest(admin=admin_class.__name__):
                model_admin = admin_class(model, AdminSite())
                self.assertIn('tts_json', model_admin.get_exclude(request))
                self.assertIn('tts_info', model_admin.get_exclude(request))
                self.assertIn('tts_json_summary', model_admin.get_readonly_fields(request))
                form = model_admin.get_form(request)
                self.assertNotIn('tts_json', form.base_fields)


@mock.patch('the_keep.services.tts.generate_sprite_sheet', return_value='/media/sheet.png')
class TTSSharedDeckBuilderTests(TestCase):
    """build_tts_objects gives a Deck post the RTT table's conventions."""

    def setUp(self):
        self.designer = Profile.objects.create(discord='deckbuilder')

    def _built(self, component, groups):
        from the_keep.services import tts
        post = SimpleNamespace(component=component, title='Big Deck', slug='big-deck',
                               decks=mock.Mock(filter=mock.Mock(return_value=groups)), language=None)
        with mock.patch.object(tts.TTSDeckGroup, 'to_object',
                               side_effect=lambda self, i: (self.group.obj, i + 1), autospec=True):
            return tts.build_tts_objects(post)

    @staticmethod
    def _group(n):
        cards = [{'Name': 'Card', 'Nickname': f'C{i}', 'Tags': []} for i in range(n)]
        from the_keep.services.tts import DEFAULT_CARD_TRANSFORM
        return SimpleNamespace(obj={'Name': 'Deck', 'Nickname': 'Group', 'ContainedObjects': cards,
                                    'Transform': dict(DEFAULT_CARD_TRANSFORM)})

    def test_deck_post_conventions(self, _sheet):
        small, big = self._group(3), self._group(30)
        accessory, main = self._built('Deck', [small, big])
        self.assertEqual((main['Nickname'], main['GMNotes']), ('Big Deck', 'rdb:deck:big-deck'))
        self.assertEqual(main['Tags'], ['Deck Object'])
        self.assertEqual(accessory['Tags'], ['Deck Object', 'Deck Accessory'])
        self.assertNotIn('GMNotes', accessory)
        self.assertTrue(all(c['Tags'] == ['Deck Object'] for d in (main, accessory) for c in d['ContainedObjects']))
        self.assertEqual((main['Transform']['rotZ'], accessory['Transform']['rotZ']), (180.0, 180.0))
        self.assertEqual((main['Transform']['posX'], accessory['Transform']['posX']), (0.0, 3.0))

    def test_other_posts_decks_unchanged(self, _sheet):
        # Never "Deck Object" outside a Deck post: the coordinator deletes those
        # whenever the shared deck is replaced.
        (deck,) = self._built('Tweak', [self._group(5)])
        self.assertNotIn('Tags', deck)
        self.assertEqual(deck['Transform']['rotZ'], 0.0)
        self.assertNotIn('GMNotes', deck)


@override_settings(CACHES=_LOCMEM, SITE_URL='https://www.example.test')
class PostTTSJsonPageTests(TestCase):
    def setUp(self):
        cache.clear()
        from django.contrib.auth.models import User
        from the_gatehouse.signals import user_logged_in_handler
        from django.contrib.auth.signals import user_logged_in
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        self.admin_user = User.objects.create_user('ttsadmin')
        self.admin_user.profile.group = 'A'   # Profile.admin is derived from the group
        self.admin_user.profile.save()
        self.client.force_login(self.admin_user)
        from the_keep.models import Deck
        self.post = Deck.objects.create(title='Big Deck', designer=self.admin_user.profile,
                                        status=StatusChoices.STABLE, card_total=54)

    def _url(self, name='manage-tts-json', **kwargs):
        return reverse(name, kwargs={'slug': self.post.slug, **kwargs})

    def _upload(self, data, name='deck.json'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        return self.client.post(self._url(), {'action': 'upload', 'tts_file': SimpleUploadedFile(name, raw)})

    def test_non_admin_gets_403(self):
        from django.contrib.auth.models import User
        other = User.objects.create_user('notadmin')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self._url()).status_code, 403)

    def test_bad_uploads_store_nothing(self):
        from the_keep.models import TTSJsonUpload
        for raw in (b'{not json', b'{"ObjectStates": []}', b'{"ObjectStates": [{"Name": "a", "x": NaN}]}',
                    '{"ObjectStates": [{"Name": "é"}]}'.encode('latin-1')):
            with self.subTest(raw=raw[:30]):
                response = self._upload(raw)
                self.assertRedirects(response, self._url())
        self.assertFalse(TTSJsonUpload.objects.exists())

    def test_upload_review_confirm_stores_json_unchanged(self):
        from the_keep.models import Post, TTSJsonUpload
        data = {'ObjectStates': [{'Name': 'Deck', 'Nickname': 'Mine', 'LuaScript': 'print("<script>hi</script>")',
                                  'CustomDeck': {'1': {'FaceURL': 'https://i.imgur.com/x.png'}},
                                  'ContainedObjects': [{'Name': 'Card'}, {'Name': 'Card'}]}],
                'SaveName': 'whatever'}
        response = self._upload(data)
        pending = TTSJsonUpload.objects.get()
        review = self._url('post-tts-json-review', upload_id=pending.pk)
        self.assertRedirects(response, review)

        page = self.client.get(review).content.decode()
        self.assertIn('i.imgur.com', page)
        self.assertIn('&lt;script&gt;hi&lt;/script&gt;', page)
        self.assertNotIn('<script>hi</script>', page)

        # Warnings need the acknowledgement.
        self.client.post(review, {'action': 'confirm'})
        self.assertIsNone(Post.objects.get(pk=self.post.pk).tts_json)

        response = self.client.post(review, {'action': 'confirm', 'acknowledge': 'on'})
        self.assertRedirects(response, self._url())
        stored = Post.objects.get(pk=self.post.pk)
        self.assertEqual(stored.tts_json, data)
        self.assertEqual((stored.tts_info['source'], stored.tts_info['filename']), ('uploaded', 'deck.json'))
        self.assertEqual(stored.tts_info['deck_roles'], ['main'])
        pending.refresh_from_db()
        self.assertIsNotNone(pending.confirmed_at)
        self.assertEqual(self.client.post(review, {'action': 'confirm', 'acknowledge': 'on'}).status_code, 404)

        page = self.client.get(self._url()).content.decode()
        self.assertIn('Uploaded', page)
        self.assertIn('value="remove"', page)   # the manage page keeps its actions
        download = self.client.get(self._url('download-tts-json'))
        self.assertEqual(json.loads(download.content), data)

    def test_review_belongs_to_its_post(self):
        from the_keep.models import Deck, TTSJsonUpload
        other = Deck.objects.create(title='Other', designer=self.admin_user.profile, card_total=1)
        pending = TTSJsonUpload.objects.create(post=other, tts_json={'ObjectStates': [{'Name': 'a'}]})
        self.assertEqual(self.client.get(self._url('post-tts-json-review', upload_id=pending.pk)).status_code, 404)

    def test_cancel_remove_and_recheck(self):
        from the_keep.models import Post, TTSJsonUpload
        self._upload({'ObjectStates': [{'Name': 'Card'}]})
        pending = TTSJsonUpload.objects.get()
        self.client.post(self._url('post-tts-json-review', upload_id=pending.pk), {'action': 'cancel'})
        self.assertFalse(TTSJsonUpload.objects.exists())

        from the_keep.services.tts_store import store_tts_json
        store_tts_json(self.post, {'ObjectStates': [{'Name': 'Card'}]}, source='uploaded', filename='a.json')
        Post.objects.filter(pk=self.post.pk).update(tts_info={'source': 'uploaded', 'filename': 'a.json'})
        self.client.post(self._url(), {'action': 'recheck'})
        info = Post.objects.get(pk=self.post.pk).tts_info
        self.assertEqual((info['source'], info['filename'], info['deck_roles']), ('uploaded', 'a.json', ['main']))

        self.client.post(self._url(), {'action': 'remove'})
        stored = Post.objects.get(pk=self.post.pk)
        self.assertIsNone(stored.tts_json)
        self.assertIsNone(stored.tts_info)

    def test_generate_refused_for_maps_and_forge_factions(self):
        from the_forge.models import ForgedFaction
        from the_keep.models import Map
        map_post = Map.objects.create(title='Lake', designer=self.admin_user.profile)
        self.client.post(reverse('manage-tts-json', kwargs={'slug': map_post.slug}), {'action': 'generate'})
        self.assertIsNone(Post.objects.get(pk=map_post.pk).tts_json)
        faction = Faction.objects.create(title='Fox Folk', animal='Fox', designer=self.admin_user.profile,
                                         component='Faction')
        ForgedFaction.objects.create(designer=self.admin_user.profile, faction_name='Fox Folk', published_post=faction)
        page = self.client.get(reverse('manage-tts-json', kwargs={'slug': faction.slug})).content.decode()
        self.assertIn('Forge sync page', page)

    def test_generate_keeps_json_when_nothing_to_build(self):
        from the_keep.services.tts_store import store_tts_json
        store_tts_json(self.post, {'ObjectStates': [{'Name': 'Card'}]}, source='uploaded')
        self.client.post(self._url(), {'action': 'generate'})   # the deck has no card groups
        self.assertEqual(Post.objects.get(pk=self.post.pk).tts_json, {'ObjectStates': [{'Name': 'Card'}]})

    def test_view_page_is_read_only_and_public_for_listed_posts(self):
        from the_keep.services.tts_store import store_tts_json
        store_tts_json(self.post, {'ObjectStates': [{'Name': 'Card', 'Nickname': 'Visible'}]}, source='generated')
        self.client.logout()
        page = self.client.get(reverse('view-tts-json', kwargs={'slug': self.post.slug}))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('Visible', html)
        self.assertIn('Generated', html)
        self.assertIn('Download', html)
        self.assertNotIn('value="remove"', html)
        self.assertNotIn('value="recheck"', html)
        download = self.client.get(reverse('download-tts-json', kwargs={'slug': self.post.slug}))
        self.assertEqual(download.status_code, 200)
        # The post page links to it, for admins only (for now).
        link = reverse('view-tts-json', kwargs={'slug': self.post.slug})
        self.assertNotIn(link, self.client.get(self.post.get_absolute_url()).content.decode())
        self.client.force_login(self.admin_user)
        self.assertIn(link, self.client.get(self.post.get_absolute_url()).content.decode())

    def test_view_page_for_unlisted_posts_needs_an_editor(self):
        from the_keep.services.tts_store import store_tts_json
        Post.objects.filter(pk=self.post.pk).update(status=StatusChoices.SUBMITTED)
        self.post.refresh_from_db()
        store_tts_json(self.post, {'ObjectStates': [{'Name': 'Card'}]}, source='generated')
        url = reverse('view-tts-json', kwargs={'slug': self.post.slug})
        self.assertEqual(self.client.get(url).status_code, 200)   # admin
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse('download-tts-json', kwargs={'slug': self.post.slug})).status_code, 404)

    def test_view_page_without_json_is_404(self):
        self.assertEqual(self.client.get(reverse('view-tts-json', kwargs={'slug': self.post.slug})).status_code, 404)

    def test_settings_hub_card_for_admins_only(self):
        hub = reverse('post-settings', kwargs={'slug': self.post.slug})
        self.assertIn('Manage TTS JSON', self.client.get(hub).content.decode())
        from django.contrib.auth.models import User
        designer = User.objects.create_user('designer2')
        self.post.designer = designer.profile
        self.post.save()
        self.client.force_login(designer)
        self.assertNotIn('Manage TTS JSON', self.client.get(hub).content.decode())


class BoardLayoutValidationTests(TestCase):
    def test_accepts_well_formed_layout(self):
        validate_tts_save({'ObjectStates': [{'Name': 'x'}],
                           'BoardLayout': {'clearings': [[1, 2.5]], 'forests': [], 'relics': [[-3, 0]]}})

    def test_rejects_malformed_layout(self):
        for bad in ([], {'clearings': [[1]]}, {'clearings': [[1, 'a']]}, {'clearings': 'x'},
                    {'rivers': [[0, 0]]}, {'clearings': [[True, 1]]}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_tts_save({'ObjectStates': [{'Name': 'x'}], 'BoardLayout': bad})


# ── Map builder (services/tts_map.py, tts_views.map_builder) ────────────────

_MAP_MEDIA = tempfile.mkdtemp(prefix='map_builder_media_')


def _detect_track(snaps):
    """A Python port of the RTT Box Score's detectTrackOn (axis x only): the
    rows of evenly spaced snaps it would read as the VP track."""
    if len(snaps) < 40:
        return None
    pts = sorted(((s['Position']['x'], s['Position']['z']) for s in snaps), key=lambda p: p[1])
    bands, cur = [], []
    for p in pts:
        if cur and p[1] - cur[-1][1] > 0.03:
            bands.append(cur)
            cur = []
        cur.append(p)
    bands.append(cur)
    found = []
    for band in bands:
        if len(band) < 25:
            continue
        xs = sorted(p[0] for p in band)
        diffs = sorted(xs[i] - xs[i - 1] for i in range(1, len(xs)))
        s = diffs[(len(diffs) + 1) // 2 - 1]
        even = s > 0.01 and all(1 <= round(d / s) <= 2 and abs(d - round(d / s) * s) <= 0.25 * s for d in diffs)
        n = round((xs[-1] - xs[0]) / s) + 1
        if even and 28 <= n <= 60 and len(xs) >= 0.85 * n:
            found.append((n, band[0][1]))
    return found or None


@override_settings(MEDIA_ROOT=_MAP_MEDIA, SITE_URL='https://www.example.test', CACHES=_LOCMEM)
class MapBuilderTests(TestCase):
    WIDTH, HEIGHT = 1090, 1000

    def setUp(self):
        cache.clear()
        from django.contrib.auth.models import User
        from django.contrib.auth.signals import user_logged_in
        from the_gatehouse.signals import user_logged_in_handler
        from the_keep.models import Map
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        self.designer_user = User.objects.create_user('mapdesigner')
        self.designer_user.profile.group = 'D'   # designers need editor rights (Profile.editor)
        self.designer_user.profile.save()
        self.map = Map.objects.create(title='Fall Map', designer=self.designer_user.profile,
                                      status=StatusChoices.STABLE, clearings=12, forests=None, ruins=4)
        os.makedirs(os.path.join(_MAP_MEDIA, 'maps'), exist_ok=True)
        Image.new('RGB', (self.WIDTH, self.HEIGHT), (90, 120, 60)).save(os.path.join(_MAP_MEDIA, 'maps', 'fall.png'))
        Map.objects.filter(pk=self.map.pk).update(board_image='maps/fall.png')
        self.map.refresh_from_db()

    def _state(self, clearings=3, fixed_suits=None, buildings=5, ruins=2, forests=2):
        from the_keep.services.tts_map import MapGeometry
        state = MapGeometry(self.WIDTH, self.HEIGHT).defaults()
        state['clearings'] = [{'centre': [0.2 + 0.2 * i, 0.4], 'marker': [0.25 + 0.2 * i, 0.4], 'number': i + 1,
                               'number_angle': 1.0, **({'suit': fixed_suits[i]} if fixed_suits else {})}
                              for i in range(clearings)]
        state['forests'] = [[0.3, 0.6 + 0.05 * i] for i in range(forests)]
        state['buildings'] = [{'at': [0.1 + 0.1 * i, 0.7], 'ruin': i < ruins} for i in range(buildings)]
        return state

    # ── Geometry ──
    def test_geometry_matches_the_official_summer_map(self):
        from the_keep.services.tts_map import MapGeometry
        g = MapGeometry(3479, 3189)
        # A Summer suit-marker snap (local) and its marker's table offset from the board.
        u, v = g.to_image(-1.798, 0.292)
        self.assertAlmostEqual(g.to_local(u, v)[0], -1.798, places=6)
        dx, dz = g.to_table(u, v)
        self.assertAlmostEqual(dx, 23.31 + 0.029, delta=0.05)
        self.assertAlmostEqual(dz, -3.84 + 0.041, delta=0.05)
        # Image left is local +x, image top is local -z.
        self.assertGreater(g.to_local(0, 0.5)[0], 0)
        self.assertLess(g.to_local(0.5, 0)[1], 0)

    # ── Build ──
    def test_built_map_matches_the_official_conventions(self):
        from the_keep.services.tts_map import build_map_tts_json, MapGeometry, NUMBER_TILE_RADIUS
        from the_keep.utils import validate_board_layout
        state = self._state()
        save = build_map_tts_json(self.map, state)
        board, *pieces = save['ObjectStates']
        self.assertEqual((board['Name'], board['Transform']['scaleX'], board['Transform']['rotY']),
                         ('Custom_Token', 12.9794, 180.0))
        self.assertEqual(board['Tags'], ['Map Object', 'RTT Map', 'RTT Map: Fall Map'])
        snaps = board['AttachedSnapPoints']
        track = [s for s in snaps if s['Position']['z'] > 1.3]
        self.assertEqual(len(track), 93)
        detected = _detect_track(snaps)
        self.assertEqual([n for n, _ in detected], [31, 31, 31])
        self.assertEqual(sum(1 for s in snaps if s.get('Tags') == ['Enclave']), 3)
        self.assertEqual(len(snaps), 93 + 12 + 3 + 5)

        by_tag = lambda tag: [p for p in pieces if tag in (p.get('Tags') or [])]
        markers, ruins, numbers = by_tag('Clearing Marker'), by_tag('Ruin'), by_tag('RTT Priority')
        self.assertEqual(len(markers), 3)
        self.assertEqual([m['Tags'][-1] for m in markers], ['Suit: fox', 'Suit: rabbit', 'Suit: mouse'])
        self.assertEqual(len(ruins), 2)
        self.assertEqual(len(numbers), 3)
        self.assertTrue(all(n['Locked'] and 'Map Object' in n['Tags'] for n in numbers))
        items = [p for p in pieces if p['Tags'] == ['Map Object'] and p['Name'] == 'Custom_Tile']
        self.assertEqual(len(items), 12)

        # Ruins sit on their buildings; number tiles touch their clearing circle.
        g = MapGeometry(self.WIDTH, self.HEIGHT)
        ruin_at = g.to_table(*state['buildings'][0]['at'])
        self.assertAlmostEqual(ruins[0]['Transform']['posX'], ruin_at[0], places=6)
        c = state['clearings'][0]
        cx, cz = g.to_table(*c['centre'])
        mx, mz = g.to_table(*c['marker'])
        tile = numbers[0]['Transform']
        import math
        self.assertAlmostEqual(math.hypot(tile['posX'] - cx, tile['posZ'] - cz),
                               math.hypot(mx - cx, mz - cz) + NUMBER_TILE_RADIUS, places=4)

        # Marker faces away from its clearing centre; its snap is that minus the board's 180.
        self.assertAlmostEqual(markers[0]['Transform']['rotY'] % 360,
                               math.degrees(math.atan2(mx - cx, mz - cz)) % 360, places=4)

        validate_board_layout(save['BoardLayout'])
        self.assertEqual(len(save['BoardLayout']['clearings']), 3)
        self.assertEqual(len(save['BoardLayout']['forests']), 2)

        # Every file comes from our own site.
        urls = re.findall(r'https?://[^"\']+', json.dumps(save))
        self.assertTrue(urls)
        self.assertEqual([u for u in urls if not u.startswith('https://www.example.test/')], [])

    def test_templates_point_at_files_that_exist(self):
        from django.contrib.staticfiles import finders
        from the_keep.services.tts_map import pieces
        paths = set(re.findall(r'static:([^"]+)', json.dumps(pieces())))
        self.assertGreater(len(paths), 30)
        missing = [p for p in paths if not finders.find(p)]
        self.assertEqual(missing, [])

    def test_fixed_maps_use_chosen_suits_and_are_not_shuffled(self):
        from the_keep.services.tts_map import build_map_tts_json
        self.map.fixed_clearings = True
        save = build_map_tts_json(self.map, self._state(fixed_suits=['bird', 'fox', 'fox']))
        markers = [p for p in save['ObjectStates'] if any(t.startswith('Suit: ') for t in p.get('Tags') or [])]
        self.assertEqual([m['Tags'] for m in markers][0], ['Map Object', 'Suit: bird'])
        self.assertTrue(all('Clearing Marker' not in m['Tags'] for m in markers))
        self.assertIn('bird_marker', markers[0]['CustomMesh']['DiffuseURL'])

    def test_extra_number_images_are_picked_up(self):
        from the_keep.services.tts_map import build_map_tts_json, highest_number, number_templates
        self.assertGreaterEqual(highest_number(), 24)
        self.assertIn('24_number.png', number_templates()[24]['CustomImage']['ImageURL'])
        save = build_map_tts_json(self.map, self._state(clearings=20))
        tiles = [p for p in save['ObjectStates'] if 'RTT Priority' in (p.get('Tags') or [])]
        self.assertEqual(len(tiles), 20)
        self.assertIn('20_number.png', tiles[-1]['CustomImage']['ImageURL'])

    def test_board_layout_is_in_number_order_and_clearings_past_the_last_number_get_none(self):
        from the_keep.services import tts_map
        from the_keep.services.tts_map import build_map_tts_json, check_builder, MapGeometry
        # Pretend only the mod's 1-12 exist, so clearing 13 has no tile.
        only_12 = {n: t for n, t in tts_map.number_templates().items() if n <= 12}
        patch = mock.patch.object(tts_map, 'number_templates', return_value=only_12)
        patch.start()
        self.addCleanup(patch.stop)
        state = self._state(clearings=13)
        state['clearings'][0]['number'], state['clearings'][1]['number'] = 2, 1
        save = build_map_tts_json(self.map, state)
        g = MapGeometry(self.WIDTH, self.HEIGHT)
        first = [round(v, 3) for v in g.to_table(*state['clearings'][1]['centre'])]
        self.assertEqual(save['BoardLayout']['clearings'][0], first)
        self.assertEqual(sum(1 for p in save['ObjectStates'] if 'RTT Priority' in (p.get('Tags') or [])), 12)
        errors, warnings = check_builder(self.map, state)
        self.assertEqual(errors, [])
        self.assertTrue(any('1-12' in w for w in warnings))

    def test_stored_map_info(self):
        from the_keep.services.tts_map import build_map_tts_json
        from the_keep.services.tts_store import store_tts_json
        info = store_tts_json(self.map, build_map_tts_json(self.map, self._state()), source='generated')
        self.assertEqual((info['map_board'], info['shuffle']), (1, True))

    # ── Checks and counts ──
    def test_checks(self):
        from the_keep.services.tts_map import check_builder
        state = self._state()
        state['clearings'][0]['marker'] = None
        state['clearings'][2]['number'] = 2
        errors, warnings = check_builder(self.map, state)
        self.assertEqual(len(errors), 2)
        # Counts that differ from the post are warnings only.
        self.assertTrue(any('3 clearings' in w and 'currently has 12' in w for w in warnings))
        self.map.fixed_clearings = True
        errors, _ = check_builder(self.map, self._state())
        self.assertIn('This Map has fixed clearings: choose a suit for each clearing before generating.', errors)

    def test_counts_update_the_post(self):
        from the_keep.models import Map
        from the_keep.services.tts_map import apply_builder_counts
        changes = apply_builder_counts(self.map, self._state(clearings=3, forests=2, buildings=5, ruins=4))
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual((stored.clearings, stored.forests, stored.building_slots, stored.ruins), (3, 2, 5, 4))
        self.assertEqual(len(changes), 3)   # ruins was already 4
        self.assertEqual(apply_builder_counts(stored, self._state(clearings=3, forests=2, buildings=5, ruins=4)), [])

    # ── Views ──
    def _builder_url(self):
        return reverse('map-builder', kwargs={'slug': self.map.slug})

    def test_designer_can_save_and_generate(self):
        from the_keep.models import Map
        self.client.force_login(self.designer_user)
        page = self.client.get(self._builder_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'map-builder-config')
        state = self._state()
        response = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                    content_type='application/json')
        result = response.json()
        self.assertTrue(result['generated'], result)
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual(len(stored.tts_builder['clearings']), 3)
        self.assertEqual(stored.tts_info['source'], 'generated')
        self.assertEqual(stored.clearings, 3)

    def test_designer_cannot_use_admin_actions(self):
        self.client.force_login(self.designer_user)
        manage = reverse('manage-tts-json', kwargs={'slug': self.map.slug})
        page = self.client.get(manage).content.decode()
        self.assertIn('Open Map Builder', page)
        self.assertNotIn('name="tts_file"', page)
        for action in ('upload', 'remove', 'recheck'):
            self.assertEqual(self.client.post(manage, {'action': action}).status_code, 403)

    def test_non_editor_gets_403(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('stranger'))
        self.assertEqual(self.client.get(self._builder_url()).status_code, 403)
        manage = reverse('manage-tts-json', kwargs={'slug': self.map.slug})
        self.assertEqual(self.client.get(manage).status_code, 403)

    def test_map_without_image(self):
        from the_keep.models import Map
        Map.objects.filter(pk=self.map.pk).update(board_image='')
        self.client.force_login(self.designer_user)
        self.assertContains(self.client.get(self._builder_url()), 'no board image')

    def test_builder_config_has_suit_icons_and_marker_outline(self):
        self.client.force_login(self.designer_user)
        config = self.client.get(self._builder_url()).context['config']
        self.assertEqual(set(config['suit_icons']), {'fox', 'rabbit', 'mouse', 'bird', 'city'})
        self.assertIn('images/tags/Bird_Tag.png', config['suit_icons']['bird'])
        self.assertIn('images/map_builder/clearing_marker.svg', config['marker_svg'])
        width, height = config['marker_px']
        self.assertAlmostEqual(width / height, 2.0, delta=0.05)
        with mock.patch('the_keep.tts_views.finders.find', return_value=None):
            self.assertIsNone(self.client.get(self._builder_url()).context['config']['marker_svg'])

    def test_marker_outline_svg(self):
        from django.contrib.staticfiles import finders
        path = finders.find('images/map_builder/clearing_marker.svg')
        self.assertTrue(path)
        svg = open(path).read()
        self.assertIn('fill="currentColor"', svg)
        x, y, w, h = map(float, re.search(r'viewBox="([^"]+)"', svg).group(1).split())
        # The marker's spot (0, 0) is inside, with the bump (more room) above it.
        self.assertTrue(x < 0 < x + w and y < 0 < y + h)
        self.assertGreater(-y, y + h)

    def test_saving_only_updates_counts_for_stages_reached(self):
        from the_keep.models import Map
        Map.objects.filter(pk=self.map.pk).update(forests=6, building_slots=22)
        self.client.force_login(self.designer_user)
        # Clearings reached; Buildings & Ruins and Forests not yet (nothing marked there).
        state = self._state(clearings=3, forests=0, buildings=0, ruins=0)
        state['visited'] = ['clearings']
        result = self.client.post(self._builder_url(), json.dumps({'state': state}),
                                  content_type='application/json').json()
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual((stored.clearings, stored.forests, stored.building_slots, stored.ruins), (3, 6, 22, 4))
        self.assertEqual(result['counted'], ['clearings'])
        self.assertEqual(stored.tts_builder['visited'], ['clearings'])
        # No "Saving updates the Map to 0" warning for a stage not reached.
        self.assertFalse(any('forests' in w for w in result['warnings']))

        # Reaching Forests (still none marked) does write its count.
        state['visited'] = ['clearings', 'forests', 'not-a-stage']
        self.client.post(self._builder_url(), json.dumps({'state': state}), content_type='application/json')
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual((stored.forests, stored.building_slots), (0, 22))
        self.assertEqual(stored.tts_builder['visited'], ['clearings', 'forests'])

    def test_switching_to_fixed_clearings_saves_to_the_map(self):
        from the_keep.models import Map
        from the_keep.tts_views import _clean_state
        self.client.force_login(self.designer_user)
        state = self._state()
        state['fixed'] = True
        # Not saved to the Map before the Clearings step is reached.
        self.client.post(self._builder_url(), json.dumps({'state': state}), content_type='application/json')
        self.assertFalse(Map.objects.get(pk=self.map.pk).fixed_clearings)
        # Saved once it is.
        state['visited'] = ['clearings']
        result = self.client.post(self._builder_url(), json.dumps({'state': state}),
                                  content_type='application/json').json()
        self.assertTrue(Map.objects.get(pk=self.map.pk).fixed_clearings)
        self.assertIn('fixed clearings No → Yes', result['changes'])

        # Generating is blocked until every clearing has a suit; then the markers are fixed.
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertFalse(result['ok'])
        state = self._state(fixed_suits=['fox', 'bird', 'mouse'])
        state['fixed'] = True
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertTrue(result['generated'], result)
        stored = Map.objects.get(pk=self.map.pk)
        markers = [o for o in stored.tts_json['ObjectStates'] if any(t.startswith('Suit: ') for t in o.get('Tags') or [])]
        self.assertTrue(all('Clearing Marker' not in m['Tags'] for m in markers))

        # The Map is the source of truth for the page, even over an older saved layout.
        Map.objects.filter(pk=self.map.pk).update(fixed_clearings=False)
        self.assertFalse(self.client.get(self._builder_url()).context['config']['fixed'])
        self.assertTrue(_clean_state({'fixed': True})['fixed'])
        self.assertNotIn('fixed', _clean_state({'fixed': 'yes'}))

    def test_ruin_item_sides_are_cleared_with_the_map_and_extra_ruins_are_marked(self):
        from the_keep.services.tts_map import build_map_tts_json, RUIN_EXTRA_NOTE
        save = build_map_tts_json(self.map, self._state(buildings=9, ruins=9))
        ruins = [o for o in save['ObjectStates'] if 'Ruin' in (o.get('Tags') or [])]
        self.assertEqual(len(ruins), 9)
        # Flipped to its item, a ruin is still a map piece (so a new map clears it).
        for ruin in ruins:
            self.assertTrue(ruin['States'])
            for side in ruin['States'].values():
                self.assertEqual(side['Tags'], ['Map Object'])
        # Ruins 1-4 keep their own items; later ones are dealt by the loader at spawn.
        firsts = [r['States']['2']['CustomImage']['ImageURL'] for r in ruins[:4]]
        self.assertEqual(len(set(firsts)), 4)
        self.assertTrue(all(r.get('GMNotes', '') != RUIN_EXTRA_NOTE for r in ruins[:4]))
        self.assertTrue(all(r['GMNotes'] == RUIN_EXTRA_NOTE for r in ruins[4:]))
        self.assertTrue(all(r['States']['2']['CustomImage']['ImageURL'] in firsts for r in ruins[4:]))

    def test_step_cards_open_by_themselves_only_until_the_first_finished_map(self):
        from the_gatehouse.models import Profile
        self.client.force_login(self.designer_user)
        self.assertTrue(self.client.get(self._builder_url()).context['config']['show_intros'])
        # A plain save doesn't count as finishing a map.
        state = self._state()
        self.client.post(self._builder_url(), json.dumps({'state': state}), content_type='application/json')
        self.assertFalse(Profile.objects.get(pk=self.designer_user.profile.pk).map_builder_onboard)
        # Completing one does.
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertTrue(result['generated'], result)
        self.assertTrue(Profile.objects.get(pk=self.designer_user.profile.pk).map_builder_onboard)
        config = self.client.get(self._builder_url()).context['config']
        self.assertFalse(config['show_intros'])
        self.assertIn('ClearingsRandom.png', config['random_icon'])

    def test_lost_city_suit(self):
        from the_keep.services.tts_map import build_map_tts_json, check_builder, marker_templates
        from the_keep.tts_views import _clean_state
        self.map.fixed_clearings = True
        state = self._state(fixed_suits=['city', 'fox', 'bird'])
        errors, _ = check_builder(self.map, state)
        self.assertEqual(errors, [])
        save = build_map_tts_json(self.map, state)
        markers = [o for o in save['ObjectStates'] if any(t.startswith('Suit: ') for t in o.get('Tags') or [])]
        city = next(m for m in markers if 'Suit: city' in m['Tags'])
        self.assertEqual(city['Tags'], ['Map Object', 'Suit: city'])
        self.assertIn('city_marker.png', city['CustomMesh']['DiffuseURL'])
        # The mod's shuffleMaps acts on these tags (expecting its own pieces): never used.
        for o in save['ObjectStates']:
            self.assertFalse({'City Marker', 'Lost City Marker'} & set(o.get('Tags') or []))
        # Built from the fox marker: same mesh, its own texture; bird kept.
        templates = marker_templates()
        self.assertEqual(templates['city']['CustomMesh']['MeshURL'], templates['fox']['CustomMesh']['MeshURL'])
        self.assertIn('bird_marker.png', templates['bird']['CustomMesh']['DiffuseURL'])
        self.assertEqual(_clean_state({'clearings': [{'centre': [0.5, 0.5], 'suit': 'city'}]})['clearings'][0]['suit'], 'city')
        self.client.force_login(self.designer_user)
        self.assertIn('City_Tag.png', self.client.get(self._builder_url()).context['config']['suit_icons']['city'])
