"""Stage timings for the Forge → keep sync, to find where its time goes.

Production has no LOGGING config, so only WARNING and above reach a handler
(Python's last-resort stderr handler, i.e. Apache's error log under mod_wsgi).
So a stage is logged as a warning only when it's slow, and once a sync has
run long, each stage's start is logged too: if mod_wsgi kills the request,
the last "starting" line shows the stage it died in.

    with SyncTimer(f'faction {pk}'):          # the view, the task, the command
        ...
        with sync_stage('refresh: sheet'):    # anywhere below it
            ...

sync_stage() does nothing outside a SyncTimer, so the timed code runs the same
from anywhere else (tests, the TTS Library snapshot, ...).
"""
import contextlib
import contextvars
import logging
import time

logger = logging.getLogger(__name__)

SLOW_STAGE = 5.0        # seconds: a stage this slow is logged
LONG_SYNC = 30.0        # seconds: past this, every stage's start is logged too

_current = contextvars.ContextVar('forge_sync_timer', default=None)


class SyncTimer:
    def __init__(self, label, echo=None, slow_stage=SLOW_STAGE, long_sync=LONG_SYNC):
        self.label = label
        self.echo = echo                # e.g. a management command's stdout.write
        self.slow_stage = slow_stage
        self.long_sync = long_sync
        self.stages = []                # (name, seconds), in finishing order
        self._depth = 0
        self._token = None
        self._started = None

    def __enter__(self):
        self._started = time.monotonic()
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc):
        _current.reset(self._token)
        total = self.elapsed
        if total >= self.slow_stage:
            summary = ', '.join(f'{name} {seconds:.1f}s' for name, seconds in self.stages)
            logger.warning('forge sync %s: took %.1fs (%s)', self.label, total, summary)
        if self.echo:
            self.echo(f'total: {total:.1f}s')
        return False

    @property
    def elapsed(self):
        return time.monotonic() - self._started

    @contextlib.contextmanager
    def stage(self, name):
        if self.elapsed >= self.long_sync:
            logger.warning('forge sync %s: starting %s at %.1fs', self.label, name, self.elapsed)
        started = time.monotonic()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            seconds = time.monotonic() - started
            self.stages.append((name, seconds))
            if seconds >= self.slow_stage:
                logger.warning('forge sync %s: %s took %.1fs', self.label, name, seconds)
            if self.echo:
                self.echo(f'{"  " * self._depth}{name}: {seconds:.2f}s')


def sync_stage(name):
    """Time `name` under the active SyncTimer; a no-op without one."""
    timer = _current.get()
    return timer.stage(name) if timer else contextlib.nullcontext()
