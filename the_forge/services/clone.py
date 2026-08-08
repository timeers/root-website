"""Generic, introspection-based deep clone of a ForgedFaction.

A ForgedFaction is a deep tree (FactionSheet, FactionBack, SetupCard, Pieces,
decks, cards, and many nested children) that grows as new features land. Rather
than hand-enumerate every field and model — which would silently drop anything
added later — this walks the CASCADE tree by introspection:

- Follow only reverse FK/O2O relations whose related model lives in ``the_forge``
  and whose FK is ``on_delete=CASCADE``. This picks up new child models for free
  and structurally cannot escape the subtree (the SET_NULL publish links live on
  ``the_keep`` models; ``designer``/``language`` are forward FKs pointing out).
- Copy all concrete scalar fields automatically; deep-copy JSON so mutable values
  aren't shared with the original.
- Copy image bytes (not the path string) so the two factions never share a file.
- Rewire FKs through an identity map keyed by ``(Model, old_pk)`` so nodes
  reachable by two paths (PhaseStep -> sheet + content_box; SetupStep -> back or
  card) are cloned once and wired correctly.

See the plan/notes for why several scalars need a post-build reconcile: some
``save()`` overrides and signals recompute copied values (ForgedCard.order,
Piece.quantity, Piece.front/back_version).
"""
import copy

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction

from .clone_flag import cloning
from .upload_paths import copy_image_field


# Reverse-relation accessor names that live in the_forge + CASCADE but should
# NOT be cloned. Empty today; kept as an explicit future hook.
SKIP_REVERSE = set()

# Concrete field names never copied straight across.
EXCLUDE_FIELDS = {
    # Publish links (OneToOne — copying would break the unique constraint; a
    # duplicate must start as an unpublished draft).
    'published_faction', 'published_translation',
    # Sync/regeneration bookkeeping and generated artifacts.
    'icon_synced_name', 'last_generated',
    'preview_fingerprint', 'preview_version', 'decree_fingerprint', 'sprite_hash',
    # slug: root gets one from the create signal; ForgedDeckGroup regenerates its
    # own in save(). Never copy the source value (unique on ForgedFaction).
    'slug',
}

# Generated preview / spritesheet images: leave blank so they regenerate.
ARTIFACT_IMAGE_FIELDS = {'image_preview', 'decree_preview', 'sprite_sheet'}

# After the tree is built, restore these from the source via the memo using
# .update() (bypasses save()/signals so nothing re-mutates them). Keyed by model
# name -> field names. These are values that a save()/signal recomputes:
#   ForgedCard.order   -> ForgedCard.save() reassigns order = max+1 for new pks
#   Piece.quantity     -> _bubble_forged_card forces it to the live card count
#   Piece.front/back_version -> Piece.save() bumps them when the image "changes"
#                               (empty in pass 2 -> real image in pass 3)
RECONCILE_FIELDS = {
    'ForgedCard': ('order',),
    'Piece': ('quantity', 'front_version', 'back_version'),
}


def _is_file_field(field):
    return isinstance(field, models.FileField)  # covers ImageField


def _cloneable_reverse_rels(model):
    """Yield reverse FK/O2O relations that belong to the clone subtree."""
    for field in model._meta.get_fields():
        if not (field.auto_created and (field.one_to_many or field.one_to_one)):
            continue
        rel_model = field.related_model
        if rel_model._meta.app_label != 'the_forge':
            continue
        if field.field.remote_field.on_delete is not models.CASCADE:
            continue
        if field.get_accessor_name() in SKIP_REVERSE:
            continue
        yield field


def _concrete_local_fields(model):
    for field in model._meta.get_fields():
        if not getattr(field, 'concrete', False):
            continue
        if field.auto_created or field.many_to_many:
            continue
        yield field


def _walk_subtree(root):
    """Depth-first list of every instance in the clone subtree, root first."""
    seen = set()
    order = []
    stack = [root]
    while stack:
        obj = stack.pop()
        key = (obj.__class__, obj.pk)
        if key in seen:
            continue
        seen.add(key)
        order.append(obj)
        for rel in _cloneable_reverse_rels(obj.__class__):
            if rel.one_to_one:
                # Reverse O2O accessor raises RelatedObjectDoesNotExist when the
                # related row is absent (e.g. a Piece with no deck_group), so it
                # must be guarded — never touched unconditionally.
                related_objs = _o2o_or_none(obj, rel)
            else:
                related_objs = list(getattr(obj, rel.get_accessor_name()).all())
            for child in related_objs:
                stack.append(child)
    return order


def _o2o_or_none(obj, rel):
    try:
        return [getattr(obj, rel.get_accessor_name())]
    except ObjectDoesNotExist:
        return []


def _build_new_instance(old, deferred_files):
    """Create the new (unsaved) instance, copying scalar + JSON fields and
    stashing image bytes. FKs and pk are left unset (handled in pass 2/3)."""
    new = old.__class__()
    for field in _concrete_local_fields(old.__class__):
        if field.primary_key:
            continue
        if field.name in EXCLUDE_FIELDS:
            continue
        if field.is_relation:  # FK / O2O -> pass 2
            continue
        if getattr(field, 'auto_now', False) or getattr(field, 'auto_now_add', False):
            continue
        if _is_file_field(field):
            if field.name in ARTIFACT_IMAGE_FIELDS:
                continue
            old_file = getattr(old, field.name)
            if old_file:
                deferred_files.append((new, field.name, copy_image_field(old_file)))
            continue
        value = getattr(old, field.attname)
        if isinstance(field, models.JSONField):
            value = copy.deepcopy(value)
        setattr(new, field.attname, value)
    return new


def _fk_fields(model):
    for field in _concrete_local_fields(model):
        if field.is_relation and (field.many_to_one or field.one_to_one):
            yield field


@transaction.atomic
def clone_forged_faction(source, *, new_name=None):
    """Deep-clone ``source`` and its whole CASCADE tree; return the new root."""
    with cloning():  # suppress the four "New ..." Discord posts
        old_nodes = _walk_subtree(source)

        # Pass 1: build new instances, copy scalars/JSON, stash image bytes.
        memo = {}
        deferred_files = []
        for old in old_nodes:
            new = _build_new_instance(old, deferred_files)
            memo[(old.__class__, old.pk)] = new

        # Pass 2: rewire FKs through the memo, then save parents-before-children.
        for old in old_nodes:
            new = memo[(old.__class__, old.pk)]
            for field in _fk_fields(old.__class__):
                if field.name in EXCLUDE_FIELDS:
                    continue
                old_target_id = getattr(old, field.attname)
                if old_target_id is None:
                    continue
                mapped = memo.get((field.related_model, old_target_id))
                if mapped is not None:
                    setattr(new, field.name, mapped)      # in-subtree ref
                else:
                    setattr(new, field.attname, old_target_id)  # external ref
            from ..models import ForgedFaction
            if isinstance(new, ForgedFaction):
                new.faction_name = new_name or f'{source.faction_name} (Copy)'
                new.slug = None  # create signal assigns a unique deduped slug

        _save_parents_first(old_nodes, memo)

        # Pass 3: assign copied image bytes now that pks/slug/parents exist.
        touched = []
        for new, field_name, content in deferred_files:
            setattr(new, field_name, content)
            touched.append(new)
        for new in _dedupe(touched):
            new.save()

        # Pass 4: restore scalars that save()/signals recomputed.
        _reconcile_scalars(old_nodes, memo)

        return memo[(source.__class__, source.pk)]


def _save_parents_first(old_nodes, memo):
    """Save each new instance only once every FK it points at (within the clone)
    has a pk. Avoids hardcoding sibling order (e.g. ContentBox before PhaseStep).
    All in-subtree cross-links are nullable, so the worklist always progresses
    and terminates.
    """
    pending = list(old_nodes)
    while pending:
        still = []
        for old in pending:
            new = memo[(old.__class__, old.pk)]
            if _fks_ready(new):
                _sync_fk_ids(new)
                new.save()
            else:
                still.append(old)
        if len(still) == len(pending):
            raise RuntimeError('clone_forged_faction: unresolved FK ordering (cycle?)')
        pending = still


def _sync_fk_ids(new):
    """Re-assign each in-subtree FK object so its now-saved pk is written to the
    ``<field>_id`` attribute before we save the child."""
    for field in _fk_fields(new.__class__):
        if field.name in EXCLUDE_FIELDS:
            continue
        try:
            target = getattr(new, field.name)
        except field.related_model.DoesNotExist:
            target = None
        if target is not None and target.pk is not None:
            setattr(new, field.name, target)


def _fks_ready(new):
    """True if every assigned in-subtree FK on ``new`` already has a saved pk.

    An unsaved related object (still awaiting its own save) has ``pk is None``;
    because pass 2 assigns the *same* memo instance, saving the parent later
    makes this check pass on a subsequent worklist round.
    """
    for field in _fk_fields(new.__class__):
        if field.name in EXCLUDE_FIELDS:
            continue
        try:
            target = getattr(new, field.name)
        except field.related_model.DoesNotExist:
            target = None
        if target is not None and target.pk is None:
            return False
    return True


def _reconcile_scalars(old_nodes, memo):
    for old in old_nodes:
        fields = RECONCILE_FIELDS.get(old.__class__.__name__)
        if not fields:
            continue
        new = memo[(old.__class__, old.pk)]
        values = {name: getattr(old, name) for name in fields}
        old.__class__.objects.filter(pk=new.pk).update(**values)


def _dedupe(instances):
    seen = set()
    out = []
    for inst in instances:
        key = id(inst)
        if key in seen:
            continue
        seen.add(key)
        out.append(inst)
    return out
