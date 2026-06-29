"""Static file storage backends.

DevCacheBustStorage is wired into STORAGES["staticfiles"] only when DEBUG is
True (see settings.py). It appends a ?v=<file-mtime> query string to every
static URL so that editing a CSS/JS file changes its URL and the browser
refetches it — Safari in particular caches plain dev-served static files
aggressively and otherwise serves stale assets after an edit.

Production is unaffected: with DEBUG=False the project uses
ManifestStaticFilesStorage, which already content-hashes filenames, so this
class is never instantiated there.
"""
import os

from django.contrib.staticfiles.finders import find
from django.contrib.staticfiles.storage import StaticFilesStorage


class DevCacheBustStorage(StaticFilesStorage):
    """StaticFilesStorage that appends ?v=<mtime> to each URL (DEBUG only).

    The version is the mtime of the *source* file as resolved by the staticfiles
    finders (e.g. the_keep/static/the_keep/main.css) — the same file the dev
    server actually serves. We deliberately don't use self.path(), which points
    at STATIC_ROOT (the collectstatic output) and would go stale on edits.
    """

    def url(self, name, *args, **kwargs):
        url = super().url(name, *args, **kwargs)
        try:
            source = find(name)
            if source and os.path.exists(source):
                return f"{url}?v={int(os.path.getmtime(source))}"
        except (ValueError, OSError):
            pass  # fall back to the un-busted URL if the file can't be located
        return url
