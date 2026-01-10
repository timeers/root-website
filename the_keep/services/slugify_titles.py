import random
from unidecode import unidecode
from django.utils.text import slugify


RESERVED_SLUGS = {'edit', 'lang', 'add', 'delete'}

def slugify_post_title(instance, save=False, new_slug=None):
    from the_keep.models import Post
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.title))
    
    # Check if the slug is reserved
    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"


    qs = Post.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_post_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance



def slugify_expansion_title(instance, save=False, new_slug=None):
    from the_keep.models import Expansion
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(unidecode(instance.title))


    qs = Expansion.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_expansion_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance


def slugify_law_group_title(instance, save=False, new_slug=None):
    from the_keep.models import LawGroup
    if new_slug is not None:
        slug = new_slug
    elif instance.post:
        slug = instance.post.slug
    else:
        slug = slugify(unidecode(instance.title))

    qs = LawGroup.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_law_group_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance

def slugify_deck_group_title(instance, save=False):
    from the_keep.models import DeckGroup
    base_slug = slugify(unidecode(instance.name))
    slug = base_slug

    # Check if the slug is reserved
    if slug in RESERVED_SLUGS:
        slug = f"{slug}-{random.randint(1000, 9999)}"

    while True:
        qs = DeckGroup.objects.filter(slug=slug, post=instance.post, language=instance.language).exclude(id=instance.id)
        if not qs.exists():
            break
        rand_int = random.randint(1000, 9999)
        slug = f"{base_slug}-{rand_int}"
    instance.slug = slug
    print(slug)
    if save:
        instance.save()
    return instance
