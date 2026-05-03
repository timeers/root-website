import random
from unidecode import unidecode
from django.utils.text import slugify


RESERVED_SLUGS = {'edit', 'lang', 'add', 'delete', 'new'}


def slugify_forged_faction_name(instance, save=False, new_slug=None):
    from the_forge.models import ForgedFaction
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.faction_name))

    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"

    qs = ForgedFaction.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_forged_faction_name(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance
