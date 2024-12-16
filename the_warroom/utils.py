import random
from django.utils.text import slugify
from django.apps import apps

def slugify_tournament_name(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.name)

    Tournament = apps.get_model('the_warroom', 'Tournament')
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


def slugify_round_name(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.name)

    Round = apps.get_model('the_warroom', 'Round')
    qs = Round.objects.filter(slug=slug, tournament=instance.tournament).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_round_name(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance