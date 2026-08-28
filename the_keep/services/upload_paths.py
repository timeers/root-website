import os
import uuid

from django.conf import settings

def language_code(obj):
    """The language code of `obj`, or None when it has no language set.

    Post.language is nullable (SET_DEFAULT with a default that returns None when
    no Language rows exist), so `obj.language.code` raises AttributeError on a
    Post saved before any Language exists -- which the image signals then swallow
    into an unreadable "Error processing ..." line. upload_path() already drops
    falsy slug parts, so returning None just omits the language folder."""
    language = getattr(obj, "language", None)
    return language.code if language else None


def upload_path(slug_parts, filename=None, force_ext=None):
    """
    Construct a relative MEDIA_ROOT path and optionally delete old file.
    
    slug_parts: list of folder names (strings)
    filename: fixed filename (with extension), or None to generate UUID
    force_ext: optional extension to force (e.g., 'webp'). If None, keep extension from filename or use 'wepb'.
    """
    slug_parts = [str(part) for part in slug_parts if part]
    folder = os.path.join("uploads", *slug_parts)
    os.makedirs(os.path.join(settings.MEDIA_ROOT, folder), exist_ok=True)
    
    if not filename:
        filename = uuid.uuid4().hex
    
    # Extract extension from filename or use force_ext
    name, ext = os.path.splitext(filename)
    ext = ext if ext else ''
    
    if force_ext:
        ext = f".{force_ext.lstrip('.')}"  # ensure dot
    
    if not ext:
        ext = ".webp"
    
    filename = f"{name}{ext}"
    
    relative_path = os.path.join(folder, filename)
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    
    if os.path.exists(full_path):
        os.remove(full_path)
    
    return relative_path


# Upload paths in the format: "uploads/posts/eyrie-dynasties/en/decks/eyrie-leaders/back.webp"
# Keeps uploaded images organized and reusable for TTS

def post_picture_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug],
        filename="picture.webp",
        force_ext='webp'
    )

def post_icon_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug],
        filename="icon.webp",
        force_ext='webp'
    )

def board_front_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug, language_code(instance), "board"],
        filename="front.webp",
        force_ext='webp'
    )

def board_back_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug, language_code(instance), "board"],
        filename="back.webp",
        force_ext='webp'
    )

def card_front_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug, language_code(instance), "card"],
        filename="front.webp",
        force_ext='webp'
    )

def card_back_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.slug, language_code(instance), "card"],
        filename="back.webp",
        force_ext='webp'
    )

def post_small_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower() if filename else None
    return upload_path(
        slug_parts=["posts", instance.slug, language_code(instance), "small_images"],
        filename=None,
        force_ext=ext.lstrip('.') if ext else None
    )


def translation_board_front_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "board"],
        filename="front.webp",
        force_ext='webp'
    )

def translation_board_back_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "board"],
        filename="back.webp",
        force_ext='webp'
    )

def translation_card_front_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "card"],
        filename="front.webp",
        force_ext='webp'
    )

def translation_card_back_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "card"],
        filename="back.webp",
        force_ext='webp'
    )

def translation_small_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower() if filename else None
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "small_images"],
        filename=None,
        force_ext=ext.lstrip('.') if ext else None
    )


def deck_back_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.post.slug, language_code(instance), "decks", instance.slug],
        filename="back.webp",
        force_ext='webp'
    )


def deck_sheet_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.group.post.slug, language_code(instance.group), "decks", instance.group.slug],
        filename="sheet.webp",
        force_ext='webp'
    )


def card_upload_path(instance, filename):
    return upload_path(
        slug_parts=["posts", instance.group.post.slug, language_code(instance.group), "decks", instance.group.slug, "cards"],
        filename=None,
        force_ext='webp'
    )

def piece_upload_path(instance, filename):
    piece_type = instance.get_type_display().lower()
    return upload_path(
        slug_parts=["posts", instance.parent.slug, f'{piece_type}s'],
        filename=None,
        # filename=str(instance.uuid),
        force_ext='webp'
    )

def avatar_upload_path(instance, filename):
    return upload_path(
        slug_parts=["users", instance.slug],
        filename=None,
    )

def changelog_image_upload_path(instance, filename):
    return upload_path(
        slug_parts=["website", "changelogs"],
        filename=instance.slug,
    )