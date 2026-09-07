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
            published_faction=None,
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
        self.assertIsNone(copy.published_faction_id)
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

