"""The publish card on a draft's Forge page (partials/_publish_card.html)."""


def link_candidate_exists(publisher, forged, profile):
    """Cheap version of the link form's options: is there anything to link to?"""
    posts, translations, cross_language = publisher.link_querysets(forged, profile)
    return posts.exists() or translations.exists() or cross_language.exists()


def publish_context(request, forged):
    """Everything the publish card needs, or {} when the draft can't be
    published (no publisher for its component, or not the user's to edit)."""
    from . import publisher_for
    from .diff import ForgedPostSyncForm
    from ..views import _resolved_published, user_can_edit_forge

    publisher = publisher_for(forged)
    if publisher is None or not user_can_edit_forge(request, forged):
        return {}
    name_taken = publisher.title_taken(forged.faction_name, exclude_pk=forged.published_post_id)
    requirements = [{
        'label': 'Unique Name',
        'done': not name_taken,
        'warning': (f'"{forged.faction_name}" is already used by another {publisher.noun}. '
                    f'Rename your {publisher.noun.lower()} before submitting.') if name_taken else None,
    }] + [r.as_dict() for r in publisher.prerequisites(forged)]

    target, target_mode, target_label = _resolved_published(forged)
    if target_mode == 'post':
        target_url = target.get_absolute_url()
    elif target_mode == 'translation':
        target_url = target.post.get_absolute_url()
    else:
        target_url = None
    # "Updates Available" must match what the sync page shows: use the sync
    # form's own rule (any field, piece, deck, TTS asset or TTS row that can be
    # applied), not timestamps, which move on bookkeeping writes.
    sync_blocked = publisher.sync_blocked(forged) if target is not None else None
    sync_available = bool(target is not None and not sync_blocked
                          and ForgedPostSyncForm(forged=forged).has_changes)
    return {
        'publisher': publisher,
        'publish_noun': publisher.noun,
        'publish_urls': publisher.urls(forged),
        'submit_ready': all(r['done'] for r in requirements),
        'submit_requirements': requirements,
        'link_candidate_exists': link_candidate_exists(publisher, forged, request.user.profile),
        'publish_target': target,
        'publish_target_mode': target_mode,
        'publish_target_label': target_label,
        'publish_target_url': target_url,
        'sync_available': sync_available,
        'sync_blocked': sync_blocked,
        'publish_note': getattr(publisher, 'pending_note', lambda f: None)(forged),
    }
