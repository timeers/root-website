"""Clone-scoped flag used to suppress the "New Faction/Board/Back/Adset" Discord
notifications while a ForgedFaction is being deep-cloned.

Four `save()` methods (ForgedFaction, FactionSheet, FactionBack, SetupCard) fire
a Discord post on create. A clone creates all four fresh, which would spam the
channel. Rather than thread a `suppress_discord` kwarg through four unrelated
`save()` signatures, the clone runs inside `cloning()` and each notify block
checks `clone_in_progress()`.

Lives in its own leaf module (no model/service imports) so both `clone.py` and
`models.py` can import it without an import cycle.
"""
import contextlib
import contextvars

_forge_cloning = contextvars.ContextVar('_forge_cloning', default=False)


def clone_in_progress():
    """True while a deep clone is running in the current context."""
    return _forge_cloning.get()


@contextlib.contextmanager
def cloning():
    """Mark the current context as inside a clone; always resets on exit.

    The clone runs in a long-lived Celery worker, so the reset MUST happen even
    on error — otherwise a later real faction create handled by the same worker
    would have its Discord notification silently suppressed.
    """
    token = _forge_cloning.set(True)
    try:
        yield
    finally:
        _forge_cloning.reset(token)
