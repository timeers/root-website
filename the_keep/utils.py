import random
from django.utils.text import slugify
from django.apps import apps
from django.core.exceptions import ValidationError
import re
from PIL import Image
import os
import math

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
        
    except Exception as e:
        print(f"Error resizing image: {e}")

def delete_old_image(old_image):
    """Helper method to delete old image if it exists."""
    if old_image:
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


 
def color_distance(rgb1, rgb2):
    """
    Calculate the Euclidean distance between two RGB colors.
    """
    return math.sqrt(
        (rgb1[0] - rgb2[0]) ** 2 +
        (rgb1[1] - rgb2[1]) ** 2 +
        (rgb1[2] - rgb2[2]) ** 2
    )

def hex_to_rgb(hex_code):
    # Remove the '#' if present and convert hex to RGB
    hex_code = hex_code.lstrip('#')
    return tuple(int(hex_code[i:i+2], 16) for i in (0, 2, 4))
 
def rgb_to_hex(r, g, b):
    # Convert RGB back to hex format
    return f'#{r:02x}{g:02x}{b:02x}'
 
def complementary_color(hex_code):
    # Convert hex to RGB
    r, g, b = hex_to_rgb(hex_code)
   
    # Calculate the complementary color
    r_complement = 255 - r
    g_complement = 255 - g
    b_complement = 255 - b
   
    # Convert back to hex and return
    return rgb_to_hex(r_complement, g_complement, b_complement)


def rgb_to_color_name(rgb):
    """
    Maps an RGB tuple to a corresponding color name, including purple, violet, lavender, and blue shades.
    
    Args:
    - rgb: A tuple (R, G, B), where R, G, and B are integers between 0 and 255.
    
    Returns:
    - The name of the color corresponding to the given RGB value.
    """
    r, g, b = rgb

    # Check if the color is grayscale (R == G == B)
    if r == g == b:
        if r > 200:
            return "White"
        elif r < 50:
            return "Black"
        else:
            return "Gray"
    
    # Determine color family (hue)
    if r > g and r > b:
        color_family = "Red"
    elif g > r and g > b:
        color_family = "Green"
    elif b > r and b > g:
        color_family = "Blue"
    elif r == g and r > b:
        color_family = "Yellow"  # Yellow is a mix of red and green
    elif g == b and g > r:
        color_family = "Cyan"  # Cyan is a mix of green and blue
    elif r == b and r > g:
        color_family = "Magenta"  # Magenta is a mix of red and blue
    else:
        color_family = "Unknown"
    
    # Handle purple shades (mix of red and blue, low green)
    if r > 100 and b > 100 and g < 100:
        if r > 150 and b > 150:
            return "Purple"  # Strong purple
        elif r < 150 and b < 150:
            return "Lavender"  # Lighter purple
        return "Violet"  # A more reddish purple

    # Check for light or dark blue more specifically
    if color_family == "Blue":
        brightness = (r + g + b) / 3
        if brightness > 180:  # High brightness, likely a light shade
            return "Light Blue"
        elif brightness < 80:  # Low brightness, likely a dark shade
            return "Dark Blue"
        else:  # Otherwise, normal blue
            return "Blue"

    # Handle green shades more specifically
    if color_family == "Green":
        brightness = (r + g + b) / 3  # Using average brightness
        
        # Light green logic: if the green channel is dominant and the brightness is high
        if g > r and g > b and brightness > 180:
            return "Light Green"
        
        # Dark green logic: if the brightness is low
        elif brightness < 80:
            return "Dark Green"
        
        # Otherwise, normal green
        return "Green"
    
    # Brightness and Saturation for other color families
    brightness = (r + g + b) / 3  # Simple average to represent brightness
    if brightness > 200:
        brightness_level = "Light"
    elif brightness < 80:
        brightness_level = "Dark"
    else:
        brightness_level = "Medium"

    # Map to more specific names based on family and brightness
    if color_family == "Red":
        if b < 100 and g < 100:
            return f"Light {color_family}"
        if b > 100 and g > 100:
            return f"Pink"
        return f"Dark {color_family}"
    elif color_family == "Yellow":
        return f"Yellow"
    elif color_family == "Cyan":
        return f"Cyan"
    elif color_family == "Magenta":
        return f"Magenta"
    
    # Return based on brightness and family
    return f"{brightness_level} {color_family}"


