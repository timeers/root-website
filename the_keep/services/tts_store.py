"""Writing a post's TTS JSON. Every write goes through store_tts_json(), so
Post.tts_info always matches Post.tts_json. The JSON is stored exactly as
given: generated JSON is built correctly by the site, and uploaded JSON was
reviewed by an admin."""
from django.core.exceptions import ValidationError

from ..models import Post
from ..utils import validate_tts_save
from .tts import SiteURLRequest, build_tts_objects, wrap_tts_save
from .tts_inspect import describe_tts_json


def store_tts_json(post, tts_json, *, source, profile=None, filename=None):
    """Save `tts_json` unchanged with its tts_info. A queryset update: tts_json
    lives on the Post table, and this skips Post.save() side effects."""
    info = describe_tts_json(post, tts_json, source=source, profile=profile, filename=filename)
    Post.objects.filter(pk=post.pk).update(tts_json=tts_json, tts_info=info)
    post.tts_json, post.tts_info = tts_json, info
    return info


def clear_tts_json(post):
    """None (not {}): the Library catalogue lists posts with tts_json set."""
    Post.objects.filter(pk=post.pk).update(tts_json=None, tts_info=None)
    post.tts_json = post.tts_info = None


def recheck_tts_info(post):
    """Recompute tts_info for the stored JSON, keeping its recorded source."""
    info = describe_tts_json(post, post.tts_json, previous=post.tts_info or {})
    Post.objects.filter(pk=post.pk).update(tts_info=info)
    post.tts_info = info
    return info


def forge_linked(post):
    from the_forge.models import ForgedFaction
    return ForgedFaction.objects.filter(published_post_id=post.pk).first()


def generate_unavailable_reason(post):
    """Why the builder can't (re)generate this post's JSON, or None."""
    if post.component == Post.ComponentChoices.MAP:
        from .tts_map import check_builder
        if not post.board_image:
            return "Add a board image to this map first."
        if not getattr(post, 'tts_builder', None):
            return "Lay out the map in the map builder first."
        errors, _warnings = check_builder(post, post.tts_builder)
        if errors:
            return "Finish the map builder first: " + " ".join(errors)
    if forge_linked(post):
        return "This faction's JSON comes from its Forge draft. Update it from the Forge sync page."
    return None


def generate_tts_json(post, profile=None):
    """Build and store the post's JSON from its site data.
    Returns (ok, message)."""
    reason = generate_unavailable_reason(post)
    if reason:
        return False, reason
    if post.component == Post.ComponentChoices.MAP:
        from .tts_map import build_map_tts_json
        save_file = build_map_tts_json(post, post.tts_builder)
    else:
        save_file = wrap_tts_save(build_tts_objects(post, request=SiteURLRequest()), save_name=post.title)
    try:
        validate_tts_save(save_file)
    except ValidationError:
        return False, "Nothing to generate from this post's data."
    store_tts_json(post, save_file, source='generated', profile=profile)
    return True, "Generated TTS JSON."
