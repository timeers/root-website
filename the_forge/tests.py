import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from the_gatehouse.models import Profile

from .models import (
    ForgedFaction, FactionSheet, ContentBox, PhaseStep, FactionBack, SetupCard,
    Piece, ForgedDeckGroup, ForgedCardDeck, ForgedCard, CharacterImage,
)
from .services.clone import clone_forged_faction
from .services.clone_flag import clone_in_progress


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
