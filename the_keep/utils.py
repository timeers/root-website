import random
from django.utils.text import slugify
from django.apps import apps
from django.core.exceptions import ValidationError
import re
from PIL import Image
import os

def resize_image(image_field, max_size):
    """Helper function to resize the image if necessary."""
    try:
        if image_field and os.path.exists(image_field.path):  # Check if the image exists
            print('resizing')
            img = Image.open(image_field.path)

            # Resize if the image is larger than the max_size
            if img.height > max_size or img.width > max_size:
                # Calculate the new size while maintaining the aspect ratio
                if img.width > img.height:
                    ratio = max_size / img.width
                    new_size = (max_size, int(img.height * ratio))
                else:
                    ratio = max_size / img.height
                    new_size = (int(img.width * ratio), max_size)

                # Resize image and save
                img = img.resize(new_size, Image.LANCZOS)
                img.save(image_field.path)
                print(f'Resized image saved at: {image_field.path}')
            else:
                print(f'Original image saved at: {image_field.path}')
        print('done resizing here')
    except Exception as e:
        print(f"Error resizing image: {e}")

def delete_old_image(old_image):
        """Helper method to delete old image if it exists."""
        if not old_image.name.startswith('default_images/'):
            if old_image and os.path.exists(old_image.path):
                os.remove(old_image.path)
                print(f"Old image deleted: {old_image}")
        else:
            print(f"Default image saved: {old_image}")

def validate_hex_color(value):
    # Regular expression to check for valid hex color codes (e.g., #RRGGBB)
    if not re.match(r'^#([0-9A-Fa-f]{6})$', value):
        raise ValidationError(f"{value} is not a valid hex color code.")


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