"""Server-wide concurrency gate for the synchronous PDF/webp render path.

Rendering a faction board is memory-heavy (a full-page raster is tens of MB) and
happens synchronously inside request handlers. On a small box a burst of concurrent
uncached renders can stack past available RAM and OOM-kill the mod_wsgi daemon,
taking the whole site down.

`render_slot()` caps the number of *simultaneous* renders across all mod_wsgi
processes to `RENDER_SLOTS`, using a fixed pool of Redis keys. Requests that can't
get a slot raise `RenderBusy`, which the views translate into an immediate
503 + Retry-After. Cache hits never enter the gate, so normal downloads stay fast.

Design notes:
- Slots are N fixed keys acquired via `SET key token NX EX=ttl`. The TTL reclaims a
  slot whose worker was killed mid-render (e.g. by mod_wsgi's request-timeout, where
  the Python `finally` never runs). Keep the TTL strictly above mod_wsgi's
  request-timeout so a *valid* slow render can never outlive its own slot.
- Release is a compare-and-del Lua script keyed on a per-acquire UUID token, so a
  slow-but-alive worker can never delete a slot that already rotated to another
  worker after a TTL expiry.
- Fail OPEN: if Redis is unreachable we render *without* a slot rather than 500 the
  whole render path. Concurrency is still bounded by the mod_wsgi process count, so
  failing open cannot exceed the memory budget.
"""
import contextlib
import gc
import logging
import uuid

from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)


# Max concurrent renders across ALL mod_wsgi processes/threads. This is a
# Redis-backed cap, so it holds regardless of the thread model (prod runs
# processes=2 threads=10 => up to 20 concurrent requests, but only 3 may render
# at once). Sized for a 2GB box: 3 renders * (~200MB baseline + ~55MB peak) is
# well within budget after the OS, Apache, and Redis. Not tied to process count.
RENDER_SLOTS = 3
# Must stay strictly greater than mod_wsgi `request-timeout` (prod: 120s) so a
# killed worker's slot is reclaimed *after*, never *during*, a valid render.
RENDER_SLOT_TTL = 180

# Release only if we still own the slot (compare-and-del), atomically.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class RenderBusy(Exception):
    """Raised when every render slot is occupied. Views map this to a 503."""


def _redis():
    return cache._cache.get_client(write=True)


def busy_503():
    """Standard response when the renderer is at capacity."""
    resp = HttpResponse(
        "Renderer busy, try again shortly.",
        status=503,
        content_type="text/plain",
    )
    resp["Retry-After"] = "5"
    return resp


@contextlib.contextmanager
def render_slot(slots=RENDER_SLOTS, ttl=RENDER_SLOT_TTL):
    """Hold one of `slots` render slots for the duration of the block.

    Raises RenderBusy if none are free. Fails open (renders ungated) if Redis is
    unavailable. Runs gc.collect() on release to reclaim fitz/PIL native buffers
    promptly.
    """
    token = uuid.uuid4().hex
    acquired_key = None
    degraded = False

    try:
        r = _redis()
        for i in range(slots):
            key = f"forge:render:slot:{i}"
            if r.set(key, token, nx=True, ex=ttl):
                acquired_key = key
                break
        if acquired_key is None:
            raise RenderBusy()
    except RenderBusy:
        raise
    except Exception:
        # Redis hiccup: don't take the render path offline. The mod_wsgi process
        # count still bounds concurrency, so proceed without a slot.
        logger.warning("render_slot: Redis unavailable, rendering ungated", exc_info=True)
        degraded = True

    try:
        yield
    finally:
        if acquired_key is not None:
            try:
                r.eval(_RELEASE_LUA, 1, acquired_key, token)
            except Exception:
                # Slot will be reclaimed by its TTL; don't mask the real work.
                logger.warning("render_slot: failed to release %s", acquired_key, exc_info=True)
        gc.collect()


def guarded_render(view):
    """Decorator for simple single-build render views: run the view inside one
    render slot and translate RenderBusy into a 503.

    NOTE: this wraps the *entire* view including its permission check, so use it
    only on views where the permission check is trivial/cheap. Views with an early
    `_forbid_if_not_editor` return should instead wrap just their build body in
    `with render_slot():` so permission-denied requests don't consume a slot.
    """
    import functools

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            with render_slot():
                return view(request, *args, **kwargs)
        except RenderBusy:
            return busy_503()

    return wrapper
