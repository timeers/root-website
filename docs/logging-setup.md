# Logging + Slow-Request Visibility (deferred)

Split out of the WSGI-timeout remediation plan to tackle later. Goal: make
incidents diagnosable and let us *measure* the effect of other fixes, by
capturing which request paths are slow.

## Problem

The `LOGGING` config in `django_project/settings.py` is entirely commented out
(~lines 382–434). Because of that, the existing
`RequestTimingMiddleware` (`the_gatehouse/middleware.py:67`) — which already
computes request duration and calls
`logger.warning("Slow request: <path> took Ns")` for anything over 4s — writes
to **no handler**, so those warnings are discarded. We currently have no record
of which URLs are slow.

## Change

1. Add a `LOGGING` dict in `settings.py` with a `RotatingFileHandler`
   (e.g. `logs/django.log`, ~5 MB × 3 backups) and a verbose formatter.
2. Wire the `the_gatehouse.middleware` logger (and root at `WARNING`) to that
   handler so the slow-request warnings land in the file.
3. Temporarily lower the `RequestTimingMiddleware` threshold from 4s → ~1s to
   catch borderline-slow paths during an investigation window, then restore.
4. Ensure the `logs/` directory exists (and is git-ignored) or point the handler
   at an existing writable log location on the server.

## Optional extras

- An `AdminEmailHandler` / SMTP handler for `ERROR`+ (the old commented config
  already sketched this) so 500s notify by email.
- Capturing the path even when mod_wsgi kills a request before the middleware's
  post-response log line runs (the timing log only fires if the view returns).

## Acceptance

Slow paths appear in `logs/django.log`. During a synthetic crawl
(`for p in $(seq 1 50); do curl ".../?page=$p"; done`) the offending paths show
up; after the pagination 404 fix lands, the `?page=` entries stop appearing.
