import random
from django.utils.text import slugify
import uuid


def slugify_instance_discord(instance, save=False, new_slug=None):
    if new_slug is not None:
        slug = new_slug
    else:
        if instance.discord:
            slug = slugify(instance.discord)
        elif instance.user:
            slug = slugify(instance.user.username)
        else:
            slug = slugify(instance.dwd)
    Klass = instance.__class__
    qs = Klass.objects.filter(slug=slug).exclude(id=instance.id)
    if qs.exists():
        # auto generate new slug
        rand_int = random.randint(1_000, 9_999)
        slug = f"{slug}-{rand_int}"
        return slugify_instance_discord(instance, save=save, new_slug=slug)
    instance.slug = slug
    if save:
        instance.save()
    return instance



def get_uuid(request):
    # Check if the 'visitor_uuid' is already in the session
    if 'visitor_uuid' not in request.session:
        # If not, create a new UUID and store it in the session
        # Generate a UUID
        original_uuid = uuid.uuid4()
        # Take the first 8 characters
        shortened_uuid = str(original_uuid).replace('-', '')[:8]
        request.session['visitor_uuid'] = shortened_uuid

    # Retrieve the UUID from the session
    visitor_uuid = request.session['visitor_uuid']

    # Pass the UUID
    return visitor_uuid