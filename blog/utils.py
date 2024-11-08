import random
from django.utils.text import slugify

import csv
from the_gatehouse.models import Profile

def slugify_instance_title(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        slug = slugify(instance.title)
    Klass = instance.__class__
    qs = Klass.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_instance_title(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance

def default_picture(instance):
    animal_lower = instance.animal.lower()
    if animal_lower == "mongoose" or animal_lower == "meerkat":
        animal_lower = 'weasel'
        
    match animal_lower:
        case 'aardvark':
            return 'animals/aardvark.png'
        case 'badger':
            return 'animals/badger.png'
        case 'bat':
            return 'animals/bat.png'
        case 'beaver':
            return 'animals/beaver.png'
        case 'cat':
            return 'animals/cat.png'
        case 'crow':
            return 'animals/crow.png'
        case 'dog':
            return 'animals/dog.png'
        case 'duck':
            return 'animals/duck.png'
        case 'eagle':
            return 'animals/eagle.png'
        case 'falcon':
            return 'animals/falcon.png'
        case 'ferret':
            return 'animals/weasel.png'
        case 'fox':
            return 'animals/fox.png'
        case 'frog':
            return 'animals/frog.png'
        case 'goat':
            return 'animals/goat.png'
        case 'hawk':
            return 'animals/hawk.png'
        case 'hare':
            return 'animals/rabbit.png'
        case 'lizard':
            return 'animals/lizard.png'
        case 'mole':
            return 'animals/mole.png'
        case 'opossum':
            return 'animals/opossum.png'
        case 'otter':
            return 'animals/otter.png'
        case 'owl':
            return 'animals/owl.png'
        case 'rabbit':
            return 'animals/rabbit.png'
        case 'raccoon':
            return 'animals/raccoon.png'
        case 'skunk':
            return 'animals/skunk.png'
        case 'squirrel':
            return 'animals/squirrel.png'
        case 'tanuki':
            return 'animals/tanuki.png'
        case 'toad':
            return 'animals/frog.png'
        case 'tortoise':
            return 'animals/turtle.png'
        case 'turtle':
            return 'animals/turtle.png'
        case 'weasel':
            return 'animals/weasel.png'
        case 'wolf':
            return 'animals/wolf.png'
        case _:  # Default case
            return 'animals/default_animal.png'

