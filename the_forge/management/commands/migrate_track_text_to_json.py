"""Copy legacy pipe-delimited row_titles / column_headers into the new
JSONField counterparts on CardboardTrack, converting any `{{ key }}` icon
tokens into the forge rich-text `<img data-forge-image="key">` HTML format
so PDF rendering through format_step_markup resolves the icons.

Idempotent on `--force` semantics: by default skips records whose JSON
field is already populated, but `--force` re-runs and rewrites cells so a
prior token-format migration can be normalised to the HTML format.

The legacy CharFields are left in place as a backup until a follow-up
release drops them.
"""
import re

from django.core.management.base import BaseCommand

from the_forge.models import CardboardTrack


# Matches `{{ key }}` with surrounding whitespace; key is alphanumerics +
# underscores + hyphens (matches the editor's key grammar).
_TOKEN_RE = re.compile(r'\{\{\s*([\w-]+)\s*\}\}')


def _tokens_to_html(value):
    """Convert legacy `{{ key }}` token text to forge rich-text storage
    HTML. Plain text is HTML-escaped; tokens become bare
    `<img data-forge-image="key">` elements. Already-HTML values
    (containing `<`) are returned unchanged so re-running is safe.
    """
    if not value:
        return ''
    if '<' in value:
        return value  # already in HTML form
    out = []
    last = 0
    for m in _TOKEN_RE.finditer(value):
        text = value[last:m.start()]
        if text:
            out.append(_escape_text(text))
        out.append(f'<img data-forge-image="{_escape_attr(m.group(1))}">')
        last = m.end()
    tail = value[last:]
    if tail:
        out.append(_escape_text(tail))
    return ''.join(out)


def _escape_text(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;')
              .replace('>', '&gt;'))


def _escape_attr(s):
    return (s.replace('&', '&amp;').replace('"', '&quot;')
              .replace('<', '&lt;').replace('>', '&gt;'))


class Command(BaseCommand):
    help = 'Copy CardboardTrack legacy row_titles / column_headers into row_titles_json / column_headers_json (converting {{ key }} tokens to <img> HTML).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would change without writing.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-process records whose JSON field is already populated. '
                 'Use this to normalise an earlier token-format migration to '
                 'the new HTML format.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        force = opts['force']
        updated_rows = 0
        updated_cols = 0
        skipped = 0
        examined = 0

        for track in CardboardTrack.objects.all().iterator():
            examined += 1
            changed = False

            if force or not track.row_titles_json:
                source = track.row_titles_json or (track.row_titles.split('|') if track.row_titles else [])
                if source:
                    new_rows = [
                        _tokens_to_html(source[i]) if i < len(source) else ''
                        for i in range(track.num_rows)
                    ]
                    if new_rows != track.row_titles_json:
                        track.row_titles_json = new_rows
                        updated_rows += 1
                        changed = True

            if force or not track.column_headers_json:
                source = track.column_headers_json or (track.column_headers.split('|') if track.column_headers else [])
                if source:
                    new_cols = [
                        _tokens_to_html(source[i]) if i < len(source) else ''
                        for i in range(track.num_columns)
                    ]
                    if new_cols != track.column_headers_json:
                        track.column_headers_json = new_cols
                        updated_cols += 1
                        changed = True

            if changed and not dry:
                track.save(update_fields=['row_titles_json', 'column_headers_json'])
            elif not changed:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Examined {examined} tracks. '
            f'Migrated row_titles for {updated_rows}, column_headers for {updated_cols}. '
            f'Skipped {skipped} (already migrated or empty).'
            + (' [dry-run]' if dry else '')
            + (' [force]' if force else '')
        ))
