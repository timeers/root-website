"""What a component's publishing needs to say about itself (Publisher), and one
sync-able keep field (SyncField)."""
from dataclasses import dataclass, field
from typing import Callable, Optional

from django.urls import reverse

from the_keep.models import PostTranslation


# Submitting needs a thread for the post on one of these (each link's own
# format is checked by the keep's PostCreateForm).
SUBMIT_THREAD_LINKS = ('ww_link', 'fr_link', 'bgg_link')
THREAD_LINK_MISSING = "Add a link to a Woodland Warriors, French Root or Board Game Geek thread."


def link_context():
    """What the submit pages' Links card (partials/_submit_links.html) needs to
    check Discord thread links in the browser."""
    from the_keep.forms import config
    return {
        'ww_guild_id': config.get('WW_GUILD_ID', ''),
        'wr_guild_id': config.get('WR_GUILD_ID', ''),
        'fr_guild_id': config.get('FR_GUILD_ID', ''),
    }


def require_thread_link(form, cleaned):
    """A submit form's rule: at least one of SUBMIT_THREAD_LINKS."""
    if not any((cleaned.get(name) or '').strip() for name in SUBMIT_THREAD_LINKS):
        form.add_error(None, THREAD_LINK_MISSING)


@dataclass
class SyncField:
    """One keep field the sync page can update from the Forge.

    kind: 'text', 'choice', 'int', 'bool', 'json' or 'image'. Images are copied
    into the keep and compared by version: in sync when the keep has an image
    and the Forge's `version` is no newer than the keep's `keep_version_attr`
    (file names never match across the two). Other kinds compare values."""
    name: str
    label: str
    source: Callable                      # forged -> proposed value (None/'' = no row)
    kind: str = 'text'
    display: Optional[Callable] = None    # value -> what the sync page shows
    version: Optional[Callable] = None    # forged -> the Forge image's version
    keep_version_attr: Optional[str] = None
    in_sync: Optional[Callable] = None    # (forged, current, proposed) -> bool, overrides the default
    on_applied: Optional[Callable] = None  # forged -> None, after the target is saved

    @property
    def is_image(self):
        return self.kind == 'image'


@dataclass
class Requirement:
    label: str
    done: bool
    warning: Optional[str] = None

    def as_dict(self):
        return {'label': self.label, 'done': self.done, 'warning': self.warning}


class Publisher:
    """Override what differs; the defaults cover a plain post with images."""
    components = ()             # ForgedFaction.component values this serves
    keep_model = None           # the keep model a submit creates
    noun = ''                   # "Faction", "Map": the wording on every page
    detail_url_name = ''        # the draft's Forge page
    submit_url_name = link_url_name = unlink_url_name = sync_url_name = ''
    submit_template = ''

    # ── Forge side ──────────────────────────────────────────────────────────
    def refresh(self, forged, for_sync=False):
        """Bring the Forge side up to date before a diff, submit or sync."""

    def prerequisites(self, forged):
        """[Requirement] the draft must meet before it can be submitted (the
        name check is added by publish_context / the submit view)."""
        return []

    def missing(self, forged):
        return [r.label for r in self.prerequisites(forged) if not r.done]

    # ── Submit ──────────────────────────────────────────────────────────────
    def submit_form(self, forged, user, data=None, files=None):
        raise NotImplementedError

    def submit_images(self, forged):
        """{keep image field: (forge file, version or None)} copied on submit."""
        return {}

    def before_submit_save(self, forged, post, form):
        """Last changes to the new (unsaved) post."""

    def after_submit(self, forged, post, profile):
        """After the new post is saved and linked."""

    def submit_context(self, forged, form):
        return {}

    # ── Names and links ─────────────────────────────────────────────────────
    def title_taken(self, name, exclude_pk=None):
        """A keep post of this kind, or a translation of one, already has the
        name (case-insensitive)."""
        posts = self.keep_model.objects.filter(title__iexact=name)
        if exclude_pk:
            posts = posts.exclude(pk=exclude_pk)
        translations = PostTranslation.objects.filter(
            translated_title__iexact=name, post__component__in=self.components)
        if exclude_pk:
            translations = translations.exclude(post__pk=exclude_pk)
        return posts.exists() or translations.exists()

    def link_querysets(self, forged, profile, show_all=False):
        """(same-language posts, same-language translations, posts in other
        languages to start a translation of) the designer can link to. A draft
        only links to posts of its own component (Clockwork to Clockwork)."""
        posts = self.keep_model.objects.filter(
            designer=profile, component=forged.component,
            source_forged_post__isnull=True, language=forged.language,
        )
        if not show_all:
            posts = posts.filter(title__iexact=forged.faction_name)
        translations = PostTranslation.objects.filter(
            post__designer=profile, source_forged_faction__isnull=True,
            translated_title__iexact=forged.faction_name, language=forged.language,
            post__component=forged.component,
        ).select_related('post', 'language')
        cross_language = self.keep_model.objects.filter(
            designer=profile, component=forged.component,
        ).exclude(language=forged.language).exclude(translations__language=forged.language)
        return posts, translations, cross_language

    # ── Sync ────────────────────────────────────────────────────────────────
    def post_fields(self, forged):
        return []

    def translation_fields(self, forged):
        return []

    def extra_plans(self, forged):
        """Rows beyond the fields (Faction: pieces, decks, TTS assets)."""
        return {'piece_plan': [], 'deck_plan': [], 'tts_asset_plan': []}

    tts_forced_fields = ()      # field rows the keep TTS JSON is built from

    def tts_plan(self, forged, diff_rows, extras):
        """The sync page's TTS row ({in_sync, ...}) or None for no row."""
        return None

    def build_keep_tts(self, forged, target):
        """(the keep's TTS save, None) or (None, why not)."""
        return None, "This component has no Tabletop Simulator sync."

    def tts_fingerprint(self, forged):
        return ''

    def always_sync(self, forged, target):
        """Applied with every sync of a post (whatever rows are ticked): data
        that isn't worth a row of its own (Map: the layout)."""

    def sync_blocked(self, forged):
        """Why the draft can't be synced right now, or None."""
        return None

    # ── URLs ────────────────────────────────────────────────────────────────
    def url(self, name, forged):
        return reverse(getattr(self, f'{name}_url_name'), kwargs={'pk': forged.pk})

    def detail_url(self, forged):
        return reverse(self.detail_url_name, kwargs={'pk': forged.pk})

    def urls(self, forged):
        return {name: self.url(name, forged) for name in ('detail', 'submit', 'link', 'unlink', 'sync')}
