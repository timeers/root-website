import random
from django.utils.text import slugify
from unidecode import unidecode
from the_warroom.models import Round, Tournament, Stage

RESERVED_SLUGS = {
    'edit', 'lang', 'add', 'delete', 
    'concluded', 'ongoing', 'my-series', 'scheduled',
    'groups', 'tournaments', 'leagues',
    }

def slugify_tournament_name(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.name))

    # Check if the slug is reserved
    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"

    qs = Tournament.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_tournament_name(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance

def slugify_stage_name(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.name))

    qs = Stage.objects.filter(slug=slug, tournament=instance.tournament).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_stage_name(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance


def slugify_round_name(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        if instance.name:
            slug = slugify(unidecode(instance.name))
        else:
            slug = f"round-{random.randint(1_000, 9_999)}"

    qs = Round.objects.filter(slug=slug, stage=instance.stage).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_round_name(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance