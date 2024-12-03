import random
from django.utils.text import slugify
from django.apps import apps

def slugify_post_title(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.title)
    
    # Klass = instance.__class__ 
    # for base in instance.__class__.__bases__:
    #     if 'slug' in base._meta.get_fields():
    #         Klass = base 
    #         break 

    Post = apps.get_model('the_keep', 'Post')
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
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.title)

    Expansion = apps.get_model('the_keep', 'Expansion')
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