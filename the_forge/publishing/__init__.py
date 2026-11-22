"""Publishing a Forge draft to the Root Database (the_keep): submit it as a new
post, link it to an existing post or translation, keep it in sync, unlink it.

Everything that differs between components lives in a Publisher
(publishing/base.py), one per component: Faction and Clockwork
(publishing/faction.py), Map (publishing/map.py). The submit, link, unlink and
sync views, the sync diff (publishing/diff.py) and the publish card are shared.
A component without a publisher (Deck, Vagabond... for now) isn't offered
publishing at all. Adding one = a Publisher subclass registered below, plus its
submit template."""


def publisher_for(forged):
    """The Publisher for a draft's component, or None."""
    from .faction import FactionPublisher
    from .map import MapPublisher
    for publisher in (FactionPublisher(), MapPublisher()):
        if forged.component in publisher.components:
            return publisher
    return None
