"""Writing a linked keep post's Tabletop Simulator JSON from its Forge draft."""
from django.core.exceptions import ValidationError


def store_keep_tts(forged, publisher, target, profile=None):
    """Build the keep's TTS JSON with the publisher, store it as Forge-synced and
    remember what it was built from (so the sync page knows when it's stale).
    Returns None on success, else why nothing was written."""
    from the_keep.models import Post
    from the_keep.services.tts_store import store_tts_json
    from the_keep.utils import tts_json_hash, validate_tts_save
    from ..models import ForgedFaction
    from ..services.sync_timing import sync_stage

    with sync_stage('tts: build'):
        save, error = publisher.build_keep_tts(forged, target)
    if save is None:
        return error
    try:
        validate_tts_save(save)
    except ValidationError:
        return "the export produced no objects."
    post = Post.objects.get(pk=target.pk)
    # A queryset update with its tts_info (store_tts_json): skips Post.save().
    with sync_stage('tts: store'):
        store_tts_json(post, save, source='forge', profile=profile)
    # Hash the value as stored: Postgres jsonb reorders keys.
    stored = Post.objects.values_list('tts_json', flat=True).get(pk=post.pk)
    forged.tts_synced_hash = tts_json_hash(stored)
    forged.tts_source_fingerprint = publisher.tts_fingerprint(forged)
    ForgedFaction.objects.filter(pk=forged.pk).update(
        tts_synced_hash=forged.tts_synced_hash, tts_source_fingerprint=forged.tts_source_fingerprint)
    return None
