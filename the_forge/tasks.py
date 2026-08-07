import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def duplicate_forged_faction_task(self, source_pk):
    """Deep-clone a ForgedFaction off the request path.

    Permission is enforced in the view before dispatch; the task trusts its
    ``source_pk``. Deliberately NOT auto-retried: the clone is atomic, so a
    failure rolls back cleanly, but a blind retry would build a *second* copy.
    Failures surface as Celery FAILURE and the user can retry via the button.
    """
    from .models import ForgedFaction
    from .services.clone import clone_forged_faction

    source = ForgedFaction.objects.get(pk=source_pk)
    new_faction = clone_forged_faction(source)
    return {'new_pk': new_faction.pk}
