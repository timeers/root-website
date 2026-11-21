import json
import colorsys
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from the_gatehouse.models import Profile

from .models import (
    ForgedFaction, FactionSheet, ContentBox, PhaseStep, FactionBack, SetupCard,
    Piece, ForgedDeckGroup, ForgedCardDeck, ForgedCard, CharacterImage,
)
from .services.clone import clone_forged_faction
from .services.clone_flag import clone_in_progress
from . import pdf_engine
from .pdf_cache import fingerprint_back
from .pdf_engine import (
    BACK_BG_SCREEN_OPACITY, BACK_INK_MAX_DARKEN_RATIO, BACK_INK_MIN_CONTRAST,
    _contrast_ratio, _ink_for_wash, _mix_hex,
)


def _rgb01(hex_color):
    """Hex -> (r, g, b) floats, for the hue/saturation assertions below."""
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# Smallest valid PNG (1x1) so ImageField validation passes.
_PNG_1x1 = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08'
    b'\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00'
    b'\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _png(name):
    return SimpleUploadedFile(name, _PNG_1x1, content_type='image/png')


_MEDIA = tempfile.mkdtemp(prefix='forge-clone-test-')


@override_settings(MEDIA_ROOT=_MEDIA)
class CloneForgedFactionTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.designer = Profile.objects.create(discord='designer')
        self.source = ForgedFaction.objects.create(
            designer=self.designer, faction_name='Original',
            published_post=None,
        )
        sheet = FactionSheet.objects.create(
            faction=self.source, snap_points=[{'x': 1, 'y': 2}],
        )
        box = ContentBox.objects.create(sheet=sheet, order=0, title='Box')
        # PhaseStep reachable via BOTH sheet and content_box (the dual-FK node).
        PhaseStep.objects.create(sheet=sheet, content_box=box, phase='birdsong', number=1)
        CharacterImage.objects.create(sheet=sheet, order=0, image=_png('char.png'))

        FactionBack.objects.create(faction=self.source)
        SetupCard.objects.create(faction=self.source)

        # A non-card piece (building) with NO deck_group — exercises the reverse
        # O2O accessor that raises RelatedObjectDoesNotExist when absent.
        Piece.objects.create(
            faction=self.source, type='B', quantity=3, name='Keep',
            small_icon=_png('bldg.png'),
        )

        # Card piece with a quantity intentionally HIGHER than its real card
        # count, and non-zero image versions — the reconcile pass must preserve
        # both against the save()/signal recompute.
        self.piece = Piece.objects.create(
            faction=self.source, type='C', quantity=20,
            small_icon=_png('icon.png'), back_image=_png('back.png'),
        )
        group = ForgedDeckGroup.objects.create(piece=self.piece, name='Deck')
        ForgedCardDeck.objects.create(group=group, deck_index=0)
        self.card_a = ForgedCard.objects.create(group=group, name='A', front_image=_png('a.png'), order=7, tags=['x'])
        self.card_b = ForgedCard.objects.create(group=group, name='B', front_image=_png('b.png'), order=9, tags=['y'])
        # Several save()/signals recompute values on create: ForgedCard.save
        # reassigns order to max+1, and _bubble_forged_card forces the piece
        # quantity to the live card count. Force divergent stored values via
        # .update() (bypassing those) so the clone's reconcile pass is actually
        # exercised — the copy must reproduce the SOURCE's stored values, not the
        # recomputed ones.
        Piece.objects.filter(pk=self.piece.pk).update(quantity=20, front_version=3, back_version=5)
        ForgedCard.objects.filter(pk=self.card_a.pk).update(order=7)
        ForgedCard.objects.filter(pk=self.card_b.pk).update(order=9)
        self.piece.refresh_from_db()

    def _model_counts(self):
        return {
            M: M.objects.count() for M in (
                ForgedFaction, FactionSheet, ContentBox, PhaseStep, CharacterImage,
                FactionBack, SetupCard, Piece, ForgedDeckGroup, ForgedCardDeck, ForgedCard,
            )
        }

    def test_row_counts_double(self):
        before = self._model_counts()
        clone_forged_faction(self.source)
        after = self._model_counts()
        for M, count in before.items():
            self.assertEqual(after[M], count * 2, f'{M.__name__} not duplicated')

    def test_copy_is_independent_faction(self):
        copy = clone_forged_faction(self.source)
        self.assertNotEqual(copy.pk, self.source.pk)
        self.assertEqual(copy.faction_name, 'Original (Copy)')
        self.assertTrue(copy.slug)
        self.assertNotEqual(copy.slug, self.source.slug)
        self.assertEqual(copy.designer_id, self.source.designer_id)
        self.assertIsNone(copy.published_post_id)
        self.assertIsNone(copy.published_translation_id)

    def test_images_are_copied_not_shared(self):
        copy = clone_forged_faction(self.source)
        src_icon = self.source.pieces.get(type='C').small_icon.name
        copy_icon = copy.pieces.get(type='C').small_icon.name
        self.assertTrue(src_icon and copy_icon)
        self.assertNotEqual(src_icon, copy_icon)
        # Source file untouched; copy lives under the copy's slug folder.
        self.assertTrue(self.source.pieces.get(type='C').small_icon.storage.exists(src_icon))
        self.assertIn(copy.slug, copy_icon)

    def test_json_not_shared(self):
        copy = clone_forged_faction(self.source)
        copy_sheet = copy.faction_sheet
        copy_sheet.snap_points.append({'x': 99, 'y': 99})
        copy_sheet.save()
        self.source.faction_sheet.refresh_from_db()
        self.assertEqual(len(self.source.faction_sheet.snap_points), 1)

    def test_scalars_reconciled(self):
        copy = clone_forged_faction(self.source)
        copy_piece = copy.pieces.get(type='C')
        # quantity preserved (not shrunk to the 2-card live count by the signal)
        self.assertEqual(copy_piece.quantity, 20)
        self.assertEqual(copy_piece.front_version, 3)
        self.assertEqual(copy_piece.back_version, 5)
        # card order preserved (not reassigned to max+1 by ForgedCard.save)
        orders = sorted(copy_piece.deck_group.cards.values_list('order', flat=True))
        self.assertEqual(orders, [7, 9])

    def test_dual_fk_node_points_at_copy(self):
        copy = clone_forged_faction(self.source)
        step = copy.faction_sheet.phase_steps.get()
        self.assertEqual(step.sheet_id, copy.faction_sheet.pk)
        self.assertIsNotNone(step.content_box_id)
        # content_box must be the COPY's box, not the original's.
        self.assertEqual(step.content_box.sheet_id, copy.faction_sheet.pk)

    def test_discord_not_sent_during_clone(self):
        with mock.patch('the_gatehouse.tasks.send_rich_discord_message_task.delay') as delay:
            clone_forged_faction(self.source)
        delay.assert_not_called()

    def test_clone_flag_resets(self):
        self.assertFalse(clone_in_progress())
        clone_forged_faction(self.source)
        self.assertFalse(clone_in_progress())

    def test_clone_flag_resets_on_error(self):
        with mock.patch('the_forge.services.clone._walk_subtree', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                clone_forged_faction(self.source)
        self.assertFalse(clone_in_progress())


class AdaptiveInkTests(TestCase):
    """The FactionBack draws its content straight onto a background that is the
    faction color lightened by a 70% white screen -- there are no opaque panels.
    A pale faction therefore paints near-invisible bars and shapes, so the ink is
    darkened just enough to stay visible. See _ink_for_wash in pdf_engine."""

    WASH = BACK_BG_SCREEN_OPACITY
    TARGET = BACK_INK_MIN_CONTRAST

    def setUp(self):
        user = User.objects.create_user(username='inker', password='pw')
        self.profile = user.profile

    def _ink(self, color):
        return _ink_for_wash(color, self.WASH, self.TARGET)

    def _ground(self, color):
        return _mix_hex('#FFFFFF', self.WASH, color)

    # ── _mix_hex ──
    def test_mixing_is_a_plain_composite(self):
        self.assertEqual(_mix_hex('#000000', 0.5, '#FFFFFF'), '#808080')
        self.assertEqual(_mix_hex('#FFFFFF', 0.0, '#123456'), '#123456')
        self.assertEqual(_mix_hex('#FFFFFF', 1.0, '#123456'), '#FFFFFF')

    def test_mixing_accepts_shorthand_hex(self):
        """_relative_luminance expands 3-digit hex, and a faction color is
        free text from the designer, so this must not raise."""
        self.assertEqual(_mix_hex('#FFF', 0.0, '#abc'), '#AABBCC')

    def test_mixing_returns_none_on_junk_rather_than_raising(self):
        """Matches _relative_luminance's contract: a bad color must not take
        down a render."""
        self.assertIsNone(_mix_hex('#FFFFFF', 0.5, 'not-a-color'))

    # ── the guarantee that matters most ──
    def test_a_legible_color_is_returned_completely_unchanged(self):
        """Most factions must render bit-identically, so the SAME string comes
        back -- not an equivalent re-rendering of it."""
        for color in ('#4667b3', '#c22424', '#ff0000', '#1402d9', '#000000'):
            with self.subTest(color=color):
                self.assertIs(self._ink(color), color)

    def test_a_color_just_above_the_target_is_untouched(self):
        """#c3d3c0 sits at 1.38 against its own washed ground, just above the
        1.3 target. It is the case that pins the threshold: this page was judged
        to already look fine, and the target was chosen so it stays that way."""
        self.assertGreater(_contrast_ratio('#c3d3c0', self._ground('#c3d3c0')),
                           self.TARGET)
        self.assertIs(self._ink('#c3d3c0'), '#c3d3c0')

    # ── darkening ──
    def test_a_washed_out_color_is_darkened_until_it_clears_the_target(self):
        """#eaebeb is a near-white grey at 1.13 -- effectively invisible."""
        ink = self._ink('#eaebeb')
        self.assertNotEqual(ink, '#eaebeb')
        self.assertGreaterEqual(
            _contrast_ratio(ink, self._ground('#eaebeb')), self.TARGET)

    def test_darkening_is_minimal_not_merely_sufficient(self):
        """Backing the result off must drop it below the target, proving the
        search returns the SMALLEST qualifying darkening rather than any one."""
        color = '#eaebeb'
        ground = self._ground(color)
        ink = self._ink(color)
        # Recover k, then confirm a slightly smaller one fails.
        k = 1 - (int(ink.lstrip('#')[0:2], 16) / int(color.lstrip('#')[0:2], 16))
        weaker = _mix_hex('#000000', max(k - 0.02, 0.0), color)
        self.assertLess(_contrast_ratio(weaker, ground), self.TARGET)

    def test_darkening_preserves_hue_and_saturation(self):
        """Mixing toward black is multiplicative, so a pale faction still reads
        as its own color -- just deeper -- rather than being recolored."""
        color = '#c9e265'
        ink = self._ink(color)
        self.assertNotEqual(ink, color)
        h1, s1, _ = colorsys.rgb_to_hsv(*_rgb01(color))
        h2, s2, _ = colorsys.rgb_to_hsv(*_rgb01(ink))
        self.assertAlmostEqual(h1, h2, places=2)
        self.assertAlmostEqual(s1, s2, places=2)

    def test_it_never_darkens_past_the_cap(self):
        for color in ('#FFFFFF', '#eaebeb', '#fffedd', '#c9e265'):
            with self.subTest(color=color):
                ink = self._ink(color)
                floor = _mix_hex('#000000', BACK_INK_MAX_DARKEN_RATIO, color)
                # Never darker than the cap allows, on every channel.
                for i in (0, 2, 4):
                    self.assertGreaterEqual(int(ink.lstrip('#')[i:i + 2], 16),
                                            int(floor.lstrip('#')[i:i + 2], 16))

    def test_every_color_that_needs_help_actually_reaches_the_target(self):
        """Swept coarsely across the cube: nothing may fall short, or a faction
        would silently keep an invisible bar."""
        for r in range(0, 256, 51):
            for g in range(0, 256, 51):
                for b in range(0, 256, 51):
                    color = '#%02X%02X%02X' % (r, g, b)
                    ink = self._ink(color)
                    self.assertGreaterEqual(
                        _contrast_ratio(ink, self._ground(color)),
                        self.TARGET - 0.01, msg=color)

    # ── the switch ──
    def test_the_flag_disables_darkening_entirely(self):
        with mock.patch.object(pdf_engine, 'BACK_INK_DARKEN_ENABLED', False):
            self.assertIs(self._ink('#eaebeb'), '#eaebeb')

    def test_the_flag_changes_the_cache_fingerprint(self):
        """Both the PDF cache and the stored WebP preview key off
        fingerprint_back. Without the flag in the payload, toggling it would
        serve the other mode's output and the switch would look broken."""
        faction = ForgedFaction.objects.create(
            faction_name='Ink Flag', color='#eaebeb', designer=self.profile)
        back = FactionBack.objects.create(faction=faction)
        on = fingerprint_back(back)
        with mock.patch.object(pdf_engine, 'BACK_INK_DARKEN_ENABLED', False):
            off = fingerprint_back(back)
        self.assertNotEqual(on, off)



# ── Tabletop Simulator loader / forge -> keep conversion ────────────────────

from django.urls import reverse
from django.utils import timezone

from the_gatehouse.models import Language, UserNotification
from the_keep.models import (
    Card as KeepCard, CardDeck as KeepCardDeck, DeckGroup as KeepDeckGroup,
    Faction as KeepFaction, Piece as KeepPiece,
)
from .forms_publish import (
    ForgedFactionSyncForm, apply_deck_entry, build_deck_plan, build_piece_plan,
    forge_tts_fingerprint,
)
from .services.tts_convert import convert_save, find_forge_urls

_TTS_MEDIA = tempfile.mkdtemp(prefix='forge-tts-test-')


def _save_with(*objects):
    return {'SaveName': 'x', 'ObjectStates': list(objects)}


class TTSConvertTests(TestCase):
    def test_convert_rewrites_only_mapped_strings(self):
        save = _save_with({
            'Name': 'Custom_Tile', 'Nickname': 'Board',
            'CustomImage': {'ImageURL': '{verifycache}https://s/media/forge/factions/f/board/front.webp'},
            'Description': 'keep me',
        })
        url_map = {'{verifycache}https://s/media/forge/factions/f/board/front.webp':
                   '{verifycache}https://s/media/uploads/posts/f/board.webp'}
        converted = convert_save(save, url_map)
        self.assertEqual(converted['ObjectStates'][0]['CustomImage']['ImageURL'],
                         '{verifycache}https://s/media/uploads/posts/f/board.webp')
        self.assertEqual(converted['ObjectStates'][0]['Description'], 'keep me')
        # The input is untouched (deep copy).
        self.assertIn('/media/forge/', save['ObjectStates'][0]['CustomImage']['ImageURL'])
        self.assertEqual(find_forge_urls(converted), [])

    def test_find_forge_urls_names_leftovers(self):
        save = _save_with(
            {'Name': 'Deck', 'Nickname': 'Ambush', 'CustomDeck': {'1': {'FaceURL': 'https://s/media/forge/x/sheet_0.webp'}}},
            {'Name': 'Custom_Tile', 'Nickname': 'Board', 'CustomImage': {'ImageURL': 'https://s/media/uploads/b.webp'}},
        )
        self.assertEqual(find_forge_urls(save), ['Ambush'])


@override_settings(MEDIA_ROOT=_TTS_MEDIA)
class ForgeTTSSyncTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TTS_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        Language.objects.create(code='en', name='English')
        self.user = User.objects.create_user(username='forgetts', password='pw')
        self.designer = Profile.objects.get(user=self.user)
        self.keep = KeepFaction.objects.create(title='Keep Fox', animal='Fox', designer=self.designer,
                                               component='Faction')
        self.forged = ForgedFaction.objects.create(designer=self.designer, faction_name='Keep Fox',
                                                   published_post=self.keep)
        FactionBack.objects.create(faction=self.forged)
        self.forge_piece = Piece.objects.create(faction=self.forged, type='C', quantity=2, name='Ambush')
        self.group = ForgedDeckGroup.objects.create(piece=self.forge_piece, name='Ambush')
        self.card_a = ForgedCard.objects.create(group=self.group, name='A', front_image=_png('a.png'), tags=['x'])
        self.card_b = ForgedCard.objects.create(group=self.group, name='B', front_image=_png('b.png'))
        self.keep_piece = KeepPiece.objects.create(parent=self.keep, name='Ambush', type='C', quantity=2)

    def _deck_entry(self):
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        return build_deck_plan(forged, build_piece_plan(forged))[0]

    def test_deck_sync_round_trip_updates_cards_in_place(self):
        entry = self._deck_entry()
        self.assertEqual(entry['action'], 'create')
        group = apply_deck_entry(entry, self.keep, self.keep_piece)
        self.assertEqual(group.language.code, 'en')
        self.assertEqual([c.name for c in group.cards.order_by('order')], ['A', 'B'])
        self.assertTrue(self._deck_entry()['in_sync'])

        card_pks = list(group.cards.order_by('order').values_list('pk', flat=True))
        ForgedCard.objects.filter(pk=self.card_a.pk).update(tags=['x', 'y'])  # tags-only edit
        entry = self._deck_entry()
        self.assertFalse(entry['in_sync'])
        apply_deck_entry(entry, self.keep, self.keep_piece)
        self.assertEqual(list(group.cards.order_by('order').values_list('pk', flat=True)), card_pks)
        self.assertEqual(KeepCard.objects.get(pk=card_pks[0]).tags, ['x', 'y'])

    def test_deck_shrink_drops_surplus_cards_and_card_decks(self):
        group = apply_deck_entry(self._deck_entry(), self.keep, self.keep_piece)
        KeepCardDeck.objects.create(group=group, deck_index=3)  # stale, past the end
        self.card_b.delete()
        apply_deck_entry(self._deck_entry(), self.keep, self.keep_piece)
        self.assertEqual(group.cards.count(), 1)
        self.assertEqual(list(group.decks.values_list('deck_index', flat=True)), [0])

    def test_fingerprint_catches_removals_and_color(self):
        base = forge_tts_fingerprint(ForgedFaction.objects.get(pk=self.forged.pk))
        ForgedFaction.objects.filter(pk=self.forged.pk).update(color='#123456')
        recolored = forge_tts_fingerprint(ForgedFaction.objects.get(pk=self.forged.pk))
        self.assertNotEqual(base, recolored)
        Piece.objects.create(faction=self.forged, type='B', quantity=1, name='Roost')
        added = forge_tts_fingerprint(ForgedFaction.objects.get(pk=self.forged.pk))
        Piece.objects.filter(faction=self.forged, name='Roost').delete()
        self.assertNotEqual(added, forge_tts_fingerprint(ForgedFaction.objects.get(pk=self.forged.pk)))

    def test_tts_row_forces_dependent_rows(self):
        Piece.objects.create(faction=self.forged, type='B', quantity=3, name='Roost')
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        roost = forged.pieces.get(name='Roost')
        form = ForgedFactionSyncForm({'sync_tts_json': 'on'}, forged=forged)
        self.assertIn(f'piece_{roost.pk}', form.tts_forced_field_names)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data[f'piece_{roost.pk}'])
        self.assertTrue(form.cleaned_data[f'deck_{self.forge_piece.pk}'])

    def test_deck_row_pulls_in_its_new_piece(self):
        self.keep_piece.delete()
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        form = ForgedFactionSyncForm({f'deck_{self.forge_piece.pk}': 'on'}, forged=forged)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data[f'piece_{self.forge_piece.pk}'])
        form.save()
        new_piece = KeepPiece.objects.get(parent=self.keep, name='Ambush')
        self.assertTrue(KeepDeckGroup.objects.filter(post=self.keep, piece=new_piece).exists())

    def test_deck_only_change_counts_as_sync_available(self):
        # Fields and pieces in sync; only the deck differs. The detail page's
        # "Updates Available" must still light up.
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        KeepFaction.objects.filter(pk=self.keep.pk).update(color=forged.color)
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        form = ForgedFactionSyncForm(forged=forged)
        self.assertEqual([row[0] for row in form.diff_rows if not row[5]], [])
        self.assertTrue(all(e['in_sync'] for e in form.piece_plan))
        self.assertFalse(form.deck_plan[0]['in_sync'])
        self.assertTrue(form.has_changes)

    def test_clone_starts_unpublished(self):
        ForgedFaction.objects.filter(pk=self.forged.pk).update(
            tts_status=ForgedFaction.TTSStatus.APPROVED, tts_json=_save_with({'Name': 'x'}),
            tts_submitted_at=timezone.now(), tts_rejection_reason='old',
            tts_synced_hash='abc', tts_source_fingerprint='def',
        )
        copy = clone_forged_faction(ForgedFaction.objects.get(pk=self.forged.pk))
        self.assertEqual(copy.tts_status, ForgedFaction.TTSStatus.NOT_SUBMITTED)
        self.assertIsNone(copy.tts_submitted_at)
        self.assertEqual(copy.tts_rejection_reason, '')
        self.assertIsNone(copy.tts_json)
        self.assertEqual((copy.tts_synced_hash, copy.tts_source_fingerprint), ('', ''))


@override_settings(MEDIA_ROOT=_TTS_MEDIA, SITE_URL='https://example.test')
class ForgeTTSConversionEndToEndTests(TestCase):
    """Real builders, no PDF rendering: a back preview stands in for the renders."""

    def setUp(self):
        Language.objects.create(code='en', name='English')
        self.designer = Profile.objects.create(discord='e2edesigner')
        self.keep = KeepFaction.objects.create(title='E2E Fox', animal='Fox', designer=self.designer,
                                               component='Faction')
        self.forged = ForgedFaction.objects.create(
            designer=self.designer, faction_name='E2E Fox', published_post=self.keep,
            vp_marker=_png('vp.png'),
        )
        back = FactionBack.objects.create(faction=self.forged)
        back.image_preview.save('back.png', _png('back.png'))
        FactionBack.objects.filter(pk=back.pk).update(preview_version=1)
        piece = Piece.objects.create(faction=self.forged, type='C', quantity=2, name='Ambush',
                                     small_icon=_png('icon.png'))
        group = ForgedDeckGroup.objects.create(piece=piece, name='Ambush', back_image=_png('dback.png'))
        ForgedCard.objects.create(group=group, name='A', front_image=_png('a.png'))
        ForgedCard.objects.create(group=group, name='B', front_image=_png('b.png'))

    def test_sync_writes_keep_only_json_and_reports_in_sync(self):
        from .views import _sync_keep_tts_json

        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        form = ForgedFactionSyncForm({'sync_tts_json': 'on'}, forged=forged)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertIsNone(_sync_keep_tts_json(self.forged.pk))

        stored = KeepFaction.objects.get(pk=self.keep.pk).tts_json
        dumped = json.dumps(stored)
        self.assertNotIn('/media/forge/', dumped)
        self.assertIn('https://example.test/media/uploads/', dumped)
        nicknames = [o.get('Nickname') for o in stored['ObjectStates']]
        self.assertIn('Ambush', nicknames)
        # The Root Box Score seats a faction through "<name> - ..." and builds its
        # row from "<name> VP"; both must share the faction's name.
        self.assertIn('E2E Fox - Faction Board', nicknames)
        self.assertIn('E2E Fox VP', nicknames)

        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        fresh = ForgedFactionSyncForm(forged=forged)
        self.assertTrue(fresh.tts_plan['in_sync'], fresh.tts_plan)

        # A removal on the forge side makes the TTS row stale again.
        ForgedFaction.objects.filter(pk=self.forged.pk).update(vp_marker='')
        stale = ForgedFactionSyncForm(forged=ForgedFaction.objects.get(pk=self.forged.pk))
        self.assertFalse(stale.tts_plan['in_sync'])


@override_settings(MEDIA_ROOT=_TTS_MEDIA)
class TTSLibrarySubmissionTests(TestCase):
    """Designer submits to the TTS Library; an admin approves or rejects."""

    def setUp(self):
        # force_login fires user_logged_in, whose handler needs a real request
        # (same workaround as the_gatehouse's _NoLoginSignalMixin).
        from django.contrib.auth.signals import user_logged_in
        from the_gatehouse.signals import user_logged_in_handler
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)

        self.user = User.objects.create_user(username='ttsdesigner', password='pw')
        self.designer = Profile.objects.get(user=self.user)
        self.admin_user = User.objects.create_user(username='ttsadmin', password='pw')
        Profile.objects.filter(user=self.admin_user).update(group='A')
        self.other_user = User.objects.create_user(username='ttsother', password='pw')
        self.forged = ForgedFaction.objects.create(designer=self.designer, faction_name='Library Fox')
        FactionBack.objects.create(faction=self.forged)

        fake_save = (_save_with({'Name': 'Custom_Tile'}), None, (None, None, None))
        for target, kwargs in (
            ('the_forge.views._build_forged_tts_save', {'return_value': fake_save}),
            ('the_forge.views.send_rich_discord_message_task.delay', {}),
            ('the_forge.views.send_discord_message_task.delay', {}),
        ):
            patcher = mock.patch(target, **kwargs)
            setattr(self, target.rsplit('.', 2)[-2] if target.endswith('.delay') else 'build', patcher.start())
            self.addCleanup(patcher.stop)

    def _status(self):
        return ForgedFaction.objects.get(pk=self.forged.pk).tts_status

    def _set(self, **fields):
        ForgedFaction.objects.filter(pk=self.forged.pk).update(**fields)

    def test_only_the_designer_can_submit(self):
        url = reverse('forge-faction-tts-submit', args=[self.forged.pk])
        self.client.force_login(self.other_user)
        self.client.post(url)
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.NOT_SUBMITTED)

        self.client.force_login(self.user)
        self.client.post(url)
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        self.assertEqual(forged.tts_status, ForgedFaction.TTSStatus.SUBMITTED)
        self.assertIsNotNone(forged.tts_submitted_at)
        self.assertEqual(forged.tts_json['ObjectStates'][0]['Name'], 'Custom_Tile')
        self.assertEqual(self.send_rich_discord_message_task.call_count, 1)
        self.assertIn('forge/tts-library/pending/', self.send_rich_discord_message_task.call_args.kwargs['url'])

    def test_resubmit_after_rejection_clears_the_reason(self):
        self._set(tts_status=ForgedFaction.TTSStatus.REJECTED, tts_rejection_reason='Fix the board')
        self.client.force_login(self.user)
        self.client.post(reverse('forge-faction-tts-submit', args=[self.forged.pk]))
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        self.assertEqual(forged.tts_status, ForgedFaction.TTSStatus.SUBMITTED)
        self.assertEqual(forged.tts_rejection_reason, '')

    def test_withdraw_and_remove(self):
        self.client.force_login(self.user)
        url = reverse('forge-faction-tts-withdraw', args=[self.forged.pk])
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED)
        self.client.post(url)
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.NOT_SUBMITTED)
        self.send_discord_message_task.assert_not_called()

        self._set(tts_status=ForgedFaction.TTSStatus.APPROVED)
        self.client.post(url)
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.NOT_SUBMITTED)
        self.send_discord_message_task.assert_called_once()

    def test_review_is_admin_only(self):
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED, tts_json=_save_with({'Name': 'x'}))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('forge-tts-library-pending')).status_code, 403)
        self.assertEqual(self.client.post(reverse('forge-tts-library-approve', args=[self.forged.pk])).status_code, 403)
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.SUBMITTED)

    def test_approve_notifies_the_designer(self):
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED, tts_json=_save_with({'Name': 'x'}))
        self.client.force_login(self.admin_user)
        self.client.post(reverse('forge-tts-library-approve', args=[self.forged.pk]))
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.APPROVED)
        note = UserNotification.objects.get(profile=self.designer)
        self.assertIn('approved', note.message)

    def test_reject_stores_feedback_and_notifies(self):
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED, tts_json=_save_with({'Name': 'x'}))
        self.client.force_login(self.admin_user)
        self.client.post(reverse('forge-tts-library-reject', args=[self.forged.pk]), {'message': 'Fix the board'})
        forged = ForgedFaction.objects.get(pk=self.forged.pk)
        self.assertEqual(forged.tts_status, ForgedFaction.TTSStatus.REJECTED)
        self.assertEqual(forged.tts_rejection_reason, 'Fix the board')
        self.assertIn('Fix the board', UserNotification.objects.get(profile=self.designer).message)

    def test_review_of_withdrawn_submission_is_a_no_op(self):
        # Designer withdrew before the admin clicked Approve.
        self._set(tts_json=_save_with({'Name': 'x'}))
        self.client.force_login(self.admin_user)
        self.client.post(reverse('forge-tts-library-approve', args=[self.forged.pk]))
        self.assertEqual(self._status(), ForgedFaction.TTSStatus.NOT_SUBMITTED)
        self.assertFalse(UserNotification.objects.filter(profile=self.designer).exists())

    def test_pending_page_lists_only_submitted(self):
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED, tts_submitted_at=timezone.now())
        ForgedFaction.objects.create(designer=self.designer, faction_name='Approved Fox',
                                     tts_status=ForgedFaction.TTSStatus.APPROVED)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('forge-tts-library-pending'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([f.pk for f in response.context['pending_factions']], [self.forged.pk])

    def test_detail_card_message_follows_status(self):
        # The detail page sits behind the forge onboarding gate.
        Profile.objects.filter(pk=self.designer.pk).update(
            group='D', player_onboard=True, forge_onboard=True)
        self.client.force_login(self.user)
        url = reverse('forge-faction-detail', args=[self.forged.pk])
        cases = [
            ({}, 'Add to the TTS Library'),
            ({'tts_status': ForgedFaction.TTSStatus.SUBMITTED, 'tts_submitted_at': timezone.now()}, 'Awaiting Approval'),
            ({'tts_status': ForgedFaction.TTSStatus.APPROVED}, 'In the TTS Library'),
            ({'tts_status': ForgedFaction.TTSStatus.REJECTED, 'tts_rejection_reason': 'Needs a decree'}, 'Needs a decree'),
        ]
        for fields, expected in cases:
            with self.subTest(expected=expected):
                if fields:
                    self._set(**fields)
                content = self.client.get(url).content.decode()
                self.assertIn(expected, content)
                # The publish control no longer lives in the Download dropdown.
                self.assertNotIn('Publish to TTS Library', content)

    def test_admin_sees_review_panel_on_submitted_faction(self):
        Profile.objects.filter(user=self.admin_user).update(player_onboard=True, forge_onboard=True)
        self.client.force_login(self.admin_user)
        url = reverse('forge-faction-detail', args=[self.forged.pk])
        self.assertNotIn('TTS Library Review', self.client.get(url).content.decode())
        self._set(tts_status=ForgedFaction.TTSStatus.SUBMITTED, tts_submitted_at=timezone.now())
        content = self.client.get(url).content.decode()
        self.assertIn('TTS Library Review', content)
        self.assertIn(reverse('forge-tts-library-approve', args=[self.forged.pk]), content)
        # The designer's own submit card is for editors only.
        self.assertNotIn('Awaiting Approval', content)


# ── Forge post types (component), Clockwork, and the published_post link ─────

from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command

from the_keep.models import Map as KeepMap
from .forms_publish import ForgedFactionLinkForm, ForgedFactionSubmitForm, submit_prerequisites_missing
from .views import _link_candidate_exists


@override_settings(MEDIA_ROOT=_TTS_MEDIA)
class ForgeComponentTests(TestCase):
    def setUp(self):
        from django.contrib.auth.signals import user_logged_in
        from the_gatehouse.signals import user_logged_in_handler
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)

        Language.objects.create(code='en', name='English')
        self.user = User.objects.create_user(username='typedesigner', password='pw')
        self.designer = Profile.objects.get(user=self.user)
        Profile.objects.filter(pk=self.designer.pk).update(group='D', player_onboard=True, forge_onboard=True)
        self.designer.refresh_from_db()

    def _draft(self, component='Faction', **kwargs):
        return ForgedFaction.objects.create(designer=self.designer, faction_name=kwargs.pop('name', 'Draft'),
                                            component=component, **kwargs)

    def test_type_flags(self):
        self.assertEqual(self._draft(name='Plain').component, 'Faction')
        for component, is_faction, setup in (('Faction', True, True), ('Clockwork', True, False),
                                             ('Map', False, False), ('Deck', False, False)):
            with self.subTest(component=component):
                draft = ForgedFaction(component=component)
                self.assertEqual((draft.is_faction, draft.uses_setup_card), (is_faction, setup))

    def test_any_type_holds_pieces_decks_and_cards_and_clones_them(self):
        draft = self._draft('Deck', name='Deck Draft')
        piece = Piece.objects.create(faction=draft, type='C', quantity=1, name='Cards')
        group = ForgedDeckGroup.objects.create(piece=piece, name='Cards')
        ForgedCard.objects.create(group=group, name='A', front_image=_png('a.png'))
        copy = clone_forged_faction(draft)
        self.assertEqual(copy.component, 'Deck')
        self.assertEqual(ForgedCard.objects.filter(group__piece__faction=copy).count(), 1)

    def test_non_faction_drafts_are_kept_out_of_faction_flows(self):
        # A Deck draft: Map drafts have their own pages (ForgedMapTests).
        draft = self._draft('Deck', name='Deck Draft')
        self.client.force_login(self.user)
        detail = reverse('forge-faction-detail', args=[draft.pk])
        for name in ('forge-faction-submit', 'forge-faction-sync'):
            self.assertRedirects(self.client.get(reverse(name, args=[draft.pk])), detail, fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('forge-sheet-create', args=[draft.pk])), detail,
                             fetch_redirect_response=False)
        content = self.client.get(detail).content.decode()
        self.assertNotIn('Submit as New', content)
        self.assertNotIn('Add to the TTS Library', content)

    def test_chooser_and_typed_create(self):
        self.client.force_login(self.user)
        content = self.client.get(reverse('forge-new')).content.decode()
        self.assertIn(reverse('forge-faction-create'), content)
        self.assertIn(reverse('forge-clockwork-create'), content)
        self.assertIn('New Forged Clockwork Faction',
                      self.client.get(reverse('forge-clockwork-create')).content.decode())
        for url, expected in ((reverse('forge-clockwork-create'), 'Clockwork'),
                              (reverse('forge-faction-create'), 'Faction')):
            with self.subTest(expected=expected):
                self.client.post(url, {'faction_name': f'{expected} Made', 'language': Language.objects.get().pk,
                                       'color': '#5f788a', 'secondary_color': '#000000',
                                       'background_tile_size': 33})
                self.assertEqual(ForgedFaction.objects.get(faction_name=f'{expected} Made').component, expected)

    def test_clockwork_has_no_adset_card(self):
        draft = self._draft('Clockwork', name='Gears')
        FactionSheet.objects.create(faction=draft)
        FactionBack.objects.create(faction=draft)
        self.client.force_login(self.user)
        detail = reverse('forge-faction-detail', args=[draft.pk])
        self.assertRedirects(self.client.post(reverse('forge-setup-card-create', args=[draft.pk])), detail,
                             fetch_redirect_response=False)
        self.assertFalse(SetupCard.objects.filter(faction=draft).exists())
        self.assertNotIn('Setup Card', submit_prerequisites_missing(draft))
        content = self.client.get(detail).content.decode()
        self.assertNotIn('Adset Card</h4>', content)
        self.assertNotIn('Adset Card', [r['label'] for r in self.client.get(detail).context['submit_requirements']])

    def test_clockwork_submit_form_publishes_type_c(self):
        draft = self._draft('Clockwork', name='Gears')
        FactionSheet.objects.create(faction=draft)
        FactionBack.objects.create(faction=draft)
        form = ForgedFactionSubmitForm(forged_faction=draft, user=self.user)
        self.assertNotIn('type', form.fields)
        self.assertNotIn('reach', form.fields)
        self.assertEqual((form.instance.type, form.instance.reach), ('C', 0))
        # The keep turns type 'C' into a Clockwork on save.
        form.instance.title, form.instance.designer, form.instance.animal = 'Gears', self.designer, 'Fox'
        form.instance.save()
        self.assertEqual(form.instance.component, 'Clockwork')

    def test_link_candidates_match_the_draft_type(self):
        KeepFaction.objects.create(title='Gears', animal='Fox', designer=self.designer, type='M', reach=6)
        clockwork_draft = self._draft('Clockwork', name='Gears')
        self.assertFalse(_link_candidate_exists(self.designer, clockwork_draft))
        keep_clockwork = KeepFaction.objects.create(title='Cogs', animal='Fox', designer=self.designer, type='C')
        ForgedFaction.objects.filter(pk=clockwork_draft.pk).update(faction_name='Cogs')
        clockwork_draft.refresh_from_db()
        self.assertTrue(_link_candidate_exists(self.designer, clockwork_draft))
        form = ForgedFactionLinkForm(user=self.user, forged=clockwork_draft)
        values = [value for value, _label in form.fields['target'].choices]
        self.assertEqual(values, [f'faction:{keep_clockwork.pk}'])
        faction = KeepFaction.objects.get(title='Gears')
        bad = ForgedFactionLinkForm({'target': f'faction:{faction.pk}'}, user=self.user, forged=clockwork_draft)
        self.assertFalse(bad.is_valid())

    def test_linked_faction_is_no_longer_a_candidate(self):
        keep = KeepFaction.objects.create(title='Linked', animal='Fox', designer=self.designer, type='M', reach=6)
        self._draft(name='Other', published_post=keep)
        self.assertFalse(KeepFaction.objects.filter(pk=keep.pk, source_forged_post__isnull=True).exists())
        self.assertFalse(_link_candidate_exists(self.designer, self._draft(name='Linked')))

    def test_published_keep_post_returns_the_concrete_type(self):
        keep_faction = KeepFaction.objects.create(title='Fox Folk', animal='Fox', designer=self.designer, type='M', reach=6)
        keep_map = KeepMap.objects.create(title='Fall Map', designer=self.designer)
        faction_draft = self._draft(name='Fox Folk', published_post=keep_faction)
        self.assertIsInstance(faction_draft.published_keep_post, KeepFaction)
        map_draft = self._draft('Map', name='Fall Map', published_post=keep_map)
        self.assertIsInstance(map_draft.published_keep_post, KeepMap)
        # A mismatched component falls back to the base Post instead of None.
        other_map = KeepMap.objects.create(title='Winter Map', designer=self.designer)
        mismatched = self._draft('Vagabond', name='Odd')
        ForgedFaction.objects.filter(pk=mismatched.pk).update(published_post=other_map)
        mismatched.refresh_from_db()
        self.assertEqual(mismatched.published_keep_post.pk, other_map.pk)
        # The cache follows the link.
        map_draft.published_post = None
        self.assertIsNone(map_draft.published_keep_post)

    def test_clean_rejects_type_mismatched_links(self):
        keep_faction = KeepFaction.objects.create(title='Fox Folk', animal='Fox', designer=self.designer, type='M', reach=6)
        with self.assertRaises(ValidationError):
            ForgedFaction(component='Vagabond', published_post=keep_faction).clean()
        ForgedFaction(component='Clockwork', published_post=keep_faction).clean()  # same Faction model
        from the_keep.models import PostTranslation
        translation = PostTranslation.objects.create(post=keep_faction, language=Language.objects.get(),
                                                     translated_title='Zorros')
        with self.assertRaises(ValidationError):
            ForgedFaction(component='Map', published_translation=translation).clean()
        with self.assertRaises(ValidationError):
            ForgedFaction(published_post=keep_faction, published_translation=translation).clean()

    def test_unlink_clears_both_links(self):
        keep = KeepFaction.objects.create(title='Fox Folk', animal='Fox', designer=self.designer, type='M', reach=6)
        draft = self._draft(name='Fox Folk', published_post=keep)
        ForgedFaction.objects.filter(pk=draft.pk).update(published_faction=keep)
        self.client.force_login(self.user)
        self.client.post(reverse('forge-faction-unlink', args=[draft.pk]))
        draft.refresh_from_db()
        self.assertIsNone(draft.published_post_id)
        self.assertIsNone(draft.published_faction_id)


class BackfillForgePublishedPostTests(TestCase):
    def setUp(self):
        Language.objects.create(code='en', name='English')
        self.designer = Profile.objects.create(discord='backfill')
        self.keep = KeepFaction.objects.create(title='Fox Folk', animal='Fox', designer=self.designer, type='M', reach=6)

    def _run(self, *args):
        out = StringIO()
        call_command('backfill_forge_published_post', *args, stdout=out)
        return out.getvalue()

    def test_moves_the_link_and_is_idempotent(self):
        draft = ForgedFaction.objects.create(designer=self.designer, faction_name='Fox Folk')
        ForgedFaction.objects.filter(pk=draft.pk).update(published_faction=self.keep)
        unlinked = ForgedFaction.objects.create(designer=self.designer, faction_name='Loose')

        self.assertIn('would have moved 1', self._run('--dry-run'))
        draft.refresh_from_db()
        self.assertIsNone(draft.published_post_id)

        self.assertIn('moved 1', self._run())
        draft.refresh_from_db()
        self.assertEqual((draft.published_post_id, draft.published_faction_id), (self.keep.pk, None))

        self.assertIn('moved 0', self._run())
        unlinked.refresh_from_db()
        self.assertIsNone(unlinked.published_post_id)

    def test_disagreeing_links_are_reported_and_left_alone(self):
        other = KeepFaction.objects.create(title='Other', animal='Fox', designer=self.designer, type='M', reach=6)
        draft = ForgedFaction.objects.create(designer=self.designer, faction_name='Fox Folk', published_post=other)
        ForgedFaction.objects.filter(pk=draft.pk).update(published_faction=self.keep)
        self.assertIn('Skipped', self._run())
        draft.refresh_from_db()
        self.assertEqual((draft.published_post_id, draft.published_faction_id), (other.pk, self.keep.pk))


class ForgedFactionAdminTests(TestCase):
    def test_component_and_legacy_link_are_read_only(self):
        from django.contrib import admin
        model_admin = admin.site._registry[ForgedFaction]
        self.assertIn('component', model_admin.readonly_fields)
        self.assertIn('published_faction', model_admin.readonly_fields)
        self.assertIn('published_post', model_admin.raw_id_fields)


class ForgeTTSNamingTests(TestCase):
    def test_board_and_vp_marker_share_the_faction_name(self):
        from .services.tts import TTSForgedFactionBoard, place_markers_for_faction
        from the_keep.services.tts import SiteURLRequest
        designer = Profile.objects.create(discord='namingdesigner')
        forged = ForgedFaction.objects.create(designer=designer, faction_name='Fox Folk')
        board = TTSForgedFactionBoard(forged, request=SiteURLRequest()).to_dict()
        self.assertEqual(board['Nickname'], 'Fox Folk - Faction Board')
        forged.vp_marker = 'forge/factions/fox-folk/board/vp_marker.webp'
        markers = place_markers_for_faction(forged, request=SiteURLRequest())
        self.assertEqual([m['Nickname'] for m in markers], ['Fox Folk VP'])

    def test_faction_boards_have_no_tooltip(self):
        from .services.tts import TTSForgedFactionBoard, TTSForgedFactionDecree
        from the_keep.models import Faction as KeepFaction
        from the_keep.services.tts import SiteURLRequest, TTSBoard
        designer = Profile.objects.create(discord='tooltipdesigner')
        forged = ForgedFaction.objects.create(designer=designer, faction_name='Fox Folk')
        self.assertFalse(TTSForgedFactionBoard(forged, request=SiteURLRequest()).to_dict()['Tooltip'])
        self.assertFalse(TTSForgedFactionDecree(forged, request=SiteURLRequest()).to_dict()['Tooltip'])
        keep = KeepFaction.objects.create(title='Fox Folk', animal='Fox', designer=designer, component='Faction')
        self.assertFalse(TTSBoard(keep, request=SiteURLRequest()).to_dict()['Tooltip'])


# ── Forge maps (forge/map/) ─────────────────────────────────────────────────

from io import BytesIO

from the_keep.services.tts_map import FORGE_FRAME, OFFICIAL_RATIO, MapGeometry, print_layout
from .models import ForgedMap
from .pdf_cache import fingerprint_map


def _image_upload(width, height, name='art.png', fmt='PNG'):
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (width, height), (120, 160, 90)).save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f'image/{fmt.lower()}')


def _fixed_layout(**extra):
    state = {
        'track_y': 0.95, 'items': [0.22, 0.04], 'logo': [0.8, 0.07], 'fixed': True,
        'clearings': [
            {'centre': [0.3, 0.4], 'marker': [0.3, 0.48], 'number': 1, 'suit': 'fox'},
            {'centre': [0.6, 0.5], 'marker': [0.68, 0.5], 'number': 2, 'suit': 'city'},
        ],
        'forests': [[0.45, 0.45]],
        'buildings': [{'at': [0.33, 0.4], 'ruin': False}, {'at': [0.62, 0.48], 'ruin': True}],
    }
    state.update(extra)
    return state


@override_settings(MEDIA_ROOT=_TTS_MEDIA, SITE_URL='https://example.test')
class ForgedMapTests(TestCase):
    def setUp(self):
        from django.contrib.auth.signals import user_logged_in
        from the_gatehouse.signals import user_logged_in_handler
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)

        Language.objects.get_or_create(code='en', defaults={'name': 'English'})
        self.user = User.objects.create_user(username='cartographer', password='pw')
        self.designer = Profile.objects.get(user=self.user)
        Profile.objects.filter(pk=self.designer.pk).update(group='D', player_onboard=True, forge_onboard=True)
        self.client.force_login(self.user)

    def _create(self, name='Highlands'):
        response = self.client.post(reverse('forge-map-create'))
        draft = ForgedFaction.objects.get(component='Map', faction_name='Untitled Map')
        self.assertRedirects(response, reverse('forge-map-builder', args=[draft.pk]), fetch_redirect_response=False)
        ForgedFaction.objects.filter(pk=draft.pk).update(faction_name=name)
        draft.refresh_from_db()
        return draft

    def _upload(self, draft, width=1090, height=1000):
        return self.client.post(reverse('forge-map-art', args=[draft.pk]), {'art': _image_upload(width, height)})

    def _save(self, draft, state, generate=False, **extra):
        return self.client.post(reverse('forge-map-builder', args=[draft.pk]),
                                json.dumps({'state': state, 'generate': generate, **extra}),
                                content_type='application/json')

    def test_faction_pages_leave_maps_out(self):
        draft = self._create()
        ForgedFaction.objects.create(designer=self.designer, faction_name='Otters', component='Faction')
        content = self.client.get(reverse('forge-faction-list')).content.decode()
        self.assertIn('Otters', content)
        self.assertNotIn('Highlands', content)
        self.assertIn('Highlands', self.client.get(reverse('forge-map-list')).content.decode())
        detail = reverse('forge-map-detail', args=[draft.pk])
        for name in ('forge-faction-detail', 'forge-faction-edit', 'forge-faction-tts'):
            with self.subTest(name=name):
                self.assertRedirects(self.client.get(reverse(name, args=[draft.pk])), detail,
                                     fetch_redirect_response=False)
        # Deleting a map goes back to the maps.
        response = self.client.post(reverse('forge-faction-delete', args=[draft.pk]))
        self.assertRedirects(response, reverse('forge-map-list'), fetch_redirect_response=False)

    def test_new_map_opens_the_builder_on_its_upload_step(self):
        draft = self._create()
        self.assertEqual(draft.component, 'Map')
        self.assertEqual(draft.forged_map.tts_builder['logo'], [0.8, 0.07])
        config = self.client.get(reverse('forge-map-builder', args=[draft.pk])).context['config']
        self.assertTrue(config['upload_step'])
        self.assertIsNone(config['image'])
        self.assertEqual((config['width'], config['height']), FORGE_FRAME)
        self.assertEqual(set(config['print']), {'vp', 'items', 'logo'})
        self.assertEqual(config['step_text']['upload']['link']['url'], reverse('map-create'))
        self.assertIn("AI-generated", config['step_text']['upload']['hint'])
        self.assertEqual(config['expected'], {'clearings': None, 'forests': None, 'buildings': None, 'ruins': None})

    def test_new_map_is_untitled_and_named_on_the_art_step(self):
        # A visit never creates a map; New Map posts.
        self.assertRedirects(self.client.get(reverse('forge-map-create')), reverse('forge-map-list'),
                             fetch_redirect_response=False)
        self.assertFalse(ForgedFaction.objects.filter(component='Map').exists())
        self.client.post(reverse('forge-map-create'))
        draft = ForgedFaction.objects.get(component='Map')
        self.assertEqual(draft.faction_name, 'Untitled Map')
        config = self.client.get(reverse('forge-map-builder', args=[draft.pk])).context['config']
        self.assertEqual(config['map_name'], 'Untitled Map')

        ForgedFaction.objects.filter(pk=draft.pk).update(tts_json={'ObjectStates': [{'Name': 'Custom_Token'}]})
        result = self._save(draft, _fixed_layout(), name='  Highlands  ').json()
        self.assertEqual(result['name'], 'Highlands')
        draft.refresh_from_db()
        self.assertEqual(draft.faction_name, 'Highlands')
        self.assertIsNone(draft.tts_json)   # it carried the old name: rebuilt on the next Complete

        result = self._save(draft, _fixed_layout(), name='   ').json()
        self.assertEqual(result['name'], 'Highlands')
        self.assertIn('Your map needs a name: kept "Highlands".', result['warnings'])

    def test_art_is_cropped_to_the_official_shape(self):
        draft = self._create()
        result = self._upload(draft, 2000, 1000).json()
        self.assertTrue(result['ok'])
        self.assertTrue(result['cropped'])
        self.assertAlmostEqual(result['width'] / result['height'], OFFICIAL_RATIO, places=2)
        self.assertEqual(result['height'], 1000)   # cropped, not stretched
        forged_map = ForgedMap.objects.get(faction=draft)
        self.assertTrue(forged_map.art_cropped)
        self.assertTrue(forged_map.art.name.endswith('map/art.webp'))
        first = fingerprint_map(forged_map)

        result = self._upload(draft, 1090, 1000).json()
        self.assertFalse(result['cropped'])
        forged_map.refresh_from_db()
        self.assertEqual(forged_map.art_version, 2)
        self.assertFalse(forged_map.art_cropped)
        self.assertNotEqual(fingerprint_map(forged_map), first)

    def test_art_upload_refuses_other_files_and_other_people(self):
        draft = self._create()
        bad = SimpleUploadedFile('art.png', b'not an image', content_type='image/png')
        response = self.client.post(reverse('forge-map-art', args=[draft.pk]), {'art': bad})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        other = User.objects.create_user(username='someone', password='pw')
        Profile.objects.filter(user=other).update(group='D', player_onboard=True, forge_onboard=True)
        self.client.force_login(other)
        self.assertEqual(self._upload(draft).status_code, 403)

    def test_save_keeps_the_logo_and_the_suit_setting(self):
        draft = self._create()
        response = self._save(draft, _fixed_layout(logo=[0.5, 0.5], extra='dropped'))
        self.assertTrue(response.json()['saved'])
        forged_map = ForgedMap.objects.get(faction=draft)
        self.assertEqual(forged_map.tts_builder['logo'], [0.5, 0.5])
        self.assertNotIn('extra', forged_map.tts_builder)
        self.assertTrue(forged_map.fixed_clearings)

    def test_complete_needs_art_and_a_finished_layout(self):
        draft = self._create()
        result = self._save(draft, _fixed_layout(), generate=True).json()
        self.assertFalse(result['ok'])
        self.assertIn("Upload your map's art first.", result['errors'])
        self._upload(draft)
        layout = _fixed_layout()
        del layout['clearings'][0]['suit']
        result = self._save(draft, layout, generate=True).json()
        self.assertFalse(result['ok'])
        self.assertFalse(ForgedMap.objects.get(faction=draft).image_preview)

    def test_complete_generates_the_map_image_and_tts_json(self):
        draft = self._create()
        self._upload(draft)
        result = self._save(draft, _fixed_layout(), generate=True).json()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['redirect'], reverse('forge-map-detail', args=[draft.pk]))
        forged_map = ForgedMap.objects.get(faction=draft)
        self.assertTrue(forged_map.image_preview.name.endswith('map/front.webp'))
        self.assertFalse(forged_map.preview_stale)
        self.assertAlmostEqual(forged_map.image_preview.width / forged_map.image_preview.height, OFFICIAL_RATIO, places=2)
        draft.refresh_from_db()
        board = draft.tts_json['ObjectStates'][0]
        self.assertIn('map/front.webp', board['CustomImage']['ImageURL'])
        self.assertEqual(board['Nickname'], 'Highlands')
        markers = [o for o in draft.tts_json['ObjectStates'] if 'Suit: city' in (o.get('Tags') or [])]
        self.assertEqual(len(markers), 1)
        self.assertEqual(len(draft.tts_json['BoardLayout']['clearings']), 2)
        self.assertTrue(Profile.objects.get(pk=self.designer.pk).map_builder_onboard)

        # The map page: the name, the generated image and the read-only layout.
        response = self.client.get(reverse('forge-map-detail', args=[draft.pk]))
        content = response.content.decode()
        self.assertIn('Highlands', content)
        self.assertIn(forged_map.image_preview.url, content)
        self.assertTrue(response.context['config']['readonly'])
        self.assertIn(reverse('forge-map-builder', args=[draft.pk]), content)
        for name in ('forge-map-pdf', 'forge-map-webp', 'forge-map-tts'):
            self.assertIn(reverse(name, args=[draft.pk]), content)
        self.assertNotIn('Layers as PDF', content)

    def test_downloads_are_for_the_designer(self):
        draft = self._create()
        self._upload(draft)
        self._save(draft, _fixed_layout())
        pdf = self.client.get(reverse('forge-map-pdf', args=[draft.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        webp = self.client.get(reverse('forge-map-webp', args=[draft.pk]))
        self.assertEqual(webp.status_code, 200)
        self.assertEqual(webp['Content-Type'], 'image/webp')
        tts = self.client.get(reverse('forge-map-tts', args=[draft.pk]))
        self.assertEqual(tts.status_code, 200)
        self.assertIn('ObjectStates', json.loads(b''.join(tts.streaming_content) if tts.streaming else tts.content))

        other = User.objects.create_user(username='someone', password='pw')
        Profile.objects.filter(user=other).update(group='D', player_onboard=True, forge_onboard=True)
        self.client.force_login(other)
        for name in ('forge-map-pdf', 'forge-map-webp', 'forge-map-tts'):
            with self.subTest(name=name):
                self.assertRedirects(self.client.get(reverse(name, args=[draft.pk])), reverse('forge-home'),
                                     fetch_redirect_response=False)

    def test_print_layout_turns_lines_to_the_marker_and_keeps_icons_upright(self):
        geometry = MapGeometry(*FORGE_FRAME)
        layout = print_layout(geometry, _fixed_layout())
        below, right = layout['suits']
        self.assertAlmostEqual(below['angle'], 90)    # marker straight below: the art's own position
        self.assertAlmostEqual(right['angle'], 0)     # marker to the right: 3 o'clock
        self.assertEqual([b['ruin'] for b in layout['buildings']], [False, True])
        self.assertEqual(print_layout(geometry, _fixed_layout(fixed=False))['suits'], [])

        from .map_pdf_engine import MapLayoutEngine
        canvas = mock.MagicMock()
        engine = MapLayoutEngine(mock.MagicMock())
        with mock.patch('the_forge.map_pdf_engine.renderPDF.draw'):
            engine._draw_suit(canvas, right)
        # The line is turned (90 - 0: from 6 o'clock to 3 o'clock); the icon isn't.
        canvas.rotate.assert_called_once_with(90)
        # Only the line is scaled to the clearing: the icon keeps its own size.
        canvas.scale.assert_called_once()
