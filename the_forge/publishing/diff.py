"""The sync page's diff and form, for any component: one row per out-of-date
SyncField (publishing/base.py), plus the publisher's extra rows (Faction: pieces,
decks, TTS assets) and its Tabletop Simulator row."""
from django import forms

from ..services.upload_paths import copy_image_field


def sync_mode(forged):
    """'post' when linked to a keep post, 'translation' when linked to one of
    its translations, else None."""
    if forged.published_post_id:
        return 'post'
    if forged.published_translation_id:
        return 'translation'
    return None


def sync_target(forged):
    mode = sync_mode(forged)
    if mode == 'post':
        return forged.published_keep_post
    if mode == 'translation':
        return forged.published_translation
    return None


def _values_equal(current, proposed, is_image):
    if is_image:
        current_name = getattr(current, 'name', '') if current else ''
        proposed_name = getattr(proposed, 'name', '') if proposed else ''
        return current_name == proposed_name
    if current in (None, '') and proposed in (None, ''):
        return True
    return current == proposed


def field_in_sync(field, forged, current, proposed):
    if field.in_sync is not None:
        return field.in_sync(forged, current, proposed)
    if field.is_image and field.version is not None:
        # File paths never match across the Forge and the keep: compare the
        # Forge image's version with the one stamped on the keep at the last
        # push. A missing keep image is never in sync (Post.save() can bump a
        # version on an empty field, so versions alone can lie).
        keep_version = getattr(sync_target(forged), field.keep_version_attr, 0) or 0
        return bool(current) and (field.version(forged) or 0) <= keep_version
    return _values_equal(current, proposed, field.is_image)


def sync_fields(forged, publisher=None):
    from . import publisher_for
    publisher = publisher or publisher_for(forged)
    mode = sync_mode(forged)
    if publisher is None or mode is None:
        return []
    return publisher.post_fields(forged) if mode == 'post' else publisher.translation_fields(forged)


def build_diff(forged, publisher=None):
    """(field name, label, current, proposed, is_image, in_sync) for every
    field with a Forge value. Rows already matching are flagged in_sync, so the
    page shows them as "Up to date" without a checkbox."""
    target = sync_target(forged)
    rows = []
    for field in sync_fields(forged, publisher):
        proposed = field.source(forged)
        if proposed is None or proposed == '':
            continue
        current = getattr(target, field.name, None)
        in_sync = field_in_sync(field, forged, current, proposed)
        if field.display is not None:
            current, proposed = field.display(current), field.display(proposed)
        rows.append((field.name, field.label, current, proposed, field.is_image, in_sync))
    return rows


class ForgedPostSyncForm(forms.Form):
    """Per-row opt-in sync of Forge values into the linked keep post or
    translation. Each out-of-date row is a BooleanField: ``sync_<field>``,
    ``piece_<pk>``, ``deck_<pk>``, ``ttsasset_<role>`` and ``sync_tts_json``."""

    def __init__(self, *args, forged=None, **kwargs):
        if forged is None:
            raise ValueError('ForgedPostSyncForm requires forged kwarg')
        from . import publisher_for
        self.forged = forged
        self.publisher = publisher_for(forged)
        self.diff_rows = build_diff(forged, self.publisher) if self.publisher else []
        extras = self.publisher.extra_plans(forged) if self.publisher and self.mode == 'post' else {}
        self.piece_plan = extras.get('piece_plan', [])
        self.deck_plan = extras.get('deck_plan', [])
        self.tts_asset_plan = extras.get('tts_asset_plan', [])
        self.tts_plan = (self.publisher.tts_plan(forged, self.diff_rows, extras)
                         if self.publisher and self.mode == 'post' else None)
        super().__init__(*args, **kwargs)
        for field_name, label, _current, _proposed, _is_image, in_sync in self.diff_rows:
            if in_sync:
                continue
            self.fields[f'sync_{field_name}'] = forms.BooleanField(required=False, initial=True, label=label)
        for entry in self.piece_plan:
            if not entry['in_sync']:
                self.fields[f'piece_{entry["forge_piece"].pk}'] = forms.BooleanField(
                    required=False, initial=True, label=f"{entry['type_display']}: {entry['resolved_name']}")
        for entry in self.deck_plan:
            if not entry['in_sync']:
                self.fields[f'deck_{entry["forge_piece"].pk}'] = forms.BooleanField(
                    required=False, initial=True, label=f"Deck: {entry['forge_group']}")
        for entry in self.tts_asset_plan:
            if not entry['in_sync']:
                self.fields[f'ttsasset_{entry["role"]}'] = forms.BooleanField(
                    required=False, initial=True, label=entry['label'])
        if self.tts_plan and not self.tts_plan['in_sync']:
            self.fields['sync_tts_json'] = forms.BooleanField(
                required=False, initial=True, label='Tabletop Simulator')

    @property
    def mode(self):
        return sync_mode(self.forged)

    @property
    def has_changes(self):
        """True when any row on the sync page can be applied. The Forge pages'
        "Updates Available" uses this too, so the two can't disagree."""
        return (
            any(not row[5] for row in self.diff_rows)
            or any(not e['in_sync'] for e in self.piece_plan)
            or any(not e['in_sync'] for e in self.deck_plan)
            or any(not e['in_sync'] for e in self.tts_asset_plan)
            or bool(self.tts_plan and not self.tts_plan['in_sync'])
        )

    @property
    def tts_forced_field_names(self):
        """Out-of-date rows the TTS JSON is built from. Empty means every asset
        the keep TTS JSON uses is already a current keep copy."""
        forced = self.publisher.tts_forced_fields if self.publisher else ()
        names = [f'sync_{row[0]}' for row in self.diff_rows if row[0] in forced and not row[5]]
        names += [f'piece_{e["forge_piece"].pk}' for e in self.piece_plan if not e['in_sync']]
        names += [f'deck_{e["forge_piece"].pk}' for e in self.deck_plan if not e['in_sync']]
        names += [f'ttsasset_{e["role"]}' for e in self.tts_asset_plan if not e['in_sync']]
        return names

    def clean(self):
        cleaned = super().clean()
        # The keep TTS JSON must only reference keep files and data, so syncing
        # it applies every row it depends on (the template also locks them).
        if cleaned.get('sync_tts_json'):
            for name in self.tts_forced_field_names:
                if name in self.fields:
                    cleaned[name] = True
        # A deck attaches to a keep piece, so a deck whose piece isn't on the
        # keep yet also applies that piece's create row.
        for entry in self.deck_plan:
            pk = entry['forge_piece'].pk
            if cleaned.get(f'deck_{pk}') and entry['keep_piece'] is None and f'piece_{pk}' in self.fields:
                cleaned[f'piece_{pk}'] = True
        return cleaned

    def save(self):
        if not self.is_valid():
            raise ValueError('Form must be valid before save()')
        target = sync_target(self.forged)
        if target is None or self.publisher is None:
            return None

        applied = []
        for field in sync_fields(self.forged, self.publisher):
            name = field.name
            if f'sync_{name}' not in self.fields or not self.cleaned_data.get(f'sync_{name}'):
                continue
            proposed = field.source(self.forged)
            if field.is_image:
                setattr(target, name, copy_image_field(proposed))
                if field.version is not None and field.keep_version_attr:
                    setattr(target, field.keep_version_attr, field.version(self.forged) or 0)
            else:
                setattr(target, name, proposed)
            applied.append(field)
        if applied:
            target.save()
        for field in applied:
            if field.on_applied is not None:
                field.on_applied(self.forged)

        if self.mode == 'post':
            from ..forms_publish import apply_extra_plans
            apply_extra_plans(self, target)
            self.publisher.always_sync(self.forged, target)
        return target


# The name the Faction code and tests have always used.
ForgedFactionSyncForm = ForgedPostSyncForm
