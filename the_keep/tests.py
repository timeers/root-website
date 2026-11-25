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
        self.assertIn('Sync in the Forge', message)
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
        self.assertIn('Sync in the Forge', page)

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

    def test_paths_step_on_the_keep(self):
        from the_keep.models import Map
        from the_keep.services.tts_map import suit_icons
        Map.objects.filter(pk=self.map.pk).update(river_clearings=5)
        self.client.force_login(self.designer_user)
        config = self.client.get(self._builder_url()).context['config']
        self.assertTrue(config['paths_step'])
        self.assertEqual(config['expected']['river_clearings'], 5)
        self.assertEqual(config['suit_icons'], suit_icons())     # one place builds them
        state = self._state()
        state['paths'] = [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}]
        state['visited'] = ['clearings', 'buildings', 'paths', 'forests']
        # Save Progress keeps the paths but leaves the Map's figure alone...
        self.client.post(self._builder_url(), json.dumps({'state': state}), content_type='application/json')
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual(stored.tts_builder['paths'], state['paths'])
        self.assertEqual(stored.river_clearings, 5)
        # ...and Complete writes it.
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertTrue(result['generated'], result)
        self.assertEqual(Map.objects.get(pk=self.map.pk).river_clearings, 2)

    def test_the_keep_builder_saves_the_setup_card_rules(self):
        # The Rules step: its form's fields saved with the layout, as the Map's
        # map_card (the same JSON as a Forge map's), named special paths and all.
        from the_keep.models import Map
        self.client.force_login(self.designer_user)
        page = self.client.get(self._builder_url())
        self.assertTrue(page.context['config']['rules_step'])
        self.assertIn('id="mb-rules"', page.content.decode())
        state = self._state()
        state['paths'] = [{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}},
                          {'kind': 'special', 'special': 'green', 'a': {'c': 1}, 'b': {'c': 2}}]
        state['visited'] = ['paths']
        card = {'special_name_red': ['Bridges'], 'special_rule_red': ['Cross by day.'], 'special_divides_red': ['no'],
                'special_name_green': [''], 'modifications': ['Set up the ferry.'], 'raging_river': ['no']}
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'card': card}),
                                  content_type='application/json').json()
        self.assertEqual(result['card_errors'], [])
        saved = Map.objects.get(pk=self.map.pk).map_card
        self.assertEqual(saved['special_paths']['red'], {'name': 'Bridges', 'rule': 'Cross by day.', 'divides_forests': False})
        self.assertEqual(saved['modifications'], 'Set up the ferry.')
        # The Map's page shows them on its summary card: named, or by their colour.
        html = self.client.get(reverse('map-detail', kwargs={'slug': self.map.slug})).content.decode()
        self.assertIn('"label": "Bridges"', html)
        self.assertIn('"label": "Green Paths"', html)
        # A card that won't fit isn't saved (the layout is), and says so.
        too_long = {**card, 'modifications': ['x' * 300], 'rule_name': [f'R{n}' for n in range(6)],
                    'rule_text': ['y' * 300] * 6}
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'card': too_long, 'generate': True}),
                                  content_type='application/json').json()
        self.assertTrue(result['card_errors'])
        self.assertFalse(result['ok'])
        self.assertEqual(Map.objects.get(pk=self.map.pk).map_card['modifications'], 'Set up the ferry.')

    def test_map_page_has_the_layout_summary(self):
        from the_keep.models import Map
        url = reverse('map-detail', kwargs={'slug': self.map.slug})
        html = self.client.get(url).content.decode()       # no layout yet: the Map's figures
        self.assertIn('id="map-layout-summary"', html)
        self.assertIn('"count": 12', html)
        Map.objects.filter(pk=self.map.pk).update(tts_builder=self._state(clearings=3))
        html = self.client.get(url).content.decode()
        self.assertIn('"count": 3', html)
        self.assertIn('map_summary.js', html)
        # Other components have no layout card.
        from the_keep.models import Faction
        fox = Faction.objects.create(title='Summary Fox', animal='Fox', designer=self.map.designer, component='Faction',
                                     status=StatusChoices.STABLE)
        html = self.client.get(reverse('faction-detail', kwargs={'slug': fox.slug})).content.decode()
        self.assertNotIn('map-layout-summary', html)

    def test_builder_config_has_suit_icons_and_marker_outline(self):
        self.client.force_login(self.designer_user)
        config = self.client.get(self._builder_url()).context['config']
        self.assertEqual(set(config['suit_icons']), {'fox', 'rabbit', 'mouse', 'bird', 'city'})
        self.assertIn('images/tags/Bird_Tag.png', config['suit_icons']['bird'])
        self.assertIn('images/map_builder/clearing_marker.svg', config['marker_svg'])
        # The summary card's forest icon: in the builder's config and the detail pages' data.
        from the_keep.services.tts_map import layout_summary
        self.assertIn('pdf/maps/root_map_green_forest.svg', config['forest_icon'])
        self.assertEqual(layout_summary({})['forest_icon'], config['forest_icon'])
        self.assertIn('images/tts/assets/ruin.png', config['ruin_icon'])
        self.assertEqual(layout_summary({})['ruin_icon'], config['ruin_icon'])
        width, height = config['marker_px']
        self.assertAlmostEqual(width / height, 2.0, delta=0.05)
        from the_keep.services.tts_map import DEFAULT_CLEARING_RADIUS
        self.assertAlmostEqual(config['default_radius_px'], DEFAULT_CLEARING_RADIUS * config['px_per_local'])
        with mock.patch('django.contrib.staticfiles.finders.find', return_value=None):
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

    def test_save_progress_only_stores_the_layout(self):
        from the_keep.models import Map
        Map.objects.filter(pk=self.map.pk).update(forests=6, building_slots=22)
        self.client.force_login(self.designer_user)
        state = self._state(clearings=3, forests=0, buildings=0, ruins=0)
        state['visited'] = ['clearings', 'forests', 'not-a-stage']
        state['fixed'] = True
        result = self.client.post(self._builder_url(), json.dumps({'state': state}),
                                  content_type='application/json').json()
        stored = Map.objects.get(pk=self.map.pk)
        # The layout is saved; the Map's own figures aren't touched.
        self.assertEqual(stored.tts_builder['visited'], ['clearings', 'forests'])
        self.assertEqual((stored.clearings, stored.forests, stored.building_slots, stored.ruins), (12, 6, 22, 4))
        self.assertFalse(stored.fixed_clearings)
        self.assertEqual((result['changes'], result['counted']), ([], []))
        # Count differences are only warnings, for the stages reached, about Complete.
        self.assertTrue(any('Completing the map updates it to 3' in w for w in result['warnings']), result)
        self.assertFalse(any('building slots' in w for w in result['warnings']))
        # Coming back later: the page reopens with the saved (not yet completed) choice.
        self.assertTrue(self.client.get(self._builder_url()).context['config']['fixed'])

    def test_complete_writes_the_maps_figures(self):
        from the_keep.models import Map
        from the_keep.tts_views import _clean_state
        self.client.force_login(self.designer_user)
        state = self._state(clearings=3, forests=2, buildings=5, ruins=2)
        state['fixed'] = True
        # A Complete that fails its checks (fixed suits, but none chosen) writes nothing.
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertFalse(result['ok'])
        self.assertIn('This Map has fixed clearings: choose a suit for each clearing before generating.',
                      result['errors'])
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual((stored.clearings, stored.fixed_clearings), (12, False))
        # Once it passes, Complete writes every figure and the fixed suits.
        state = self._state(clearings=3, forests=2, buildings=5, ruins=2, fixed_suits=['fox', 'bird', 'mouse'])
        state['fixed'] = True
        result = self.client.post(self._builder_url(), json.dumps({'state': state, 'generate': True}),
                                  content_type='application/json').json()
        self.assertTrue(result['generated'], result)
        stored = Map.objects.get(pk=self.map.pk)
        self.assertEqual((stored.clearings, stored.forests, stored.building_slots, stored.ruins, stored.fixed_clearings),
                         (3, 2, 5, 2, True))
        self.assertIn('fixed clearings No → Yes', result['changes'])
        markers = [o for o in stored.tts_json['ObjectStates'] if any(t.startswith('Suit: ') for t in o.get('Tags') or [])]
        self.assertTrue(all('Clearing Marker' not in m['Tags'] for m in markers))
        # A layout without a saved choice falls back to the Map's setting.
        Map.objects.filter(pk=self.map.pk).update(tts_builder={'clearings': []}, fixed_clearings=True)
        self.assertTrue(self.client.get(self._builder_url()).context['config']['fixed'])
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

    def test_keep_builder_has_none_of_the_forge_extras(self):
        from the_keep.services.tts_map import clean_builder_state
        from the_keep.tts_views import _clean_state
        self.client.force_login(self.designer_user)
        response = self.client.get(self._builder_url())
        config = response.context['config']
        for key in ('upload_step', 'print', 'readonly', 'step_text', 'default_logo'):
            self.assertNotIn(key, config)
        self.assertEqual(config['building_size'], 0.1)
        self.assertNotIn('id="mb-upload"', response.content.decode())
        # The Root logo is only printed on Forge maps: a Keep layout drops it.
        self.assertNotIn('logo', _clean_state({'logo': [0.5, 0.5]}))
        self.assertEqual(clean_builder_state({'logo': [0.5, 0.5]}, logo=True)['logo'], [0.5, 0.5])

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


# ── TTS warrior figures and supplies (tts_figures.py, tts_supply.py) ────────

import io as _io
import math

from PIL import ImageDraw

from the_keep.services import tts_figures, tts_supply
from the_keep.services.tts import LOCK_ON_REST_LUA


def _meeple(size=(200, 260), colour=(200, 90, 30, 255)):
    """A meeple-ish RGBA image: a body with two ears, transparent around it."""
    w, h = size
    im = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([w * 0.15, h * 0.35, w * 0.85, h - 1], fill=colour)
    d.polygon([(w * 0.15, h * 0.35), (w * 0.3, 0), (w * 0.45, h * 0.35)], fill=colour)
    d.polygon([(w * 0.55, h * 0.35), (w * 0.7, 0), (w * 0.85, h * 0.35)], fill=colour)
    return im


def _meeple_bytes(**kwargs):
    out = _io.BytesIO()
    _meeple(**kwargs).save(out, format='PNG')
    return out.getvalue()


def _poly_area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2


def _parse_obj(text, normals=False):
    """(v, vt, faces), plus vn when `normals`. Face corners are index tuples:
    (v, vt) or (v, vt, vn)."""
    v = [tuple(map(float, l.split()[1:])) for l in text.splitlines() if l.startswith('v ')]
    vt = [tuple(map(float, l.split()[1:])) for l in text.splitlines() if l.startswith('vt ')]
    vn = [tuple(map(float, l.split()[1:])) for l in text.splitlines() if l.startswith('vn ')]
    faces = [[tuple(int(x) for x in p.split('/')) for p in l.split()[1:]]
             for l in text.splitlines() if l.startswith('f ')]
    return (v, vt, vn, faces) if normals else (v, vt, faces)


def _normal(v, face):
    a, b, c = (v[corner[0] - 1] for corner in face[:3])
    u = [b[i] - a[i] for i in range(3)]
    w = [c[i] - a[i] for i in range(3)]
    return (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])


def _figure_template(size=(1.15, 0.56, 1.55)):
    obj = tts_figures.figure_object('mesh.obj', 'tex.png', nickname='Cat Warrior')
    return obj, size


class TTSFigureTests(TestCase):
    def test_shape_size_and_triangles(self):
        im = _meeple()
        shape = tts_figures.build_shape(im)
        self.assertGreater(len(shape.outline), 3)
        area = _poly_area(shape.outline)
        tri_area = sum(_poly_area([shape.outline[i] for i in t]) for t in shape.triangles)
        self.assertAlmostEqual(tri_area, area, delta=area * 0.001)
        self.assertEqual(shape.height, tts_figures.FIGURE_HEIGHT)
        self.assertEqual(shape.depth, tts_figures.FIGURE_DEPTH)
        # The opaque shape is 0.7 x 1.0 of the image: width follows its aspect.
        self.assertAlmostEqual(shape.width, 1.55 * (0.7 * 200) / 259, delta=0.03)

    def test_obj_indexes_winding_and_side_uv(self):
        shape = tts_figures.build_shape(_meeple())
        v, vt, vn, faces = _parse_obj(tts_figures.figure_obj(shape), normals=True)
        for face in faces:
            for vi, ti, ni in face:
                self.assertTrue(1 <= vi <= len(v) and 1 <= ti <= len(vt) and 1 <= ni <= len(vn))
        n = len(shape.outline)
        caps = [f for f in faces if len(f) == 3]
        front = [_normal(v, f)[2] for f in caps if all(c[0] <= n for c in f)]
        back = [_normal(v, f)[2] for f in caps if all(n < c[0] <= 2 * n for c in f)]
        self.assertTrue(front and all(z > 0 for z in front))
        self.assertTrue(back and all(z < 0 for z in back))
        self.assertEqual(len(front) + len(back), len(caps))
        side_uv = len(vt)
        self.assertEqual(vt[side_uv - 1], tts_figures.SIDE_UV)
        for face in faces:
            if len(face) == 4:
                self.assertTrue(all(c[1] == side_uv and c[0] > 2 * n for c in face))
        # Base on the ground, centred across.
        self.assertAlmostEqual(min(p[1] for p in v), 0.0, places=4)
        xs = [p[0] for p in v]
        self.assertAlmostEqual(max(xs) + min(xs), 0.0, places=3)

    def test_texture_colour_area(self):
        texture = tts_figures.art_texture(_meeple(), '#3366cc')
        self.assertEqual(texture.size, (1024, 1024))
        self.assertEqual(texture.getpixel((1023, 1023)), (0x33, 0x66, 0xcc))
        self.assertEqual(texture.getpixel((int(0.75 * 1024), int(0.75 * 1024))), (0x33, 0x66, 0xcc))

    def test_bad_images_raise(self):
        with self.assertRaises(tts_figures.FigureError):
            tts_figures.build_shape(Image.new('RGBA', (50, 50), (0, 0, 0, 0)))
        with self.assertRaises(tts_figures.FigureError):
            tts_figures.build_shape(Image.new('RGB', (50, 50), (255, 0, 0)))

    def test_nicknames_end_in_warrior(self):
        self.assertEqual(tts_figures.warrior_nickname('Cat', 'Marquise'), 'Cat Warrior')
        self.assertEqual(tts_figures.warrior_nickname('Cat Warrior', 'Marquise'), 'Cat Warrior')
        self.assertEqual(tts_figures.warrior_nickname('Cat Warriors', 'Marquise'), 'Cat Warrior')
        self.assertEqual(tts_figures.warrior_nickname('Warriors', 'Marquise'), 'Marquise Warrior')
        self.assertEqual(tts_figures.warrior_nickname('', 'Marquise'), 'Marquise Warrior')


def _curved_meeple(size=(240, 300), aa=False, patch=False):
    """A meeple with curves (a round body) and a sharp feature (a pointed ear,
    its tip the topmost point). `aa`: drawn at 4x and downsampled, so its edge
    is anti-aliased. `patch`: a translucent patch inside the art."""
    scale = 4 if aa else 1
    w, h = size[0] * scale, size[1] * scale
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    colour = (40, 120, 200, 255)
    d.ellipse([w * 0.1, h * 0.25, w * 0.9, h * 0.95], fill=colour)
    d.rectangle([w * 0.2, h * 0.6, w * 0.8, h - 1], fill=colour)
    d.polygon([(w * 0.55, h * 0.35), (w * 0.7, 0), (w * 0.8, h * 0.4)], fill=colour)
    if patch:
        d.ellipse([w * 0.35, h * 0.55, w * 0.6, h * 0.75], fill=(255, 255, 255, 120))
    return im.resize(size, Image.LANCZOS) if aa else im


def _turns(outline):
    """The turn at each vertex, in degrees."""
    n = len(outline)
    out = []
    for i in range(n):
        a, b, c = outline[i - 1], outline[i], outline[(i + 1) % n]
        turn = math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(b[1] - a[1], b[0] - a[0])
        out.append(abs(math.degrees((turn + math.pi) % (2 * math.pi) - math.pi)))
    return out


def _segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1e-9)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _max_deviation(points, outline):
    edges = list(zip(outline, outline[1:] + outline[:1]))
    return max(min(_segment_distance(p, a, b) for a, b in edges) for p in points)


def _bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _inside(point, polygon):
    """Ray casting: is `point` inside `polygon`?"""
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _points_outward(v, ring, face, normal):
    """Whether `normal` (x, y) points out of the shape at side `face`: a point
    a little way along it from the face's middle is outside the outline."""
    a, b = v[face[0][0] - 1], v[face[1][0] - 1]
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    length = math.hypot(normal[0], normal[1]) or 1e-9
    step = 1e-3
    return not _inside((mid[0] + normal[0] / length * step, mid[1] + normal[1] / length * step), ring)


def _shape_from(outline):
    outline = list(outline)
    return tts_figures.FigureShape(outline=outline, triangles=tts_figures.triangulate(outline),
                                   image_size=(100, 100), width=1.0, height=1.55, depth=0.56,
                                   art_box=tts_figures.art_box((100, 100)))


class TTSFigureSmoothingTests(TestCase):
    """The outline is traced between pixels and, for hard-edged art, smoothed;
    the mesh carries its own normals."""

    def test_both_kinds_of_edge_give_a_smooth_detailed_outline(self):
        for aa in (False, True):
            with self.subTest(antialiased=aa):
                outline = tts_figures.build_shape(_curved_meeple(aa=aa)).outline
                turns = sorted(_turns(outline))
                self.assertLess(turns[len(turns) // 2], 12)
                self.assertGreaterEqual(len(outline), 60)     # the old tracer gave 36 on Meeple1
                # No pixel stairs: 3+ consecutive axis-aligned ~1 px edges.
                run = longest = 0
                for a, b in zip(outline, outline[1:] + outline[:1]):
                    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
                    stair = (dx < 1e-6 or dy < 1e-6) and 0.9 <= dx + dy <= 1.1
                    run = run + 1 if stair else 0
                    longest = max(longest, run)
                self.assertLess(longest, 3)

    def test_hard_edges_are_read_from_the_outline(self):
        hard = tts_figures.trace_outline(_curved_meeple())
        patched_image = _curved_meeple(patch=True)
        patched = tts_figures.trace_outline(patched_image)
        soft = tts_figures.trace_outline(_curved_meeple(aa=True))
        self.assertLess(hard.soft_share, tts_figures.HARD_EDGE_SHARE)
        self.assertGreater(soft.soft_share, tts_figures.HARD_EDGE_SHARE)
        # Translucent detail inside the art doesn't make the outline soft,
        # though the image is full of partly transparent pixels.
        partial = sum(patched_image.getchannel('A').histogram()[1:255])
        self.assertGreater(partial / len(patched.points), 1)
        self.assertLess(patched.soft_share, tts_figures.HARD_EDGE_SHARE)

    def test_antialiased_art_is_not_smoothed(self):
        image = _curved_meeple(aa=True)
        raw = tts_figures.trace_outline(image).points
        outline = tts_figures.build_shape(image).outline
        self.assertLessEqual(_max_deviation(raw[::3], outline), 0.35)
        # The ear's tip (the topmost point) is still a sharp vertex.
        tip = min(range(len(outline)), key=lambda i: outline[i][1])
        self.assertGreater(_turns(outline)[tip], 30)

    def test_smoothing_keeps_the_shape(self):
        image = _curved_meeple()
        raw = tts_figures.trace_outline(image).points
        outline = tts_figures.build_shape(image).outline
        self.assertAlmostEqual(_poly_area(outline), _poly_area(raw), delta=_poly_area(raw) * 0.01)
        for got, want in zip(_bbox(outline), _bbox(raw)):
            self.assertAlmostEqual(got, want, delta=1.5)      # the pointed ear loses ~1 px

    def test_detail_doesnt_depend_on_resolution(self):
        small = tts_figures.build_shape(_curved_meeple(size=(160, 200)))
        large = tts_figures.build_shape(_curved_meeple(size=(320, 400)))
        self.assertAlmostEqual(len(large.outline) / len(small.outline), 1.0, delta=0.3)
        self.assertAlmostEqual(large.width, small.width, delta=small.width * 0.01)

    def test_intricate_shapes_are_capped(self):
        import random
        rng = random.Random(7)
        im = Image.new('RGBA', (400, 400), (0, 0, 0, 0))
        points = []
        for i in range(720):
            angle = 2 * math.pi * i / 720
            radius = 150 + rng.uniform(-25, 25)
            points.append((200 + radius * math.cos(angle), 200 + radius * math.sin(angle)))
        ImageDraw.Draw(im).polygon(points, fill=(0, 0, 0, 255))
        shape = tts_figures.build_shape(im)
        self.assertLessEqual(len(shape.outline), tts_figures.MAX_OUTLINE_POINTS)
        area = _poly_area(shape.outline)
        tri_area = sum(_poly_area([shape.outline[i] for i in t]) for t in shape.triangles)
        self.assertAlmostEqual(tri_area, area, delta=area * 0.01)

    def test_sides_face_outward_whatever_order_the_trace_gives(self):
        image = _curved_meeple(aa=True)
        trace = tts_figures.trace_outline(image)
        reversed_trace = tts_figures.Trace(list(reversed(trace.points)), trace.soft_share)
        for traced in (trace, reversed_trace):
            with self.subTest(reversed=traced is reversed_trace), \
                    mock.patch.object(tts_figures, 'trace_outline', return_value=traced):
                shape = tts_figures.build_shape(image)
                v, _vt, faces = _parse_obj(tts_figures.figure_obj(shape))
                ring = [p[:2] for p in v[:len(shape.outline)]]
                for face in faces:
                    if len(face) == 4:
                        self.assertTrue(_points_outward(v, ring, face, _normal(v, face)))

    def test_normals(self):
        shape = tts_figures.build_shape(_curved_meeple(aa=True))
        v, vt, vn, faces = _parse_obj(tts_figures.figure_obj(shape), normals=True)
        for normal in vn:
            self.assertAlmostEqual(math.hypot(*normal), 1.0, places=3)
        # Like the official warriors: one texture coordinate and one normal per vertex.
        used = {}
        for face in faces:
            for vi, ti, ni in face:
                used.setdefault(vi, set()).add((ti, ni))
        self.assertTrue(all(len(combos) == 1 for combos in used.values()))
        n = len(shape.outline)
        ring = [p[:2] for p in v[:n]]
        for face in faces:
            geometric = _normal(v, face)
            for vi, _ti, ni in face:
                normal = vn[ni - 1]
                # Each corner's normal agrees with its face's winding.
                self.assertGreater(sum(g * c for g, c in zip(geometric, normal)), 0)
                if len(face) == 3:
                    self.assertEqual(normal, (0.0, 0.0, 1.0) if vi <= n else (0.0, 0.0, -1.0))
                else:
                    self.assertEqual(normal[2], 0.0)
                    self.assertTrue(_points_outward(v, ring, face, normal))

    def test_sharp_corners_split_and_curves_share(self):
        square = _shape_from([(0, 0), (10, 0), (10, 10), (0, 10)])
        v, _vt, vn, _faces = _parse_obj(tts_figures.figure_obj(square), normals=True)
        # 4 corners at 90 degrees: two side vertex pairs each, four side normals.
        self.assertEqual(len(v), 2 * 4 + 4 * 2 * 2)
        self.assertEqual(len(vn), 2 + 4 * 2)
        circle = _shape_from([(50 + 40 * math.cos(2 * math.pi * i / 32), 50 + 40 * math.sin(2 * math.pi * i / 32))
                              for i in range(32)])
        v, _vt, vn, _faces = _parse_obj(tts_figures.figure_obj(circle), normals=True)
        # Gentle 11-degree turns: one shared side vertex pair and normal per point.
        self.assertEqual(len(v), 2 * 32 + 2 * 32)
        self.assertEqual(len(vn), 2 + 32)


@override_settings(SITE_URL='https://example.test')
class TTSSupplyTests(TestCase):
    def test_model_supply(self):
        template, _size = _figure_template()
        bag = tts_supply.make_supply(template, 7, nickname='Marquise Supply')
        self.assertEqual(bag['Name'], 'Custom_Model_Bag')
        self.assertEqual(bag['CustomMesh']['MeshURL'], template['CustomMesh']['MeshURL'])
        self.assertEqual(bag['CustomMesh']['TypeIndex'], 6)
        self.assertEqual(bag['Bag'], {'Order': 0})
        self.assertEqual(bag['LuaScript'], LOCK_ON_REST_LUA)
        self.assertFalse(bag['Locked'])
        t = bag['Transform']
        self.assertEqual((t['rotX'], t['rotY'], t['rotZ']), (270.0, 180.0, 0.0))
        self.assertAlmostEqual(t['scaleX'], 2.34 * 1.73, places=4)
        self.assertAlmostEqual(t['scaleZ'], 2.93 * 1.73, places=4)
        self.assertEqual(len(bag['ContainedObjects']), 7)
        guids = {o['GUID'] for o in bag['ContainedObjects']} | {bag['GUID']}
        self.assertEqual(len(guids), 8)
        for o in bag['ContainedObjects']:
            self.assertEqual(o['Nickname'], 'Cat Warrior')
            self.assertEqual((o['Transform']['scaleX'], o['Transform']['scaleZ']), (2.34, 2.93))

    def test_round_and_square_supplies(self):
        tile = {'Name': 'Custom_Tile', 'Nickname': 'Wood', 'Transform': {'scaleX': 0.7, 'scaleY': 1.0, 'scaleZ': 0.7},
                'CustomImage': {'CustomTile': {'Type': 2}}}
        self.assertEqual(tts_supply.supply_shape_for(tile), 'round')
        self.assertEqual(tts_supply.supply_shape_for({'Name': 'Custom_Tile', 'CustomImage': {'CustomTile': {'Type': 3}}}),
                         'square')
        for shape in ('round', 'square'):
            bag = tts_supply.make_supply(tile, 8, nickname='Wood Supply', shape=shape, texture_url='tex.png')
            self.assertEqual(bag['Name'], 'Custom_Model_Bag')
            self.assertIn(f'/images/tts/assets/supply_{shape}.obj', bag['CustomMesh']['MeshURL'])
            self.assertTrue(bag['CustomMesh']['MeshURL'].startswith('{verifycache}https://example.test/'))
            self.assertEqual(bag['CustomMesh']['DiffuseURL'], 'tex.png')
            self.assertEqual(bag['LuaScript'], LOCK_ON_REST_LUA)
            self.assertEqual(len(bag['ContainedObjects']), 8)
            self.assertEqual(bag['ContainedObjects'][0]['Transform']['scaleX'], 0.7)
        with self.assertRaises(ValueError):
            tts_supply.make_supply(tile, 8, nickname='Wood Supply', shape='round')

    def test_static_meshes(self):
        from django.contrib.staticfiles import finders
        for shape in ('round', 'square'):
            path = finders.find(tts_supply.ASSETS_STATIC + tts_supply.STATIC_SUPPLY_MESHES[shape])
            self.assertIsNotNone(path)
            with open(path) as handle:
                v, vt, faces = _parse_obj(handle.read())
            xs = [p[0] * tts_supply.STATIC_SUPPLY_SCALE for p in v]
            ys = [p[1] * tts_supply.STATIC_SUPPLY_SCALE for p in v]
            self.assertAlmostEqual(max(xs) - min(xs), tts_supply.STATIC_SUPPLY_SIZE, places=2)
            self.assertAlmostEqual(max(ys) - min(ys), tts_supply.STATIC_SUPPLY_HEIGHT, places=2)
            for face in faces:
                for _v, t in face:
                    u, w = vt[t - 1]
                    if len(face) == 4:
                        self.assertEqual((u, w), tts_figures.SIDE_UV)
                    else:   # caps: the art quarter (top-left of the image)
                        self.assertTrue(0 <= u <= 0.5 and 0.5 <= w <= 1)

    def test_row_layout(self):
        row = tts_supply.RowLayout(start_x=0.0, z=0.0, gap=0.5)
        self.assertEqual(row.end, 0.0)
        self.assertEqual(row.place(2.0, 1.0), (0.0, 0.0))
        self.assertEqual(row.end, 1.5)
        self.assertEqual(row.place(1.0, 1.0), (2.0, 0.0))
        self.assertEqual(row.place(1.0, 1.0, min_x=10.0), (10.0, 0.0))
        blocked = tts_supply.RowLayout(start_x=0.0, z=0.0, gap=0.5, obstacles=[(-5, 3, -2, 2)])
        x, _z = blocked.place(2.0, 1.0)
        self.assertEqual(x, 3.5 + 1.0)
        # Only clipping an obstacle's north edge: moved north, not right.
        clipped = tts_supply.RowLayout(start_x=0.0, z=0.0, gap=0.5, obstacles=[(-5, 5, -9, -0.3)])
        x, z = clipped.place(2.0, 1.0)
        self.assertEqual(x, 0.0)
        self.assertAlmostEqual(z - 0.5, -0.3 + 0.05)

    def test_few_warriors_stand_in_a_row(self):
        template, size = _figure_template()
        row = tts_supply.RowLayout()
        objs = tts_supply.layout_groups([tts_supply.SupplyGroup(template, 3, 'Marquise Supply', size=size)],
                                        row, loose_spacing=tts_supply.LOOSE_SPACING)
        self.assertEqual(len(objs), 3)
        self.assertTrue(all(o['Name'] == 'Custom_Model' for o in objs))
        xs = [o['Transform']['posX'] for o in objs]
        self.assertAlmostEqual(sum(xs) / 3, tts_supply.ROW_START_X, places=4)
        self.assertAlmostEqual(xs[1] - xs[0], tts_supply.LOOSE_SPACING, places=4)
        self.assertTrue(all(o['Transform']['posZ'] == tts_supply.ROW_Z for o in objs))

    def test_supply_is_centred_on_its_slot(self):
        template, size = _figure_template()
        row = tts_supply.RowLayout()
        groups = [tts_supply.SupplyGroup(template, 7, 'A Supply', size=size),
                  tts_supply.SupplyGroup(template, 6, 'B Supply', size=size)]
        bag, second = tts_supply.layout_groups(groups, row)
        self.assertEqual(bag['Name'], 'Custom_Model_Bag')
        self.assertEqual(len(bag['ContainedObjects']), 7)
        self.assertEqual(bag['ColorDiffuse'], {'r': 1.0, 'g': 1.0, 'b': 1.0})
        t = bag['Transform']
        self.assertAlmostEqual(t['posX'], tts_supply.ROW_START_X)
        # Lying down its base (the mesh origin) is the south end: the footprint
        # runs posZ .. posZ + height x 1.73, centred on the row.
        length = size[2] * tts_supply.SUPPLY_SCALE
        self.assertAlmostEqual(t['posZ'] + length / 2, tts_supply.ROW_Z)
        # The second slot is to the right, without overlap.
        (a0, a1, *_), (b0, b1, *_) = row.placed
        self.assertGreater(second['Transform']['posX'], t['posX'])
        self.assertGreaterEqual(b0, a1)


_FIGURE_MEDIA = tempfile.mkdtemp(prefix='keep-figure-test-')


@override_settings(MEDIA_ROOT=_FIGURE_MEDIA, SITE_URL='https://example.test')
class KeepWarriorExportTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_FIGURE_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from the_keep.models import Faction as KeepFaction, Language, Piece as KeepPiece
        Language.objects.get_or_create(code='en', defaults={'name': 'English'})
        designer = Profile.objects.create(discord='keepwarriors')
        self.post = KeepFaction.objects.create(title='Cats', animal='Cat', designer=designer,
                                               component='Faction', color='#d2691e')
        self.piece = KeepPiece.objects.create(
            parent=self.post, name='Warriors', type='W', quantity=25,
            small_icon=SimpleUploadedFile('w.png', _meeple_bytes(size=(600, 780)), content_type='image/png'))

    def test_warrior_icon_kept_at_400_with_alpha(self):
        self.piece.refresh_from_db()
        with Image.open(self.piece.small_icon.path) as im:
            self.assertEqual(max(im.size), 400)
            self.assertIn('A', im.getbands())
            self.assertEqual(im.getpixel((0, 0))[3], 0)

    def test_figures_from_an_older_version_regenerate(self):
        self.assertEqual(tts_figures.FIGURE_VERSION, 2)
        from the_keep.models import Piece as KeepPiece
        with mock.patch.object(tts_figures, 'FIGURE_VERSION', 1):
            tts_figures.ensure_figure(self.piece, '#d2691e')
        old = KeepPiece.objects.get(pk=self.piece.pk)
        old_fingerprint = old.figure_fingerprint      # ensure_figure updates the piece it's given
        os.utime(old.figure_mesh.path, (0, 0))        # so a rewrite shows in the mtime
        tts_figures.ensure_figure(old, '#d2691e')
        new = KeepPiece.objects.get(pk=self.piece.pk)
        self.assertNotEqual(new.figure_fingerprint, old_fingerprint)
        self.assertGreater(os.path.getmtime(new.figure_mesh.path), 0)   # regenerated

    def test_generate_includes_the_supply(self):
        from the_keep.services.tts import build_tts_objects
        objects = build_tts_objects(self.post)
        bags = [o for o in objects if o['Name'] == 'Custom_Model_Bag']
        self.assertEqual(len(bags), 1)
        self.assertEqual(bags[0]['Nickname'], 'Cats Supply')
        self.assertEqual(len(bags[0]['ContainedObjects']), 25)
        self.assertEqual(bags[0]['ContainedObjects'][0]['Nickname'], 'Cats Warrior')
        self.assertTrue(bags[0]['CustomMesh']['MeshURL'].endswith('-figure.obj'))
        self.piece.refresh_from_db()
        self.assertTrue(os.path.exists(self.piece.figure_mesh.path))
        self.assertTrue(self.piece.figure_mesh.name.startswith('uploads/posts/cats/warriors/'))
        with Image.open(self.piece.figure_texture.path) as tex:
            self.assertEqual(tex.getpixel((1000, 1000)), (0xd2, 0x69, 0x1e))


# ── Map builder: Paths & Rivers (tts_map.path_shapes, clean_paths) ──────────

class MapPathTests(TestCase):
    """The Forge builder's Paths step: its data rules and geometry (the
    builder's JS mirrors these)."""

    def _state(self, paths, clearings=None, visited=('paths',)):
        from the_keep.services.tts_map import FORGE_FRAME
        if clearings is None:
            # Three clearings in a row (the middle one in the way of 0-2), one below.
            clearings = [(0.2, 0.5), (0.5, 0.5), (0.8, 0.5), (0.5, 0.85)]
        return {'clearings': [{'centre': [u, v], 'marker': [u + 0.06, v], 'number': i + 1}
                              for i, (u, v) in enumerate(clearings)],
                'paths': paths, 'visited': list(visited), 'frame': FORGE_FRAME}

    def _geometry(self):
        from the_keep.services.tts_map import FORGE_FRAME, MapGeometry
        return MapGeometry(*FORGE_FRAME)

    def _circle(self, state, i):
        g = self._geometry()
        c = state['clearings'][i]
        cx, cy = g.to_pixels(*c['centre'])
        mx, my = g.to_pixels(*c['marker'])
        return cx, cy, math.hypot(mx - cx, my - cy)

    def test_cleaning_drops_bad_paths(self):
        from the_keep.services.tts_map import clean_paths
        raw = [
            {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},        # kept: 0
            {'kind': 'road', 'a': {'c': 0}, 'b': {'c': 2}},        # unknown kind
            {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 2}},       # kept: 1 (joins path 0)
            {'kind': 'special', 'a': {'c': 1}, 'b': {'c': 0}},     # same ends as path 0
            {'kind': 'path', 'a': {'p': 4}, 'b': {'c': 0}},        # attached to itself
            {'kind': 'path', 'a': {'p': 1}, 'b': {'c': 9}},        # clearing out of range
            {'kind': 'path', 'a': {'c': 2}, 'b': {'c': 2}},        # an end to itself
        ]
        self.assertEqual(clean_paths(raw, 3), [
            {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
            {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 2}},
        ])

    def test_cleaning_renumbers_and_cascades(self):
        from the_keep.services.tts_map import clean_paths
        raw = [
            {'kind': 'road', 'a': {'c': 0}, 'b': {'c': 1}},        # dropped...
            {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
            {'kind': 'river', 'a': {'p': 1}, 'b': {'c': 2}},       # ...so this now joins path 0
            {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 2}},       # joined the dropped one: goes too
        ]
        self.assertEqual(clean_paths(raw, 3), [
            {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
            {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 2}},
        ])
        # Two paths attached to each other's middles: a loop, both dropped.
        loop = [{'kind': 'path', 'a': {'p': 1}, 'b': {'c': 0}}, {'kind': 'path', 'a': {'p': 0}, 'b': {'c': 1}}]
        self.assertEqual(clean_paths(loop, 3), [])

    def test_clean_builder_state_keeps_paths(self):
        from the_keep.services.tts_map import clean_builder_state
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}, {'kind': 'x'}])
        self.assertEqual(clean_builder_state(state)['paths'], [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}])
        self.assertEqual(clean_builder_state(state)['visited'], ['paths'])

    def test_paths_start_at_the_circles_and_bend_around_clearings(self):
        from the_keep.services.tts_map import PATH_WIDTH, path_shapes
        state = self._state([{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}}])
        straight, bent = path_shapes(self._geometry(), state)
        for shape, (first, last) in ((straight, (0, 1)), (bent, (0, 2))):
            for point, i in ((shape['points'][0], first), (shape['points'][-1], last)):
                cx, cy, r = self._circle(state, i)
                self.assertAlmostEqual(math.hypot(point[0] - cx, point[1] - cy), r, delta=1)
        # Nothing in the way: a straight line.
        self.assertTrue(all(abs(y - straight['points'][0][1]) < 1e-6 for _x, y in straight['points']))
        # Clearing 1 sits between 0 and 2: the path bends round it, keeping half a width clear.
        cx, cy, r = self._circle(state, 1)
        clearance = r + PATH_WIDTH * self._geometry().px_per_local / 2
        self.assertTrue(all(math.hypot(x - cx, y - cy) >= clearance - 1e-6 for x, y in bent['points']))

    def test_a_path_can_join_another_paths_middle(self):
        from the_keep.services.tts_map import path_shapes
        state = self._state([{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'river', 'a': {'c': 3}, 'b': {'p': 0}}])
        path, river = path_shapes(self._geometry(), state)
        self.assertEqual(river['points'][-1], path['middle'])
        # The middle is halfway along the drawn line.
        points = path['points']
        lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
        mx = path['middle'][0]
        before = sum(l for (a, _b), l in zip(zip(points, points[1:]), lengths) if a[0] < mx)
        self.assertAlmostEqual(before, sum(lengths) / 2, delta=max(lengths))

    def test_special_path_types(self):
        # Kept by key (the first, red, is the default: no key); an unknown key is red.
        from the_keep.services.tts_map import clean_paths, layout_summary, path_shapes
        paths = clean_paths([{'kind': 'special', 'special': 'green', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'special', 'special': 'red', 'a': {'c': 1}, 'b': {'c': 2}},
                             {'kind': 'special', 'special': 'purple', 'a': {'c': 2}, 'b': {'c': 3}},
                             {'kind': 'path', 'special': 'green', 'a': {'c': 0}, 'b': {'c': 3}}], 4)
        self.assertEqual([p.get('special') for p in paths], ['green', None, None, None])
        # Each drawn (printed, and on the card) in its type's colour.
        state = self._state(paths)
        self.assertEqual([s['colour'] for s in path_shapes(self._geometry(), state)],
                         ['#2f9e44', '#ed2731', '#ed2731', '#ffffff'])
        # The summary: Paths, River Clearings, then each type, by its name or colour.
        state['visited'] = ['paths']
        tiles = layout_summary(state, map_card={'special_paths': {'green': {'name': 'Bridges'}}})['path_tiles']
        self.assertEqual([(t['kind'], t['label'], t['count']) for t in tiles],
                         [('path', 'Paths', 1), ('river', 'River Clearings', 0),
                          ('special', 'Red Paths', 2), ('special', 'Bridges', 1)])
        self.assertEqual(tiles[3]['colour'], '#2f9e44')
        # Just one type: "Special Paths", as maps had.
        state['paths'] = [p for p in paths if p.get('special') != 'green']
        self.assertEqual([t['label'] for t in layout_summary(state)['path_tiles']][2:], ['Special Paths'])

    def test_river_clearings_count_once_and_only_once_visited(self):
        from the_keep.services.tts_map import layout_counts, path_totals
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'river', 'a': {'c': 1}, 'b': {'c': 2}},
                             {'kind': 'special', 'a': {'c': 0}, 'b': {'c': 3}},
                             {'kind': 'path', 'a': {'c': 2}, 'b': {'c': 3}}])
        self.assertEqual(path_totals(state), {'path': 1, 'special': 1, 'river': 2, 'lake': 0, 'river_clearings': 3,
                                              'specials': {'red': 1}})
        self.assertEqual(layout_counts(state)['river_clearings'], 3)
        state['visited'] = []
        self.assertNotIn('river_clearings', layout_counts(state))

    def test_river_clearings_update_the_map(self):
        from the_keep.models import Map
        from the_keep.services.tts_map import apply_builder_counts
        designer = Profile.objects.create(discord='riverdesigner')
        keep_map = Map.objects.create(title='River Map', designer=designer, clearings=4, river_clearings=7)
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}], visited=())
        apply_builder_counts(keep_map, state)
        self.assertEqual(Map.objects.get(pk=keep_map.pk).river_clearings, 7)   # Paths step not reached
        state['visited'] = ['paths']
        apply_builder_counts(keep_map, state)
        self.assertEqual(Map.objects.get(pk=keep_map.pk).river_clearings, 2)

    def test_rivers_join_through_clearings_and_run_off_the_edge(self):
        from the_keep.services.tts_map import FORGE_FRAME, path_shapes, river_links
        g = self._geometry()
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}},
                             {'kind': 'river', 'a': {'c': 3}, 'b': {'c': 2}}])
        shapes = path_shapes(g, state)
        links = river_links(g, state, shapes)
        # Clearing 3 has two rivers: one curve joins their ends through it.
        ends = {shapes[0]['points'][-1], shapes[1]['points'][0]}
        joins = [l for l in links if {tuple(l[0]), tuple(l[-1])} == ends]
        self.assertEqual(len(joins), 1)
        # Clearings 0 and 2 have one river each: it runs on to the map's edge.
        runs = [l for l in links if l is not joins[0]]
        self.assertEqual(len(runs), 2)
        for run in runs:
            # It reaches the edge, then carries on past it (so its flat end is
            # wholly off the page, whatever the angle).
            x, y = run[-2]
            self.assertTrue(min(x, y, FORGE_FRAME[0] - x, FORGE_FRAME[1] - y) < 1e-6, run[-2])
            x, y = run[-1]
            self.assertTrue(min(x, y, FORGE_FRAME[0] - x, FORGE_FRAME[1] - y) < 0, run[-1])
        # Paths don't get links.
        state['paths'][0]['kind'] = 'path'
        state['paths'][1]['kind'] = 'path'
        self.assertEqual(river_links(g, state), [])

    def test_printed_clearing_circles_sit_a_fixed_inset_inside(self):
        from the_keep.services.tts_map import CLEARING_OUTLINE_INSET, print_layout
        state = self._state([], clearings=[(0.3, 0.4), (0.7, 0.4)])
        state['clearings'][1]['marker'] = [0.705, 0.4]            # a tiny clearing (~11 px)
        big, tiny = print_layout(self._geometry(), state)['clearings']
        self.assertAlmostEqual(big[2], self._circle(state, 0)[2] - CLEARING_OUTLINE_INSET)
        self.assertAlmostEqual(tiny[2], self._circle(state, 1)[2] / 2)   # never below half

    # ── The layout summary card's data (map_summary.js draws it) ──
    def test_layout_summary(self):
        from the_keep.services.tts_map import layout_summary, suit_icons
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'path', 'a': {'c': 1}, 'b': {'c': 2}}])
        state['clearings'][0]['suit'] = 'fox'
        state['clearings'][1]['suit'] = 'fox'
        state['clearings'][2]['suit'] = 'bird'
        state['buildings'] = [{'at': [0.2, 0.2], 'ruin': True}, {'at': [0.3, 0.2], 'ruin': False}]
        summary = layout_summary(state, fixed=True)
        self.assertEqual([(t['kind'], t['count']) for t in summary['tiles']],
                         [('clearing', 4), ('building', 2), ('ruin', 1), ('forest', 0)])
        # Special paths only when there are some.
        self.assertEqual([(t['kind'], t['count']) for t in summary['path_tiles']], [('path', 1), ('river', 2)])
        self.assertEqual([(c['suit'], c['count']) for c in summary['suits']['chips']], [('fox', 2), ('bird', 1)])
        self.assertEqual(summary['suits']['chips'][0]['icon'], suit_icons()['fox'])
        self.assertEqual(summary['suits']['unset'], 1)
        # The layout's own choice wins over the Map's.
        state['fixed'] = False
        self.assertFalse(layout_summary(state, fixed=True)['suits']['fixed'])
        # No path tiles before the Paths step is reached.
        state['visited'] = []
        self.assertEqual(layout_summary(state)['path_tiles'], [])

    def test_figures_summary_and_which_one_a_map_gets(self):
        from the_keep.models import Map
        from the_keep.services.tts_map import map_summary
        designer = Profile.objects.create(discord='summarydesigner')
        keep_map = Map.objects.create(title='Old Map', designer=designer, clearings=12, forests=None,
                                      building_slots=22, ruins=4, river_clearings=3, fixed_clearings=True)
        summary = map_summary(keep_map)    # no layout: the Map's own figures
        self.assertEqual([(t['kind'], t['count']) for t in summary['tiles']],
                         [('clearing', 12), ('building', 22), ('ruin', 4)])   # forests not set
        self.assertEqual([(t['kind'], t['count']) for t in summary['path_tiles']], [('river', 3)])
        self.assertTrue(summary['suits']['fixed'])
        self.assertNotIn('chips', summary['suits'])
        keep_map.tts_builder = self._state([])
        self.assertEqual(map_summary(keep_map)['tiles'][0]['count'], 4)       # the layout's

    # ── Printed rivers: merges and run-offs (print_rivers) ──
    def _print_rivers(self, paths, clearings=None):
        from the_keep.services.tts_map import path_shapes, print_rivers
        state = self._state(paths, clearings=clearings)
        shapes = path_shapes(self._geometry(), state)
        return state, shapes, print_rivers(self._geometry(), state, shapes)

    def _on_line(self, point, line):
        return min(_segment_distance(point, a, b) for a, b in zip(line, line[1:]))

    def _angle(self, u, v):
        lu, lv = math.hypot(*u), math.hypot(*v)
        return math.degrees(math.acos(max(-1, min(1, abs(u[0] * v[0] + u[1] * v[1]) / (lu * lv)))))

    def test_a_river_curves_into_another_rivers_middle(self):
        from the_keep.services.tts_map import MERGE_REACH, PATH_WIDTH, RIVER_WIDTH
        width = PATH_WIDTH * RIVER_WIDTH * self._geometry().px_per_local
        # River 1 runs from clearing 3 (below) into river 0's middle (0 -> 2, along the top).
        for joining in ({'kind': 'river', 'a': {'c': 3}, 'b': {'p': 0}},      # its b end
                        {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 3}}):     # its a end
            with self.subTest(joining=joining):
                state, shapes, rivers = self._print_rivers(
                    [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}, joining],
                    clearings=[(0.2, 0.4), (0.8, 0.4), (0.9, 0.9), (0.5, 0.85)])
                target = shapes[0]['points']
                merge = next(l for l in rivers['links'] if self._on_line(l[-1], target) < 1)
                # Its end lies on the other river, arriving along it.
                last = (merge[-1][0] - merge[-2][0], merge[-1][1] - merge[-2][1])
                k = min(range(len(target) - 1), key=lambda n: _segment_distance(merge[-1], target[n], target[n + 1]))
                along = (target[k + 1][0] - target[k][0], target[k + 1][1] - target[k][1])
                self.assertLess(self._angle(last, along), 10)
                # Its own line is cut back where the curve begins.
                trimmed = rivers['trimmed'][1]
                full = shapes[1]['points']
                lost = (math.hypot(*(p - q for p, q in zip(full[-1], trimmed[-1]))) if 'p' in joining['b']
                        else math.hypot(*(p - q for p, q in zip(full[0], trimmed[0]))))
                self.assertAlmostEqual(lost, MERGE_REACH * width, delta=width * 0.2)
                self.assertTrue(any(math.hypot(merge[0][0] - p[0], merge[0][1] - p[1]) < 1e-6
                                    for p in (trimmed[0], trimmed[-1])))

    def test_a_short_joining_river_keeps_half_its_length(self):
        from the_keep.services.tts_map import MERGE_REACH, PATH_WIDTH, RIVER_WIDTH, _polyline_length
        # Clearing 1 sits right beside river 0's middle: a very short river.
        _state, shapes, rivers = self._print_rivers(
            [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 2}}, {'kind': 'river', 'a': {'c': 1}, 'b': {'p': 0}}],
            clearings=[(0.2, 0.3), (0.5, 0.4), (0.8, 0.3)])
        full = _polyline_length(shapes[1]['points'])
        self.assertLess(full, 2 * MERGE_REACH * PATH_WIDTH * RIVER_WIDTH * self._geometry().px_per_local)   # short enough
        self.assertAlmostEqual(_polyline_length(rivers['trimmed'][1]), full / 2, delta=1)

    def test_a_chain_merges_into_the_printed_line(self):
        # C (0 -> 1) along the top; B from clearing 2 into C's middle; A from clearing 3 into B's middle.
        _state, shapes, rivers = self._print_rivers(
            [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}},
             {'kind': 'river', 'a': {'c': 2}, 'b': {'p': 0}},
             {'kind': 'river', 'a': {'c': 3}, 'b': {'p': 1}}],
            clearings=[(0.15, 0.2), (0.85, 0.2), (0.5, 0.9), (0.9, 0.6)])
        b_printed = rivers['trimmed'][1]
        self.assertTrue(any(self._on_line(link[-1], b_printed) < 1 for link in rivers['links']))

    def test_an_odd_river_at_a_clearing_curves_into_the_pair(self):
        from the_keep.services.tts_map import CLEARING_OUTLINE_INSET
        # Clearing 0 in the middle, with rivers to 1 (left), 2 (right) and 3 (below).
        state, _shapes, rivers = self._print_rivers(
            [{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}, {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 2}},
             {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}],
            clearings=[(0.5, 0.5), (0.15, 0.5), (0.85, 0.5), (0.5, 0.9)])
        cx, cy, _r = self._circle(state, 0)
        # Nothing runs to the clearing's centre any more...
        self.assertFalse(any(math.hypot(l[-1][0] - cx, l[-1][1] - cy) < 1 for l in rivers['links']))
        # ...the odd one ends on the straight pair's curve through the clearing.
        pair = next(l for l in rivers['links'] if abs(l[0][1] - cy) < 1 and abs(l[-1][1] - cy) < 1)
        self.assertTrue(any(self._on_line(l[-1], pair) < 1 for l in rivers['links'] if l is not pair))

    def test_a_river_into_a_path_middle_runs_off_the_map(self):
        from the_keep.services.tts_map import FORGE_FRAME
        _state, shapes, rivers = self._print_rivers(
            [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}}, {'kind': 'river', 'a': {'c': 2}, 'b': {'p': 0}}],
            clearings=[(0.2, 0.3), (0.8, 0.3), (0.5, 0.8)])
        junction = shapes[1]['points'][-1]
        run = next(l for l in rivers['links'] if math.hypot(l[0][0] - junction[0], l[0][1] - junction[1]) < 1e-6)
        x, y = run[-1]
        self.assertLess(min(x, y, FORGE_FRAME[0] - x, FORGE_FRAME[1] - y), 0)   # past the edge
        self.assertNotIn(1, rivers['trimmed'])                                  # not cut back

    # A river from clearing 0 ending at 1, heading down-left, with clearing 2
    # in its way off the map: round 2's bottom is the long way, round its top
    # (turning in 1) the short way to the left edge.
    BLOCKED_RUN_OFF = [(0.47, 0.21), (0.30, 0.36), (0.13, 0.49)]

    def test_a_blocked_run_off_takes_the_shorter_way(self):
        from the_keep.services.tts_map import RIVER_WIDTH, RUN_OFF_MAX_TURN, _circles, _path_px, _run_off
        state, shapes, rivers = self._print_rivers([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}],
                                                   clearings=self.BLOCKED_RUN_OFF)
        self.assertEqual(rivers['springs'], [])
        turn, _side = rivers['run_offs'][(0, 'b')]
        self.assertNotEqual(turn, 0)
        cx, cy, r = _circles(self._geometry(), state)[1]
        ox, oy, r2 = _circles(self._geometry(), state)[2]
        at = shapes[0]['points'][-1]
        join = next(link for link in rivers['links'] if math.dist(link[0], at) < 1e-6)
        run = next(link for link in rivers['links'] if math.dist(link[0], join[-1]) < 1e-6 and link is not join)
        self.assertLess(run[-1][0], 0)                                       # off the left edge...
        self.assertLess(run[-1][1], oy)                                      # ...above clearing 2
        # Shorter than the way round 2's bottom (straight on, bent as a path).
        before = shapes[0]['points'][-2]
        d = ((at[0] - before[0]), (at[1] - before[1]))
        d = (d[0] / math.hypot(*d), d[1] / math.hypot(*d))
        half = _path_px(self._geometry())['width'] * RIVER_WIDTH / 2
        old = _run_off(at, d, self._geometry(), [(ox, oy, r2 + half)], half)
        length = lambda line: sum(math.dist(p, q) for p, q in zip(line, line[1:]))     # noqa: E731
        self.assertLess(length(join) + length(run), length(old))
        # Joined as two rivers are: on from the river's end along its way...
        self.assertLess(math.dist(join[0], at), 1e-6)
        self.assertLess(self._angle((join[1][0] - join[0][0], join[1][1] - join[0][1]), d), 1)
        # ...to the run's start on the clearing's rim, along the run's way out.
        self.assertLess(math.dist(join[-1], run[0]), 1e-6)
        self.assertAlmostEqual(math.dist(run[0], (cx, cy)), r, places=3)
        self.assertLess(self._angle((join[-1][0] - join[-2][0], join[-1][1] - join[-2][1]),
                                    (run[1][0] - run[0][0], run[1][1] - run[0][1])), 1)
        # Never more than RUN_OFF_MAX_TURN from the river's way.
        out = (run[1][0] - run[0][0], run[1][1] - run[0][1])
        self.assertLessEqual(math.degrees(math.acos((out[0] * d[0] + out[1] * d[1]) / math.hypot(*out))),
                             RUN_OFF_MAX_TURN + 1e-6)

    def test_a_run_off_never_turns_back(self):
        from the_keep.services.tts_map import MapGeometry, _clearing_run_off
        # Heading left from (1000, 1000), walled in on every side but behind:
        # no way off within RUN_OFF_MAX_TURN, so it's carried on as before.
        walls = [(1000 + 450 * math.cos(math.radians(a)), 1000 + 450 * math.sin(math.radians(a)), 260)
                 for a in range(-170, 171, 20) if abs(a) >= 60]
        way = _clearing_run_off((900, 1000), (-1, 0), (1000, 1000, 100), MapGeometry(2000, 2000), walls, 10)
        self.assertIsNone(way['choice'])
        self.assertIsNone(way['join'])

    def test_an_unblocked_run_off_runs_straight_on(self):
        _state, shapes, rivers = self._print_rivers([{'kind': 'river', 'a': {'c': 1}, 'b': {'c': 0}}],
                                                    clearings=[(0.1, 0.5), (0.4, 0.5)])
        self.assertEqual(rivers['run_offs'], {(0, 'b'): (0, 0)})
        run, = rivers['links']
        self.assertLess(math.dist(run[0], shapes[0]['points'][-1]), 1e-6)       # from the river's end
        self.assertTrue(all(abs(y - run[0][1]) < 1e-6 for _x, y in run))      # straight on

    def test_the_setup_card_runs_off_the_printed_maps_way(self):
        from unittest import mock
        from the_keep.services import map_setup_card as msc
        from the_keep.services.tts_map import FORGE_FRAME
        state, _shapes, rivers = self._print_rivers([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}],
                                                    clearings=self.BLOCKED_RUN_OFF)
        with mock.patch.object(msc, 'river_extras', wraps=msc.river_extras) as extras:
            msc._Diagram(state, FORGE_FRAME, (160, 140), False)
        kwargs = extras.call_args.kwargs
        self.assertEqual(kwargs['run_offs'], rivers['run_offs'])
        card = msc.river_extras(*extras.call_args.args, **kwargs)
        self.assertEqual(card['run_offs'], rivers['run_offs'])               # the same way off...
        self.assertEqual(len(card['links']), len(card['run_offs']))          # ...each end's run alone (no join)

    # ── Lakes ──
    def test_lake_rules(self):
        from the_keep.services.tts_map import clean_paths
        raw = [{'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}},
               {'kind': 'river', 'a': {'c': 1}, 'b': {'c': 0}},        # water twin of the lake: dropped
               {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},         # land beside it: kept
               {'kind': 'path', 'a': {'c': 2}, 'b': {'p': 0}},         # on the lake's middle: dropped
               {'kind': 'lake', 'a': {'c': 2}, 'b': {'p': 2}}]         # a lake to a path's middle: dropped
        self.assertEqual([(p['kind'], p['a'], p['b']) for p in clean_paths(raw, 3)],
                         [('lake', {'c': 0}, {'c': 1}), ('path', {'c': 0}, {'c': 1})])

    def test_lake_shapes_are_kept_and_held_to_size(self):
        from the_keep.services.tts_map import LAKE_RING_POINTS_MAX, clean_builder_state
        ring = [[0.1, 0.1], [0.3, 0.12345678], [0.2, 1.3]]
        state = self._state([{'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}}])
        state['lake_shapes'] = [ring, [[0, 0]]]
        self.assertEqual(clean_builder_state(state)['lake_shapes'], [[[0.1, 0.1], [0.3, 0.1235], [0.2, 1.05]]])
        state['lake_shapes'] = [[[0.1, 0.1]] * (LAKE_RING_POINTS_MAX + 1)]
        self.assertNotIn('lake_shapes', clean_builder_state(state))         # too big: dropped
        state['lake_shapes'] = [[['x', 0.1], [0.2, 0.2], [0.3, 0.3]]]
        self.assertNotIn('lake_shapes', clean_builder_state(state))         # malformed
        state = self._state([{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}])
        state['lake_shapes'] = [ring]
        self.assertNotIn('lake_shapes', clean_builder_state(state))         # no lake

    def test_lake_clearings_count_as_river_clearings(self):
        from the_keep.services.tts_map import layout_summary, path_totals
        state = self._state([{'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'lake', 'a': {'c': 1}, 'b': {'c': 2}},
                             {'kind': 'river', 'a': {'c': 2}, 'b': {'c': 3}}])
        totals = path_totals(state)
        self.assertEqual((totals['lake'], totals['river_clearings']), (1, 4))
        tiles = {t['kind']: t['count'] for t in layout_summary(state, fixed=False)['path_tiles']}
        self.assertNotIn('lake', tiles)                 # no Lakes tile
        self.assertEqual(tiles['river'], 4)

    def _river_into_lake(self, rivers, lake_shapes=None):
        """Clearing 1 on a lake (1-2, to its right); `rivers` end at it."""
        paths = [{'kind': 'lake', 'a': {'c': 1}, 'b': {'c': 2}}] + rivers
        state = self._state(paths, clearings=[(0.2, 0.5), (0.5, 0.5), (0.8, 0.5), (0.5, 0.15), (0.5, 0.85)])
        if lake_shapes:
            state['lake_shapes'] = lake_shapes
        from the_keep.services.tts_map import path_shapes, print_rivers
        shapes = path_shapes(self._geometry(), state)
        return state, shapes, print_rivers(self._geometry(), state, shapes)

    def test_a_river_flows_through_its_clearing_into_the_lake(self):
        # Without an outline: from the river's end on to where the lake's line leaves the clearing.
        state, shapes, rivers = self._river_into_lake([{'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}}])
        self.assertFalse(any(s['key'] == (1, 'b') for s in rivers['springs']))    # no rounded end, no run-off
        arrive = shapes[1]['points'][-1]
        lake_end = shapes[0]['points'][0]
        link = next(l for l in rivers['links'] if math.dist(l[0], arrive) < 1e-6)
        from the_keep.services.tts_map import PATH_WIDTH, RIVER_WIDTH
        half = PATH_WIDTH * self._geometry().px_per_local * RIVER_WIDTH / 2
        self.assertLess(math.dist(link[-1], lake_end), half + 1)       # nudged half a river into the water
        # With an outline: to its nearest point to the clearing (a little into the water).
        ring = [[0.55, 0.45], [0.75, 0.45], [0.75, 0.55], [0.55, 0.55]]
        state, shapes, rivers = self._river_into_lake([{'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}}], [ring])
        g = self._geometry()
        link = next(l for l in rivers['links'] if math.dist(l[0], shapes[1]['points'][-1]) < 1e-6)
        x0 = g.to_pixels(0.55, 0.5)[0]
        self.assertAlmostEqual(link[-1][1], g.to_pixels(0.5, 0.5)[1], delta=2)
        self.assertGreater(link[-1][0], x0)            # just inside the water's edge
        self.assertLess(link[-1][0], x0 + half + 1)

    def test_a_river_the_lakes_funnel_reaches_gets_no_link(self):
        # The outline curves out to the river's end on the rim (map_lakes.js's
        # funnel): the river stops there and the lake carries on from it.
        g = self._geometry()
        state, shapes, rivers = self._river_into_lake([{'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}}])
        end = shapes[1]['points'][-1]
        u, v = end[0] / g.width, end[1] / g.height
        ring = [[u - 0.02, v - 0.04], [u + 0.02, v - 0.04], [u + 0.02, v + 0.03], [u - 0.02, v + 0.03]]
        state, shapes, rivers = self._river_into_lake([{'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}}], [ring])
        self.assertFalse(any(math.dist(l[0], end) < 1e-6 for l in rivers['links']))
        from the_keep.services.tts_map import RIVER_LINK_REACH, path_config
        self.assertEqual(path_config(g)['link_reach'], RIVER_LINK_REACH)

    def test_several_rivers_feed_one_lake(self):
        state, shapes, rivers = self._river_into_lake([{'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}},
                                                       {'kind': 'river', 'a': {'c': 4}, 'b': {'c': 1}}])
        starts = [shapes[i]['points'][-1] for i in (1, 2)]
        for start in starts:
            self.assertTrue(any(math.dist(l[0], start) < 1e-6 for l in rivers['links']))

    def test_lake_settings_reach_the_builder(self):
        from the_keep.services.tts_map import path_config
        lake = path_config(self._geometry())['lake']
        self.assertEqual(lake['clearing_overlap'], 0.25)
        for key in ('smooth', 'min_half', 'round', 'cross_reach', 'touch', 'lens', 'funnel', 'variation',
                    'shore_margin'):
            self.assertGreater(lake[key], 0)
        self.assertNotIn('building_r', lake)            # buildings don't shape lakes (their clearings do)

    def test_the_setup_cards_fit_reaches_the_builder(self):
        from the_keep.services import map_setup_card as msc
        from the_keep.services.tts_map import SUITS, path_config
        card = path_config(self._geometry())['card']
        self.assertEqual(card['clearing_r'], msc.CLEARING_R)
        self.assertEqual(card['box_w'], msc._card_size()[0] - 2 * msc.MARGIN)
        self.assertEqual((card['min_h'], card['max_h']), (msc.DIAGRAM_MIN_H, msc.DIAGRAM_MAX_H))
        self.assertEqual(card['pad'], msc.CLEARING_R + 2)
        self.assertEqual((card['stretch'], card['min_scale']), (msc.DIAGRAM_MAX_STRETCH, msc.CLEARING_MIN_SCALE))
        self.assertEqual(card['lake_wobble'], msc.LAKE_WOBBLE)
        self.assertEqual(set(card['icon_half']), set(SUITS))
        self.assertEqual(card['icon_half']['city'], msc.SUIT_ICON_H * msc.SUIT_ICON_SCALE['city'] / 2)

    def test_print_layout_gives_the_lake_outline_or_a_line(self):
        from the_keep.services.tts_map import print_layout
        state = self._state([{'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}}])
        layout = print_layout(self._geometry(), state)
        self.assertEqual((layout['lakes'], layout['lake_lines']), ([], True))
        state['lake_shapes'] = [[[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]]]
        layout = print_layout(self._geometry(), state)
        self.assertEqual(len(layout['lakes'][0]), 3)
        self.assertFalse(layout['lake_lines'])

    # ── Springs: a loose end far from the edge it's heading for ──
    def test_a_river_heading_inland_starts_at_its_clearing(self):
        # Clearing 0 near the left edge; the river flows from 1 into 0, i.e.
        # leftwards, at 0: the left edge is near, so it runs off.
        _state, _shapes, rivers = self._print_rivers([{'kind': 'river', 'a': {'c': 1}, 'b': {'c': 0}}],
                                                     clearings=[(0.1, 0.5), (0.4, 0.5)])
        self.assertEqual([s['key'] for s in rivers['springs']], [(0, 'a')])   # at 1, heading right: a spring
        self.assertEqual(len(rivers['links']), 1)                             # only 0's end runs off
        x, _y = rivers['links'][0][-1]
        self.assertLess(x, 0)                                                 # off the left edge

    def test_a_spring_on_a_path_middle(self):
        # A river ending on the middle of a path near the top, heading down
        # into the map: far from the bottom edge, near the top one.
        _state, _shapes, rivers = self._print_rivers(
            [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}}, {'kind': 'river', 'a': {'p': 0}, 'b': {'c': 2}}],
            clearings=[(0.2, 0.1), (0.8, 0.1), (0.5, 0.9)])
        # Heading up at the path's middle (from 2 to it): the top edge is near, so it runs off...
        self.assertEqual(rivers['springs'], [])
        # A path across the lower middle, and a river from a clearing near the
        # bottom up into its middle: heading up there, the top edge is far and
        # the bottom near, so it starts at the path (its clearing end runs off).
        _state, _shapes, rivers = self._print_rivers(
            [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}}, {'kind': 'river', 'a': {'c': 2}, 'b': {'p': 0}}],
            clearings=[(0.2, 0.6), (0.8, 0.6), (0.5, 0.9)])
        self.assertEqual([s['key'] for s in rivers['springs']], [(1, 'b')])
        self.assertEqual(len(rivers['links']), 1)

    def test_print_layout_gives_springs_their_rounded_ends(self):
        from the_keep.services.tts_map import print_layout
        state = self._state([{'kind': 'river', 'a': {'c': 1}, 'b': {'c': 0}}], clearings=[(0.1, 0.5), (0.4, 0.5)])
        layout = print_layout(self._geometry(), state)
        self.assertEqual(len(layout['river_springs']), 1)
        x, y = layout['river_springs'][0]
        cx, cy, r = self._circle(state, 1)
        self.assertAlmostEqual(math.hypot(x - cx, y - cy), r, delta=1.0)     # at clearing 1's circle

    # ── A river and a path between the same two clearings ──
    def test_a_river_and_a_path_may_share_two_clearings(self):
        from the_keep.services.tts_map import clean_paths
        raw = [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
               {'kind': 'river', 'a': {'c': 1}, 'b': {'c': 0}},       # the other family: kept
               {'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}},     # a second land path: dropped
               {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}]       # a second river: dropped
        self.assertEqual([p['kind'] for p in clean_paths(raw, 2)], ['path', 'river'])

    def test_a_shared_river_and_path_bow_apart(self):
        from the_keep.services.tts_map import PATH_WIDTH, RIVER_WIDTH, path_shapes
        g = self._geometry()
        width = PATH_WIDTH * g.px_per_local
        alone = path_shapes(g, self._state([{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}}],
                                           clearings=[(0.2, 0.3), (0.5, 0.9), (0.8, 0.3)]))
        path, river = path_shapes(g, self._state(
            [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}}, {'kind': 'river', 'a': {'c': 2}, 'b': {'c': 0}}],
            clearings=[(0.2, 0.3), (0.5, 0.9), (0.8, 0.3)]))
        # Alone it's straight; sharing, the two are apart by more than their half-widths at the middle.
        self.assertTrue(all(abs(y - alone[0]['points'][0][1]) < 1e-6 for _x, y in alone[0]['points']))
        gap = math.hypot(path['middle'][0] - river['middle'][0], path['middle'][1] - river['middle'][1])
        self.assertGreater(gap, width * (RIVER_WIDTH + 1) / 2)
        # On opposite sides of the straight line between the clearings.
        y0 = alone[0]['points'][0][1]
        self.assertLess((path['middle'][1] - y0) * (river['middle'][1] - y0), 0)

    def _shared_pair(self, path_bow=None, river_bow=None, river_reversed=False):
        """A path 0->2 and a river between the same clearings: (path, river, y of the straight line)."""
        from the_keep.services.tts_map import path_shapes
        path = {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}}
        river = {'kind': 'river', 'a': {'c': 2}, 'b': {'c': 0}} if river_reversed \
            else {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 2}}
        if path_bow is not None:
            path['bow'] = path_bow
        if river_bow is not None:
            river['bow'] = river_bow
        shapes = path_shapes(self._geometry(), self._state([path, river], clearings=[(0.2, 0.3), (0.5, 0.9), (0.8, 0.3)]))
        y0 = self._geometry().to_pixels(0.2, 0.3)[1]
        return shapes[0], shapes[1], y0

    def test_bow_chooses_the_side(self):
        # The river 0->2 runs left to right: its normal (-dy, dx) points down (y down).
        _path, river, y0 = self._shared_pair(river_bow=1)
        self.assertGreater(river['middle'][1], y0)
        path, river, y0 = self._shared_pair(river_bow=-1)
        self.assertLess(river['middle'][1], y0)
        self.assertGreater(path['middle'][1], y0)          # the path always on the other side
        # The same choice made on the path instead.
        path, river, y0 = self._shared_pair(path_bow=1)
        self.assertGreater(path['middle'][1], y0)
        self.assertLess(river['middle'][1], y0)

    def test_bow_is_measured_along_its_own_direction(self):
        # A river drawn 2->0 (reversed) with bow -1 bows the way 0->2 with bow 1 does.
        _p, forward, _y = self._shared_pair(river_bow=1)
        _p, backward, _y = self._shared_pair(river_bow=-1, river_reversed=True)
        self.assertAlmostEqual(forward['middle'][1], backward['middle'][1], places=6)

    def test_clean_paths_keeps_bow(self):
        from the_keep.services.tts_map import clean_paths
        raw = [{'kind': 'path', 'a': {'c': 9}, 'b': {'c': 1}},          # dropped: renumbers the rest
               {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}, 'bow': -1},
               {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}, 'bow': 2},
               {'kind': 'special', 'a': {'c': 1}, 'b': {'c': 2}, 'bow': True}]
        self.assertEqual([p.get('bow') for p in clean_paths(raw, 3)], [-1, None, None])

    # ── Dragged arcs: side, the clearings they go round, and bulge ──
    def _bent(self, **bend):
        from the_keep.services.tts_map import path_shapes
        state = self._state([dict({'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}}, **bend)])
        return state, path_shapes(self._geometry(), state)[0]

    def _clear_of(self, shape, circle):
        cx, cy, r = circle
        return all(math.hypot(x - cx, y - cy) > r for x, y in shape['points'])

    def test_a_dragged_arc_goes_round_the_clearings_on_its_side(self):
        # 0->2 runs left to right, so side 1 (its normal, y down) is below: round clearing 3.
        state, shape = self._bent(side=1, around=[3])
        below = self._circle(state, 3)
        self.assertGreater(shape['middle'][1], below[1] + below[2])
        self.assertTrue(self._clear_of(shape, below))
        self.assertTrue(self._clear_of(shape, self._circle(state, 1)))
        # Side -1, round nothing: over clearing 1, the other way from its usual route.
        state, shape = self._bent(side=-1)
        self.assertLess(shape['middle'][1], self._circle(state, 1)[1])

    def test_a_bulge_bends_further(self):
        _s, small = self._bent(side=-1)
        _s, big = self._bent(side=-1, bulge=1.0)
        self.assertLess(big['middle'][1], small['middle'][1])

    def test_an_arc_that_cant_go_round_keeps_its_side(self):
        from the_keep.services.tts_map import _route_around
        # Something to go round far beyond the most it may bend, below the line: not ok, still below.
        control, ok = _route_around((0, 0), (10, 0), [], [(5, 500, 1)], 1)
        self.assertFalse(ok)
        self.assertGreater(control[1], 0)
        # Round a clearing on the other side: ignored (the least bend on its side).
        control, ok = _route_around((0, 0), (10, 0), [], [(5, -3, 1)], 1)
        self.assertTrue(ok)
        self.assertGreater(control[1], 0)

    def test_clean_paths_keeps_a_dragged_arc(self):
        from the_keep.services.tts_map import clean_paths
        raw = [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 2}, 'side': -1, 'around': [3, 1, 1, 0, 9, 'x'], 'bulge': 0.123456},
               {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}, 'side': 2, 'around': [3]},
               {'kind': 'path', 'a': {'c': 1}, 'b': {'c': 2}, 'side': 1, 'bulge': 9},
               {'kind': 'path', 'a': {'c': 2}, 'b': {'c': 3}, 'side': True}]
        cleaned = clean_paths(raw, 4)
        self.assertEqual({k: cleaned[0][k] for k in ('side', 'around', 'bulge')},
                         {'side': -1, 'around': [1, 3], 'bulge': 0.123})
        self.assertFalse({'side', 'around', 'bulge'} & set(cleaned[1]))     # no side: none of it
        self.assertEqual((cleaned[2].get('side'), cleaned[2].get('bulge')), (1, None))
        self.assertNotIn('side', cleaned[3])

    def test_a_dragged_path_still_bows_from_its_twin(self):
        # A path dragged over its river (bow) is on the other side of it, bent or not.
        path, river, y0 = self._shared_pair(path_bow=-1)
        self.assertLess(path['middle'][1], river['middle'][1])
        path, river, y0 = self._shared_pair(path_bow=1)
        self.assertGreater(path['middle'][1], river['middle'][1])

    # ── Oceans ──
    def test_ocean_clearings_count_as_river_clearings(self):
        from the_keep.services.tts_map import layout_summary, path_totals
        state = self._state([{'kind': 'ocean', 'a': {'c': 0}, 'b': {'c': 1}},
                             {'kind': 'river', 'a': {'c': 1}, 'b': {'c': 2}}])
        totals = path_totals(state)
        self.assertEqual(totals['river_clearings'], 3)
        self.assertNotIn('ocean', totals)
        self.assertEqual(totals.get('lake', 0), 0)          # oceans aren't lakes
        tiles = {t['kind']: t['count'] for t in layout_summary(state, fixed=False)['path_tiles']}
        self.assertNotIn('ocean', tiles)
        self.assertEqual(tiles['river'], 3)

    def test_an_ocean_keeps_its_shape_and_lake_rules(self):
        from the_keep.services.tts_map import clean_builder_state, clean_paths
        state = self._state([{'kind': 'ocean', 'a': {'c': 0}, 'b': {'c': 1}}])
        state['lake_shapes'] = [[[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]]]
        self.assertIn('lake_shapes', clean_builder_state(state))
        # Clearings only at its ends, as a lake's.
        raw = [{'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}},
               {'kind': 'ocean', 'a': {'c': 2}, 'b': {'p': 0}}]
        self.assertEqual([p['kind'] for p in clean_paths(raw, 3)], ['path'])

    # ── The special paths' name ──
    def test_special_path_name(self):
        from the_keep.services.tts_map import SPECIAL_PATH_NAME_MAX, clean_builder_state, layout_summary
        state = self._state([{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}}])
        state['special_path_name'] = '  Mountain Passes  '
        self.assertEqual(clean_builder_state(state)['special_path_name'], 'Mountain Passes')
        state['special_path_name'] = 'x' * 40
        self.assertEqual(len(clean_builder_state(state)['special_path_name']), SPECIAL_PATH_NAME_MAX)
        state['special_path_name'] = '   '
        self.assertNotIn('special_path_name', clean_builder_state(state))
        # The summary's special paths tile is labelled with it.
        state['special_path_name'] = 'Bridges'
        labels = [t['label'] for t in layout_summary(state)['path_tiles']]
        self.assertIn('Bridges', labels)
        del state['special_path_name']
        self.assertIn('Special Paths', [t['label'] for t in layout_summary(state)['path_tiles']])


# ── Map setup cards (services/map_setup_card.py) ────────────────────────────
from the_keep.services import map_setup_card as msc
from the_keep.services.tts_map import FORGE_FRAME


def _card_layout(paths=(), forests=((0.45, 0.45),), clearings=None, **extra):
    clearings = clearings or [
        {'centre': [0.2, 0.3], 'marker': [0.2, 0.36], 'number': 1},
        {'centre': [0.8, 0.3], 'marker': [0.8, 0.36], 'number': 2},
        {'centre': [0.2, 0.8], 'marker': [0.2, 0.86], 'number': 3},
        {'centre': [0.8, 0.8], 'marker': [0.8, 0.86], 'number': 4},
    ]
    return {'clearings': clearings, 'forests': [list(f) for f in forests], 'paths': list(paths), **extra}


class MapSetupCardDataTests(TestCase):
    def test_clean_setup_card(self):
        card = msc.clean_setup_card({
            'special_path_rule': '  Cross\x07 only by day.  ', 'modifications': 'x' * 999, 'raging_river': 'yes',
            'rules': [{'name': ' Ferry ', 'text': 'Sail.'}, {'name': '', 'text': '  '}, 'junk']
                     + [{'name': f'R{n}', 'text': 't'} for n in range(9)],
        })
        # (A card from before special path types: its one rule is the first type's.)
        self.assertEqual(card['special_paths']['red']['rule'], 'Cross only by day.')
        self.assertEqual(len(card['modifications']), msc.MODIFICATIONS_MAX)
        self.assertIs(card['raging_river'], False)          # only a real True
        self.assertEqual(card['rules'][0], {'name': 'Ferry', 'text': 'Sail.'})
        self.assertEqual(len(card['rules']), msc.RULES_MAX)
        self.assertEqual(msc.clean_setup_card(None)['rules'], [])

    def test_special_paths_divide_forests_by_default(self):
        special = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}}])
        divides = lambda card: msc.special_types(special, card, 'en')[0]['divides_forests']  # noqa: E731
        self.assertTrue(divides({}))
        self.assertFalse(divides({'special_divides_forests': False}))          # (a card from before types)
        self.assertTrue(divides({'special_divides_forests': 'no'}))            # only a real False
        self.assertFalse(divides({'special_paths': {'red': {'divides_forests': False}}}))
        # Without special paths it doesn't apply (and doesn't change the card).
        self.assertEqual(msc.applicable(_card_layout(), {'special_divides_forests': False})['special_paths'], {})

    def test_sections_the_layout_lacks_are_kept_but_not_drawn(self):
        card = {'special_path_rule': 'Bridges.', 'raging_river': True}
        self.assertEqual(msc.applicable(_card_layout(), card)['special_paths'], {})
        self.assertEqual(msc.clean_setup_card(card)['special_paths']['red']['rule'], 'Bridges.')     # (still stored)
        self.assertFalse(msc.applicable(_card_layout(), card)['raging_river'])
        rivers = _card_layout(paths=[{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}])
        self.assertTrue(msc.applicable(rivers, card)['raging_river'])
        # ...and don't change the fingerprint while they don't apply.
        fp = lambda c: msc.card_fingerprint('Map', _card_layout(), c, frame=FORGE_FRAME, lang_code='en')  # noqa: E731
        self.assertEqual(fp(card), fp({}))

    def test_the_fingerprint_ignores_what_the_card_doesnt_draw(self):
        fp = lambda layout, title='Map', lang='en': msc.card_fingerprint(  # noqa: E731
            title, layout, {}, frame=FORGE_FRAME, lang_code=lang)
        base = fp(_card_layout())
        self.assertEqual(base, fp(_card_layout(logo=[0.1, 0.1], track_y=0.5, buildings=[{'at': [0.5, 0.5]}])))
        self.assertNotEqual(base, fp(_card_layout(forests=[(0.5, 0.6)])))
        self.assertNotEqual(base, fp(_card_layout(), title='Other'))
        self.assertNotEqual(base, fp(_card_layout(), lang='fr'))
        self.assertEqual(len(base), 32)

    def test_translations_fall_back_to_english_per_string(self):
        from the_forge import pdf_engine
        with mock.patch.dict(pdf_engine.PDF_TEXT, {'xx': {'map_setup_card': {'raging_river': 'Fleuve'}}}):
            text = msc.card_text('xx')
        self.assertEqual(text['raging_river'], 'Fleuve')
        self.assertEqual(text['modifications'], 'Setup Modifications')
        self.assertEqual(msc.card_text('zz')['special_paths'], 'Special Paths')

    def test_run_in_escapes_and_italicises(self):
        self.assertEqual(msc.run_in('Ferry', 'Sail <b>now</b> (spend a card) & go'),
                         '<b>Ferry.</b> Sail &lt;b&gt;now&lt;/b&gt; <i>(spend a card)</i> &amp; go')
        self.assertEqual(msc.run_in('A & B?', ''), '<b>A &amp; B?</b>')


class ForestTreesTests(TestCase):
    FRAME = (160.0, 120.0)

    def test_a_forest_fills_only_its_own_area(self):
        wall = ([(80.0, -10.0), (80.0, 130.0)], 1.0)     # a path down the middle
        left = msc.forest_trees(self.FRAME, [], [wall], [(40.0, 60.0)])
        self.assertTrue(left)
        self.assertTrue(all(x < 80 for x, _y in left))
        reach = msc.TREE_GAP + msc.TREE_H / 2 + 1.0
        self.assertTrue(all(abs(x - 80) >= reach for x, _y in left))

    def test_trees_keep_clear_of_clearings(self):
        clearing = (80.0, 60.0, 13.0)
        trees = msc.forest_trees(self.FRAME, [clearing], [], [(20.0, 20.0)])
        self.assertTrue(all(math.hypot(x - 80, y - 60) >= 13 + msc.TREE_GAP for x, y in trees))

    def test_avoided_lines_dont_bound_a_forest(self):
        line = ([(80.0, -10.0), (80.0, 130.0)], 1.0)
        trees = msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0)], avoid_lines=[line])
        self.assertTrue(any(x > 80 for x, _y in trees))            # both sides
        reach = msc.TREE_GAP + msc.TREE_H / 2 + 1.0
        self.assertTrue(all(abs(x - 80) >= reach for x, _y in trees))   # but clear of the line
        bounded = msc.forest_trees(self.FRAME, [], [line], [(40.0, 60.0)])
        self.assertFalse(any(x > 80 for x, _y in bounded))

    def test_a_narrow_passage_still_joins_a_forest(self):
        # A suit icon all but closing the frame: too narrow for a tree on
        # either side of it, but it doesn't bound the forest.
        icon = (80.0, 20.0, 15.0)
        trees = msc.forest_trees((160.0, 40.0), [], [], [(20.0, 20.0)], avoid=[icon])
        self.assertTrue(any(x > 100 for x, _y in trees))
        self.assertTrue(all(math.hypot(x - 80, y - 20) >= 15 + msc.TREE_GAP for x, y in trees))
        # A path there does bound it.
        wall = ([(80.0, -10.0), (80.0, 50.0)], 1.0)
        trees = msc.forest_trees((160.0, 40.0), [], [wall], [(20.0, 20.0)])
        self.assertFalse(any(x > 80 for x, _y in trees))

    def test_a_narrow_gap_still_joins_a_forest(self):
        # Two clearings drawn 4 apart, touching the frame's top and bottom:
        # too narrow for a tree, but nothing closes it, so the forest reaches
        # the other side (no tree in the gap).
        top, bottom = (80.0, 17.0, 17.0), (80.0, 55.0, 17.0)
        frame = (160.0, 72.0)
        trees = msc.forest_trees(frame, [top, bottom], [], [(30.0, 45.0)])
        self.assertTrue(any(x > 100 for x, _y in trees))
        self.assertTrue(all(math.hypot(x - cx, y - cy) >= r + msc.TREE_GAP for x, y in trees for cx, cy, r in (top, bottom)))

    def test_clearings_drawn_overlapping_dont_split_a_forest_the_map_doesnt(self):
        # Drawn overlapping, the two clearings shut the frame...
        top, bottom = (80.0, 17.0, 19.0), (80.0, 55.0, 19.0)
        frame = (160.0, 72.0)
        shut = msc.forest_trees(frame, [top, bottom], [], [(30.0, 45.0)])
        self.assertFalse(any(x > 100 for x, _y in shut))
        # ...but where the map has an opening between them, they part the
        # forests smaller than drawn: the forest reaches the other side, and
        # its trees still keep clear of the clearings as drawn.
        bounds = [(80.0, 17.0, 9.0), (80.0, 55.0, 9.0)]
        open_ = msc.forest_trees(frame, [top, bottom], [], [(30.0, 45.0)], bounds=bounds)
        self.assertTrue(any(x > 100 for x, _y in open_))
        self.assertTrue(all(math.hypot(x - cx, y - cy) >= r + msc.TREE_GAP for x, y in open_ for cx, cy, r in (top, bottom)))
        # A path ending at one of them still bounds a forest: it runs on to the
        # clearing's centre, so nothing slips round its end.
        wall = ([(80.0, -10.0), (80.0, 0.0)], 1.0)       # down to the top clearing's circle
        under = ([(80.0, 72.0), (80.0, 90.0)], 1.0)      # up to the bottom one's
        walled = msc.forest_trees(frame, [top, bottom], [wall, under], [(30.0, 45.0)], bounds=bounds)
        self.assertTrue(any(x > 100 for x, _y in walled))                       # (the gap between them is open)
        sealed = msc.forest_trees(frame, [top, bottom], [wall, under, ([(80.0, 34.0), (80.0, 38.0)], 1.0)],
                                  [(30.0, 45.0)], bounds=bounds)
        self.assertFalse(any(x > 100 for x, _y in sealed))                      # (a path between them shuts it)

    def test_every_forest_gets_a_tree(self):
        # A forest squeezed between two paths, with no room for a tree on the
        # grid: one tree where its point is (and none in a lake).
        walls = [([(-10.0, 18.0), (170.0, 18.0)], 1.0), ([(-10.0, 24.0), (170.0, 24.0)], 1.0)]
        trees = msc.forest_trees(self.FRAME, [], walls, [(40.0, 21.0)])
        self.assertEqual(trees, [(40.0, 21.0)])
        lake = [[(30.0, 15.0), (50.0, 15.0), (50.0, 27.0), (30.0, 27.0)]]
        self.assertEqual(msc.forest_trees(self.FRAME, [], walls, [(40.0, 21.0)], water=lake), [])
        # A forest with room gets its trees as before, no extra one.
        roomy = msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0)])
        self.assertNotIn((40.0, 60.0), roomy)

    def test_a_narrow_strip_still_fills_with_trees(self):
        # A strip between two paths, too narrow for the trees' grid to have
        # more than a spot or two: trees between its spots too, along it.
        walls = [([(-10.0, 16.0), (170.0, 16.0)], 1.0), ([(-10.0, 27.0), (170.0, 27.0)], 1.0)]
        trees = msc.forest_trees(self.FRAME, [], walls, [(40.0, 21.5)])
        self.assertGreaterEqual(len(trees), 5)
        self.assertTrue(all(16 < y < 27 for _x, y in trees))
        self.assertGreater(max(x for x, _y in trees) - min(x for x, _y in trees), 80)

    def test_a_forest_fills_the_side_it_is_on_on_the_map(self):
        # A raging river drawn over a forest's point: the map says which side
        # it's on (board_area), so it fills that side, not the one its point
        # happens to be on on the card.
        river = ([(-10.0, 60.0), (170.0, 60.0)], 4.0)
        below = msc.forest_trees(self.FRAME, [], [river], [(50.0, 59.0)],
                                 board_area=lambda p: 1 if p[1] < 60 else 2, forest_areas=[2])
        self.assertTrue(below)
        self.assertTrue(all(y > 60 for _x, y in below))

    def test_an_area_the_card_splits_fills_whole(self):
        # One area on the map; on the card a path happens to part it. Both
        # parts are its, so both fill.
        wall = ([(80.0, -10.0), (80.0, 130.0)], 1.0)
        trees = msc.forest_trees(self.FRAME, [], [wall], [(40.0, 60.0)],
                                 board_area=lambda p: 1, forest_areas=[1])
        self.assertTrue(any(x < 80 for x, _y in trees) and any(x > 80 for x, _y in trees))

    def test_two_forests_in_one_area_fill_it_once(self):
        one = msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0)])
        two = msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0), (120.0, 60.0)])
        self.assertEqual(len(one), len(two))

    def test_evenly_spaced_with_a_little_variation(self):
        trees = msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0)])
        nearest = [min(math.dist(a, b) for b in trees if b is not a) for a in trees]
        self.assertGreaterEqual(min(nearest), msc.TREE_SPACING - 2 * msc.TREE_JITTER - 1e-6)
        self.assertGreater(len(set(round(n, 1) for n in nearest)), 5)        # not a plain grid
        self.assertEqual(trees, msc.forest_trees(self.FRAME, [], [], [(40.0, 60.0)]))   # but always the same


class MapSetupCardEngineTests(TestCase):
    RIVER = {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}

    def _engine(self, layout=None, card=None, **kwargs):
        return msc.MapSetupCardEngine('Highlands', layout or _card_layout(), card or {}, frame=FORGE_FRAME, **kwargs)

    def _diagram(self, layout, raging=False):
        engine = self._engine(layout)
        return msc._Diagram(layout, FORGE_FRAME, (engine.text_w, 150.0), raging)

    def test_pages(self):
        import fitz
        front, back = self._engine().render()
        self.assertIsNone(back)
        self.assertEqual(fitz.open(stream=front, filetype='pdf').page_count, 1)
        self.assertIn('Highlands', fitz.open(stream=front, filetype='pdf')[0].get_text())
        _front, back = self._engine(card={'rules': [{'name': 'Ferry', 'text': 'Sail.'}]}).render()
        self.assertIn('Ferry.', fitz.open(stream=back, filetype='pdf')[0].get_text())
        # Raging River alone makes a back, its stock rule first.
        rivers = _card_layout(paths=[self.RIVER])
        engine = self._engine(rivers, card={'raging_river': True, 'rules': [{'name': 'Ferry', 'text': 'Sail.'}]})
        self.assertEqual([h for h, _t in engine.back_items()], ['Raging River', 'Ferry'])
        text = fitz.open(stream=engine.render()[1], filetype='pdf')[0].get_text()
        self.assertLess(text.index('Raging River'), text.index('Ferry'))

    def test_lines_end_on_the_clearing_circles(self):
        path = {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}}
        diagram = self._diagram(_card_layout(paths=[path]))
        (a, b), ((ax, ay, r), (bx, by, _r)) = diagram.paths[0]['points'][::32], diagram.clearings[:2]
        self.assertAlmostEqual(math.hypot(a[0] - ax, a[1] - ay), r, places=3)
        self.assertAlmostEqual(math.hypot(b[0] - bx, b[1] - by), r, places=3)

    def test_a_lone_river_runs_off_the_frame(self):
        river = {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}
        diagram = self._diagram(_card_layout(paths=[river]))
        ends = [points[-1] for points in diagram.rivers[1:]]      # the run-offs
        self.assertEqual(len(ends), 2)
        for x, y in ends:
            self.assertTrue(x <= 0 or x >= diagram.box_w or y <= 0 or y >= diagram.height)

    def test_springs_follow_the_printed_map(self):
        from the_keep.services.tts_map import MapGeometry, print_rivers
        # Clearing 1 (right) is a lone river end heading right, with the right
        # edge near: it runs off. Clearing 0's end heads left, far less near.
        layout = _card_layout(clearings=[{'centre': [0.45, 0.5], 'number': 1}, {'centre': [0.9, 0.5], 'number': 2}],
                              paths=[{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 1}}], forests=())
        printed = print_rivers(MapGeometry(*FORGE_FRAME), layout)
        self.assertEqual([s['key'] for s in printed['springs']], [(0, 'a')])
        diagram = self._diagram(layout)
        self.assertEqual(len(diagram.rivers), 1 + len(printed['links']))      # the river, and one run-off

    def test_a_river_merges_into_another(self):
        rivers = [self.RIVER, {'kind': 'river', 'a': {'c': 2}, 'b': {'p': 0}}]
        diagram = self._diagram(_card_layout(paths=rivers))
        main = diagram.rivers[0]
        merge = min(diagram.rivers[2:], key=lambda pts: min(math.dist(pts[-1], q) for q in main))
        # It ends on the main river's line.
        self.assertLess(min(msc._seg_distance(merge[-1], a, b) for a, b in zip(main, main[1:])), 0.5)

    def test_a_raging_river_is_twice_as_wide_and_bounds_forests(self):
        across = {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}
        calm, raging = self._diagram(_card_layout(paths=[across])), self._diagram(_card_layout(paths=[across]), True)
        self.assertEqual(raging.river_width, 2 * calm.river_width)
        self.assertGreater(len(calm.trees), len(raging.trees))
        main = raging.rivers[0]
        for p in raging.trees:
            self.assertGreaterEqual(min(msc._seg_distance(p, a, b) for a, b in zip(main, main[1:])),
                                    raging.river_width / 2)

    def test_a_forest_beside_a_raging_river_fills_its_own_side(self):
        # A square of paths, a raging river across it corner to corner, and a
        # forest placed on the map just to one side of the river (on the card,
        # under the river's width): only that side fills.
        square = [{'kind': 'path', 'a': {'c': a}, 'b': {'c': b}} for a, b in ((0, 1), (1, 3), (0, 2), (2, 3))]
        across = {'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}
        for spot, sign in (((0.51, 0.54), -1), ((0.49, 0.56), 1)):
            diagram = self._diagram(_card_layout(paths=square + [across], forests=[spot]), True)
            (ax, ay, _r), (bx, by, _r2) = diagram.clearings[0], diagram.clearings[3]
            sides = {(bx - ax) * (y - ay) - (by - ay) * (x - ax) > 0 for x, y in diagram.trees}
            self.assertEqual(sides, {sign > 0}, spot)
            self.assertGreater(len(diagram.trees), 3)

    def test_a_path_bent_past_the_clearings_stays_on_the_card(self):
        # Clearings 1-2 along the top, a path between them dragged down round
        # 3 (below, between them): the diagram fits it, not just the clearings.
        clearings = [{'centre': [0.2, 0.3], 'marker': [0.2, 0.36], 'number': 1},
                     {'centre': [0.8, 0.3], 'marker': [0.8, 0.36], 'number': 2},
                     {'centre': [0.5, 0.7], 'marker': [0.5, 0.76], 'number': 3}]
        bent = {'kind': 'path', 'a': {'c': 0}, 'b': {'c': 1}, 'side': 1, 'around': [2]}
        diagram = self._diagram(_card_layout(paths=[bent], clearings=clearings))
        ys = [y for s in diagram.paths for _x, y in s['points']]
        xs = [x for s in diagram.paths for x, _y in s['points']]
        self.assertLessEqual(max(ys), diagram.height - msc.PATH_WIDTH / 2)
        self.assertGreater(max(ys), max(y + r for _x, y, r in diagram.clearings))     # (it is below them)
        self.assertGreaterEqual(min(xs), msc.PATH_WIDTH / 2)
        self.assertLessEqual(max(xs), diagram.box_w - msc.PATH_WIDTH / 2)

    def test_the_legend_only_with_special_paths(self):
        self.assertEqual(self._engine()._legend(), [])
        # (A layout and card from before special path types: the first type's name and rule.)
        special = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}}], special_path_name='Bridges')
        engine = self._engine(special, card={'special_path_rule': 'Cross by day.'})
        # The key is just the name beside its colour; what they do is a rule on the back.
        (paragraph, colour), = engine._legend()
        self.assertEqual((paragraph.text, colour), (': Bridges', '#ed2731'))
        self.assertEqual(engine.back_items(), [('Bridges', 'Cross by day.')])
        self.assertTrue(engine.has_back)
        self.assertFalse(self._engine(special).has_back)        # no rule: no back
        unnamed = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}}])
        self.assertIn('Special Paths', self._engine(unnamed)._legend()[0][0].text)

    def test_a_legend_line_and_rule_per_special_path_type(self):
        both = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}},
                                   {'kind': 'special', 'special': 'orange', 'a': {'c': 1}, 'b': {'c': 2}}])
        engine = self._engine(both, card={'special_paths': {'orange': {'name': 'Ropes', 'rule': 'Climb.'}}})
        # In SPECIAL_PATH_TYPES order; an unnamed one by its colour (there are two types).
        self.assertEqual([(p.text, colour) for p, colour in engine._legend()],
                         [(': Red Paths', '#ed2731'), (': Ropes', '#f08c00')])
        self.assertEqual(engine.back_items(), [('Ropes', 'Climb.')])
        self.assertTrue(engine.fits()['front'])

    def test_the_diagram_keeps_the_maps_shape_within_its_stretch(self):
        wide = _card_layout(clearings=[{'centre': [0.1, 0.45], 'number': 1}, {'centre': [0.9, 0.55], 'number': 2}],
                            forests=())
        diagram = self._diagram(wide)
        (ax, ay, _r), (bx, by, _r2) = diagram.clearings
        sx = (bx - ax) / (0.8 * FORGE_FRAME[0])
        sy = (by - ay) / (0.1 * FORGE_FRAME[1])
        self.assertLessEqual(sy / sx, msc.DIAGRAM_MAX_STRETCH + 1e-6)

    def test_special_paths_divide_forests_or_not(self):
        # A forest split only by a special path from clearing 1 (top left) to 4 (bottom right).
        layout = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 3}}], forests=((0.3, 0.7),))
        divided = msc._Diagram(layout, FORGE_FRAME, (self._engine().text_w, 150.0), False)
        joined = msc._Diagram(layout, FORGE_FRAME, (self._engine().text_w, 150.0), False, dividing_specials=set())
        (ax, ay, _r), (bx, by, _r2) = divided.clearings[0], divided.clearings[3]
        side = lambda p: (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)  # noqa: E731
        self.assertEqual(len({side(p) > 0 for p in divided.trees}), 1)     # one side only
        self.assertEqual(len({side(p) > 0 for p in joined.trees}), 2)      # both sides
        # Not dividing them, it's like a river that isn't raging: trees go over
        # it (it's drawn under them), right across its line.
        self.assertTrue(divided.paths[0]['divides'])
        self.assertFalse(joined.paths[0]['divides'])
        line = joined.paths[0]['points']
        self.assertTrue(any(min(msc._seg_distance(p, a, b) for a, b in zip(line, line[1:])) < msc.PATH_WIDTH
                            for p in joined.trees))

    def test_river_banks_vary_a_little(self):
        values = [msc._wobble(s / 3, 'seed') for s in range(300)]
        self.assertTrue(all(-1 <= v <= 1 for v in values))
        self.assertEqual(values, [msc._wobble(s / 3, 'seed') for s in range(300)])     # repeatable
        line = [(0.0, 50.0), (200.0, 50.0)]
        half = 2.0
        outline = msc.river_outline(line, half, 'seed')
        amp = msc.RIVER_WOBBLE * half
        banks = [abs(y - 50.0) for x, y in outline if 0 < x < 200]
        self.assertTrue(all(half - amp - 1e-6 <= b <= half + amp + 1e-6 for b in banks))
        self.assertGreater(max(banks) - min(banks), amp / 2)                          # it does vary

    def test_a_card_always_draws_the_same(self):
        layout = _card_layout(paths=[{'kind': 'river', 'a': {'c': 0}, 'b': {'c': 3}}])
        import fitz
        pixels = lambda: fitz.open(stream=self._engine(layout).render()[0], filetype='pdf')[0].get_pixmap().samples  # noqa: E731
        self.assertEqual(pixels(), pixels())        # (the PDFs differ only in their random document ID)

    def test_a_river_into_a_lake_stops_on_the_card(self):
        # Clearing 1 (top right) is on a lake; the river from 3 (bottom right) ends there.
        river = {'kind': 'river', 'a': {'c': 3}, 'b': {'c': 1}}
        ring = [[0.15, 0.2], [0.85, 0.2], [0.85, 0.4], [0.15, 0.4]]
        with_lake = self._diagram(_card_layout(paths=[{'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}}, river],
                                               forests=(), lake_shapes=[ring]))
        alone = self._diagram(_card_layout(paths=[river], forests=()))
        # Alone, the river runs off at both ends; into the lake, only at the other end.
        self.assertEqual(len(with_lake.rivers), len(alone.rivers) - 1)

    def test_lakes_on_the_card(self):
        lake = {'kind': 'lake', 'a': {'c': 0}, 'b': {'c': 1}}
        ring = [[0.15, 0.2], [0.85, 0.2], [0.85, 0.42], [0.15, 0.42]]
        layout = _card_layout(paths=[lake], forests=((0.5, 0.3), (0.5, 0.6)), lake_shapes=[ring])
        diagram = self._diagram(layout)
        self.assertEqual(diagram.paths, [])                 # never a dashed line
        self.assertEqual(diagram.rivers, [])                # nor a river
        self.assertEqual(len(diagram.lakes), 1)
        self.assertFalse(any(msc._inside(p, diagram.lakes) for p in diagram.trees))   # no tree in the water
        self.assertTrue(diagram.trees)                      # the forest below it still fills
        # Without an outline, the lake is drawn as a river.
        self.assertEqual(len(self._diagram(_card_layout(paths=[lake])).rivers), 1)
        # The outline is part of the card's fingerprint.
        fp = lambda l: msc.card_fingerprint('Map', l, {}, frame=FORGE_FRAME, lang_code='en')  # noqa: E731
        self.assertNotEqual(fp(layout), fp({**layout, 'lake_shapes': [[[0.1, 0.1], [0.9, 0.1], [0.5, 0.5]]]}))
        self.assertTrue(self._engine(layout).render()[0])

    def test_fixed_suits_get_their_icons(self):
        layout = _card_layout(fixed=True)
        for clearing, suit in zip(layout['clearings'], ('fox', 'rabbit', 'city')):
            clearing['suit'] = suit
        diagram = self._diagram(layout)
        self.assertEqual(diagram.suits, ['fox', 'rabbit', 'city', None])
        # Room above the top clearings for their icons.
        top = min(y - r for _x, y, r in diagram.clearings)
        self.assertGreaterEqual(top, msc.SUIT_ICON_H * msc.SUIT_ICON_SCALE['city'] / 2)
        self.assertEqual(self._diagram({**layout, 'fixed': False}).suits, [])
        fp = lambda l: msc.card_fingerprint('Map', l, {}, frame=FORGE_FRAME, lang_code='en')  # noqa: E731
        self.assertNotEqual(fp(layout), fp({**layout, 'fixed': False}))
        self.assertTrue(self._engine(layout).render()[0])   # draws

    def test_setup_modifications_go_on_the_back_unless_it_is_full(self):
        import fitz
        rules = lambda n, words: [{'name': f'R{i}', 'text': 'word ' * words} for i in range(n)]  # noqa: E731
        # On the back by default, before the special paths' rule and the rules.
        special = _card_layout(paths=[{'kind': 'special', 'a': {'c': 0}, 'b': {'c': 1}}])
        short = self._engine(special, card={'modifications': 'Skip ruins.', 'special_path_rule': 'Cross by day.',
                                            'rules': [{'name': 'Ferry', 'text': 'Sail.'}]})
        self.assertTrue(short.modifications_on_back)
        self.assertEqual(short._front_top(), [])
        self.assertEqual([h for h, _t in short.back_items()], ['Setup Modifications', 'Special Paths', 'Ferry'])
        front, back = short.render()
        self.assertNotIn('Setup Modifications', fitz.open(stream=front, filetype='pdf')[0].get_text())
        self.assertIn('Setup Modifications', fitz.open(stream=back, filetype='pdf')[0].get_text())
        self.assertTrue(self._engine(card={'modifications': 'Skip ruins.'}).has_back)   # even alone
        # A back too full for them, with room on the front: on the front.
        full = self._engine(card={'modifications': 'word ' * 5, 'rules': rules(3, 40)})
        self.assertFalse(full.modifications_on_back)
        self.assertEqual(len(full._front_top()), 1)
        self.assertEqual(full.fits(), {'front': True, 'back': True})
        # Room on neither: they stay on the back, and the back is reported.
        neither = self._engine(card={'modifications': 'word ' * 59, 'rules': rules(2, 40)})
        self.assertTrue(neither.modifications_on_back)
        self.assertEqual(neither.fits(), {'front': True, 'back': False})

    def test_fits(self):
        self.assertEqual(self._engine().fits(), {'front': True, 'back': True})
        crowded = {'modifications': 'word ' * 60, 'rules': [{'name': f'R{n}', 'text': 'word ' * 58} for n in range(6)]}
        fits = self._engine(card=crowded).fits()
        self.assertFalse(fits['back'])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='map-card-test-'))
class RefreshSetupCardTests(TestCase):
    """refresh_setup_card works on any map with the card fields: a keep Map here."""
    def test_a_keep_map(self):
        from django.contrib.auth.models import User
        from the_gatehouse.models import Language, Profile
        from the_keep.models import Map
        english, _ = Language.objects.get_or_create(code='en', defaults={'name': 'English'})
        designer = Profile.objects.get(user=User.objects.create_user(username='cartographer', password='pw'))
        keep_map = Map.objects.create(title='Highlands', designer=designer, language=english,
                                      map_card={'rules': [{'name': 'Ferry', 'text': 'Sail.'}]})
        args = {'title': 'Highlands', 'layout': _card_layout(), 'lang_code': 'en', 'frame': (1000, 900)}
        self.assertTrue(msc.refresh_setup_card(keep_map, **args))
        keep_map.refresh_from_db()
        self.assertTrue(keep_map.card_image and keep_map.card_2_image)
        versions = (keep_map.card_image_version, keep_map.card_2_image_version)
        self.assertFalse(msc.refresh_setup_card(keep_map, **args))           # up to date
        # A new title redraws both sides; the same back is the same file.
        keep_map.map_card = {'rules': [{'name': 'Ferry', 'text': 'Sail.'}]}
        self.assertTrue(msc.refresh_setup_card(keep_map, **{**args, 'title': 'Lowlands'}))
        keep_map.refresh_from_db()
        self.assertEqual(keep_map.card_image_version, versions[0] + 1)
        # No rules: the back goes.
        keep_map.map_card = {}
        msc.refresh_setup_card(keep_map, **{**args, 'title': 'Lowlands'})
        keep_map.refresh_from_db()
        self.assertFalse(keep_map.card_2_image)
