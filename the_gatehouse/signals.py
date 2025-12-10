import os
import uuid
from io import BytesIO
from PIL import Image

from django.db.models.signals import post_save, pre_save
from django.shortcuts import redirect
from django.dispatch import receiver
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.files.base import ContentFile
from django.contrib.auth.models import Group

from .models import Profile, ForegroundImage, BackgroundImage
from .services.discordservice import get_discord_display_name, get_discord_id, check_user_guilds, send_discord_message, update_discord_avatar
from .utils import slugify_instance_discord

from the_keep.utils import resize_image_to_webp, delete_old_image
from the_keep.models import Post, Piece, PostTranslation, Map, Deck, Vagabond, Landmark, Hireling, Tweak


@receiver(pre_save, sender=Profile)
def component_pre_save(sender, instance, **kwargs):
    if instance.slug is None:
        slugify_instance_discord(instance, save=False)

@receiver(post_save, sender=Profile)
def component_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_instance_discord(instance, save=True)



@receiver(post_save, sender=User)
def manage_profile(sender, instance, created, **kwargs):
    if created:
        profile, _ = Profile.objects.get_or_create(discord=instance.username, defaults={'user': instance})
        # print(f'Profile created/linked: {profile.discord}')
        # logger.info(f'Profile created/linked: {profile.discord}')
        # send_discord_message(f'Profile created for {profile.discord}', category='user_updates')
    else:
        try:
            profile = instance.profile
        except Profile.DoesNotExist:
            profile, _ = Profile.objects.get_or_create(discord=instance.username)
            profile.user = instance
            profile.save()

    

# This is to put users in the correct groups when created or updated
@receiver(user_logged_in)
def user_logged_in_handler(request, user, **kwargs):
    new_user = False
    # logger.info(f'{user} logged in')
    send_discord_message(f"{user} logged in",category='report')
    send_discord_message(f'{user} logged in')

    if not hasattr(user, 'profile'):
        send_discord_message(f"{user} has no profile, creating",category='report')
        user.save()
        new_user = True
    
    if user.last_login is None:
        send_discord_message(f"{user} first login",category='report')
        new_user = True

    profile = user.profile
    profile_updated = False
    current_group = profile.group
    send_discord_message(f"Checking user guilds",category='report')
    in_ww, in_wr, in_fr = check_user_guilds(user)
    send_discord_message(f"Getting display name",category='report')
    display_name = get_discord_display_name(user)
    send_discord_message(f"Discord user {display_name} logging in",category='report')

    if not profile.discord_id:
        send_discord_message(f"Discord user {display_name} does not have an id",category='report')
        discord_id = get_discord_id(user)
        send_discord_message(f"Discord user {display_name} has id {discord_id}",category='report')
        if discord_id:
            if not Profile.objects.filter(discord_id=discord_id).exists():
                profile.discord_id = discord_id
                profile_updated = True
            else:
                # Handle conflict
                send_discord_message(f"Discord ID {discord_id} already exists for another profile",category='report')
    send_discord_message(f"Discord user {display_name} updating Avatar",category='report')
    update_discord_avatar(user)
    send_discord_message(f"Discord user {display_name} updating Display Name",category='report')
    if display_name and profile.display_name != display_name:
        profile.display_name = display_name
        profile_updated = True
    send_discord_message(f"Discord user {display_name} setting group",category='report')
    # If user is a member of WW but in group O (add to group P)
    if (current_group == 'O' and in_ww) or (current_group == 'O' and in_wr) or (current_group == 'O' and in_fr):
        user_posts = Post.objects.filter(designer=profile)
        if user_posts:
            profile.group = 'E'
        else:
            profile.group = 'P'
        profile_updated = True

    # If user is a member of WR but does not have the weird view (add view)
    if not profile.in_weird_root and in_wr:
        profile.in_weird_root = True
        profile.weird = True
        profile_updated = True

    if not profile.in_french_root and in_fr:
        profile.in_french_root = True
        profile_updated = True

    if not profile.in_woodland_warriors and in_ww:
        profile.in_woodland_warriors = True
        profile_updated = True

    if profile_updated:
        profile.save()

    if new_user:
        send_discord_message(f'Profile created for {profile.discord} ({profile.group})', category='user_updates')
        if profile.group == "O":
            messages.info(request, f'Welcome, {user.profile.display_name}! You can now bookmark posts for quick access. Join the Woodland Warriors Discord and log back in to record games.')
        else:
            messages.info(request, f'Welcome, {user.profile.display_name}! You can now bookmark posts for quick access and record games to track stats.')


    group_name = 'admin'  
    group_exists = user.groups.filter(name=group_name).exists()
    
    # If user is in group A but not in the Admin group (add to group)
    if not group_exists and current_group == "A":
        # Get the group object
        group, created = Group.objects.get_or_create(name=group_name)
        # Add the user to the group
        user.groups.add(group)
        user.is_staff = True
        send_discord_message(f'User {user} added to {group_name}')
        user.save()

    # If user is not in group A but is in the Admin group (remove from group)
    elif group_exists and current_group != "A":
        # Get the group object
        group, created = Group.objects.get_or_create(name=group_name)
        # Add the user to the group
        user.groups.remove(group)
        user.is_staff = False
        send_discord_message(f'User {user} removed from {group_name}')
        user.save()
        



# Define models + field configs somewhere globally
IMAGE_FIELDS_CONFIG = {
    'Post': {
        'fields': {
            'small_icon': 100,
            'picture': 350,
            'card_image': 600,
            'card_2_image': 600,
            'board_image': 1600,
            'board_2_image': 1600,
        }
    },
    'Expansion': {
        'fields': {
            'picture': 1600,
        }
    },
    'PostTranslation': {
        'fields': {
            'translated_card_image': 600,
            'translated_card_2_image': 600,
            'translated_board_image': 1600,
            'translated_board_2_image': 1600,
        }
    },
    'Piece': {
        'fields': {
            'small_icon': 100,
        }
    },
    'BackgroundImage': {
        'fields': {
            'small_image': 768,
            'image': 4096,
            'pattern': 4096,
        }
    },
    'ForegroundImage': {
        'fields': {
            'small_image': 992,
            'image': 4096,
        }
    },
}


@receiver(post_save)
def handle_image_resize_to_webp(sender, instance, **kwargs):
    model_name = sender.__name__
    config = IMAGE_FIELDS_CONFIG.get(model_name)

    # Fallback config for subclasses
    if not config:
        if issubclass(sender, Post):
            config = IMAGE_FIELDS_CONFIG.get('Post')
        elif issubclass(sender, Piece):
            config = IMAGE_FIELDS_CONFIG.get('Piece')
        else:
            return  # Not a model we're handling

    updated = False
    updated_fields = []

    for field_name, max_size in config['fields'].items():
        image_field = getattr(instance, field_name, None)

        if image_field and hasattr(image_field, 'path') and image_field.name:
            new_path = resize_image_to_webp(image_field, max_size)

            if new_path and new_path != image_field.name:
                setattr(instance, field_name, new_path)
                updated = True
                updated_fields.append(field_name)

    if updated:
        # Prevent infinite recursion by updating only modified fields
        instance.save(update_fields=updated_fields)




SMALL_IMAGE_FIELDS = {
    'picture': ('small_picture', 150),
    'board_image': ('small_board_image', 800),
    'card_image': ('small_card_image', 200),
    'board_2_image': ('small_board_2_image', 800),
    'card_2_image': ('small_card_2_image', 200),
}



IMAGE_FIELD_CONFIG = {
    PostTranslation: {
        'fields': {
            'translated_board_image': ('small_board_image', 800),
            'translated_card_image': ('small_card_image', 200),
            'translated_board_2_image': ('small_board_2_image', 800),
            'translated_card_2_image': ('small_card_2_image', 200),
        },
        'flag': '_processed_small_images',
    },
    Map: {
        'fields': {
            'board_image': [
                ('small_icon', 100),
                ('picture', 350),
            ],
        },
        'flag': '_processed_map_images',
    },
    Deck: {
        'fields': {
            'card_image': [
                ('small_icon', 100),
                ('picture', 350),
            ],
        },
        'flag': '_processed_deck_images',
    },
    Vagabond: {
        'fields': {
            'picture': [('small_icon', 100)],
        },
        'flag': '_processed_other_images',
    },
    Hireling: {
        'fields': {
            'picture': [('small_icon', 100)],
        },
        'flag': '_processed_other_images',
    },
    Landmark: {
        'fields': {
            'picture': [('small_icon', 100)],
        },
        'flag': '_processed_other_images',
    },
    Tweak: {
        'fields': {
            'picture': [('small_icon', 100)],
        },
        'flag': '_processed_other_images',
    },
    ForegroundImage: {
        'fields': {
            'image': ('small_image', 992),
        },
        'flag': '_processed_image',
    },
    BackgroundImage: {
        'fields': {
            'image': ('small_image', 992),
        },
        'flag': '_processed_image',
    },
}










# SMALL_TRANS_IMAGE_FIELDS = {
#     'translated_board_image': ('small_board_image', 800),
#     'translated_card_image': ('small_card_image', 200),
#     'translated_board_2_image': ('small_board_2_image', 800),
#     'translated_card_2_image': ('small_card_2_image', 200),
# }

# MAP_IMAGE_FIELDS = {
#     'board_image': [
#         ('small_icon', 100),
#         ('picture', 350),
#     ],
# }

# DECK_IMAGE_FIELDS = {
#     'card_image': [
#         ('small_icon', 100),
#         ('picture', 350),
#     ],
# }

# OTHER_IMAGE_FIELDS = {
#     'picture': [
#         ('small_icon', 100),
#     ],
# }



@receiver(post_save)
def process_small_images(sender, instance, **kwargs):
    config = IMAGE_FIELD_CONFIG.get(sender)
    if not config:
        return

    flag = config.get('flag', '_processed_image')
    if hasattr(instance, flag):
        return

    updated_fields = []

    for original_field, targets in config['fields'].items():
        original_file = getattr(instance, original_field, None)
        if not original_file or not hasattr(original_file, 'path') or not os.path.exists(original_file.path):
            continue

        # Always treat targets as a list
        if not isinstance(targets, list):
            targets = [targets]

        for small_field_name, max_size in targets:
            small_file = getattr(instance, small_field_name, None)

            if small_file and hasattr(small_file, 'path') and os.path.exists(small_file.path):
                orig_time = os.path.getmtime(original_file.path)
                small_time = os.path.getmtime(small_file.path)
                if small_time >= orig_time:
                    continue

            try:
                img = Image.open(original_file.path)
                img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

                if img.width > max_size or img.height > max_size:
                    ratio = min(max_size / img.width, max_size / img.height)
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)

                img_io = BytesIO()
                img.save(img_io, format='WEBP', quality=80)
                img_io.seek(0)

                if small_file and hasattr(small_file, 'path') and os.path.exists(small_file.path):
                    try:
                        delete_old_image(small_file)
                    except Exception as e:
                        print(f"Error deleting old image: {e}")

                unique_name = f"{uuid.uuid4().hex}.webp"
                getattr(instance, small_field_name).save(unique_name, ContentFile(img_io.read()), save=False)
                updated_fields.append(small_field_name)

            except Exception as e:
                print(f"Error processing {small_field_name}: {e}")

    if updated_fields:
        setattr(instance, flag, True)
        instance.save(update_fields=updated_fields)



# @receiver(post_save, sender=ForegroundImage)
# @receiver(post_save, sender=BackgroundImage)
# def generate_small_image(sender, instance, created, **kwargs):
#     image_field = instance.image

#     if not image_field or not hasattr(image_field, 'path') or not os.path.exists(image_field.path):
#         return

#     # If small_image already exists and is newer, skip
#     if instance.small_image and os.path.exists(instance.small_image.path):
#         image_time = os.path.getmtime(image_field.path)
#         small_image_time = os.path.getmtime(instance.small_image.path)
#         if small_image_time >= image_time:
#             return  # Already processed

#     try:
#         img = Image.open(image_field.path)
#         img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

#         # Resize
#         max_size = 992
#         if img.width > max_size or img.height > max_size:
#             ratio = min(max_size / img.width, max_size / img.height)
#             new_size = (int(img.width * ratio), int(img.height * ratio))
#             img = img.resize(new_size, Image.LANCZOS)

#         img_io = BytesIO()
#         img.save(img_io, format='WEBP', quality=80)
#         img_io.seek(0)

#         # Save to model
#         unique_filename = f"{uuid.uuid4().hex}.webp"
#         instance.small_image.save(unique_filename, img_io, save=False)
#         instance.save(update_fields=['small_image'])

#     except Exception as e:
#         print(f"Error processing small image: {e}")




@receiver(post_save)
def generate_small_images(sender, instance, **kwargs):
    if not isinstance(instance, Post):
        return  # Only act on Post or its subclasses
    
    # Prevent recursion
    if hasattr(instance, '_processed_small_images'):
        return

    updated_fields = []
    for original_field_name, (small_field_name, max_size) in SMALL_IMAGE_FIELDS.items():
        original_field = getattr(instance, original_field_name, None)
        small_field = getattr(instance, small_field_name, None)

        if not original_field or not hasattr(original_field, 'path') or not os.path.exists(original_field.path):
            continue

        # Check if small image already exists and is newer
        if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
            image_time = os.path.getmtime(original_field.path)
            small_image_time = os.path.getmtime(small_field.path)
            if small_image_time >= image_time:
                continue  # Already processed

        try:
            img = Image.open(original_field.path)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

            # Resize if necessary
            if img.width > max_size or img.height > max_size:
                ratio = min(max_size / img.width, max_size / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            # Save to memory
            img_io = BytesIO()
            img.save(img_io, format='WEBP', quality=80)
            img_io.seek(0)

            # 🧹 Delete the old small image file (if it exists)
            if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
                try:
                    # os.remove(small_field.path)
                    delete_old_image(small_field)
                    print(f"Deleted old small image: {small_field.path}")
                except Exception as e:
                    print(f"Could not delete old small image: {e}")


            # Generate unique name and save to small field
            unique_filename = f"{uuid.uuid4().hex}.webp"
            getattr(instance, small_field_name).save(unique_filename, ContentFile(img_io.read()), save=False)
            updated_fields.append(small_field_name)


        except Exception as e:
            print(f"Error processing {small_field_name}: {e}")

    # # Save all updated fields at once
    # instance.save(update_fields=[small for _, (small, _) in SMALL_IMAGE_FIELDS.items()])
    if updated_fields:
        instance._processed_small_images = True  # Prevent recursion
        instance.save(update_fields=updated_fields)



# @receiver(post_save, sender=PostTranslation)
# def generate_translated_small_images(sender, instance, **kwargs):
    
#     # Prevent recursion
#     if hasattr(instance, '_processed_small_images'):
#         return

#     updated_fields = []
#     for original_field_name, (small_field_name, max_size) in SMALL_TRANS_IMAGE_FIELDS.items():
#         original_field = getattr(instance, original_field_name, None)
#         small_field = getattr(instance, small_field_name, None)

#         if not original_field or not hasattr(original_field, 'path') or not os.path.exists(original_field.path):
#             continue

#         # Check if small image already exists and is newer
#         if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#             image_time = os.path.getmtime(original_field.path)
#             small_image_time = os.path.getmtime(small_field.path)
#             if small_image_time >= image_time:
#                 continue  # Already processed

#         try:
#             img = Image.open(original_field.path)
#             img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

#             # Resize if necessary
#             if img.width > max_size or img.height > max_size:
#                 ratio = min(max_size / img.width, max_size / img.height)
#                 new_size = (int(img.width * ratio), int(img.height * ratio))
#                 img = img.resize(new_size, Image.LANCZOS)

#             # Save to memory
#             img_io = BytesIO()
#             img.save(img_io, format='WEBP', quality=80)
#             img_io.seek(0)

#             # 🧹 Delete the old small image file (if it exists)
#             if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                 try:
#                     # os.remove(small_field.path)
#                     delete_old_image(small_field)
#                     print(f"Deleted old small image: {small_field.path}")
#                 except Exception as e:
#                     print(f"Could not delete old small image: {e}")


#             # Generate unique name and save to small field
#             unique_filename = f"{uuid.uuid4().hex}.webp"
#             getattr(instance, small_field_name).save(unique_filename, ContentFile(img_io.read()), save=False)
#             updated_fields.append(small_field_name)


#         except Exception as e:
#             print(f"Error processing {small_field_name}: {e}")

#     # # Save all updated fields at once
#     # instance.save(update_fields=[small for _, (small, _) in SMALL_IMAGE_FIELDS.items()])
#     if updated_fields:
#         instance._processed_small_images = True  # Prevent recursion
#         instance.save(update_fields=updated_fields)




# @receiver(post_save, sender=Map)
# def generate_map_images(sender, instance, **kwargs):
    
#     # Prevent recursion
#     if hasattr(instance, '_processed_map_images'):
#         return
#     print('processing map')
#     updated_fields = []
#     for original_field_name, targets in MAP_IMAGE_FIELDS.items():
#         original_field = getattr(instance, original_field_name, None)

#         if not original_field or not hasattr(original_field, 'path') or not os.path.exists(original_field.path):
#             continue

#         for small_field_name, max_size in targets:
#             small_field = getattr(instance, small_field_name, None)

#             # Check if small image already exists and is newer
#             if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                 image_time = os.path.getmtime(original_field.path)
#                 small_image_time = os.path.getmtime(small_field.path)
#                 if small_image_time >= image_time:
#                     continue  # Already processed

#             try:
#                 img = Image.open(original_field.path)
#                 img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

#                 # Resize if necessary
#                 if img.width > max_size or img.height > max_size:
#                     ratio = min(max_size / img.width, max_size / img.height)
#                     new_size = (int(img.width * ratio), int(img.height * ratio))
#                     img = img.resize(new_size, Image.LANCZOS)

#                 # Save to memory
#                 img_io = BytesIO()
#                 img.save(img_io, format='WEBP', quality=80)
#                 img_io.seek(0)

#                 if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                     try:
#                         delete_old_image(small_field)
#                     except Exception as e:
#                         print(f"Could not delete old small image: {e}")

#                 unique_filename = f"{uuid.uuid4().hex}.webp"
#                 getattr(instance, small_field_name).save(unique_filename, ContentFile(img_io.read()), save=False)
#                 updated_fields.append(small_field_name)

#             except Exception as e:
#                 print(f"Error processing {small_field_name}: {e}")


#     # # Save all updated fields at once
#     # instance.save(update_fields=[small for _, (small, _) in SMALL_IMAGE_FIELDS.items()])
#     if updated_fields:
#         instance._processed_map_images = True  # Prevent recursion
#         instance.save(update_fields=updated_fields)




# @receiver(post_save, sender=Deck)
# def generate_deck_images(sender, instance, **kwargs):
    
#     # Prevent recursion
#     if hasattr(instance, '_processed_deck_images'):
#         return
#     print('processing deck')
#     updated_fields = []
#     for original_field_name, targets in DECK_IMAGE_FIELDS.items():
#         original_field = getattr(instance, original_field_name, None)

#         if not original_field or not hasattr(original_field, 'path') or not os.path.exists(original_field.path):
#             continue

#         for small_field_name, max_size in targets:
#             small_field = getattr(instance, small_field_name, None)

#             # Check if small image already exists and is newer
#             if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                 image_time = os.path.getmtime(original_field.path)
#                 small_image_time = os.path.getmtime(small_field.path)
#                 if small_image_time >= image_time:
#                     continue  # Already processed

#             try:
#                 img = Image.open(original_field.path)
#                 img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

#                 # Resize if necessary
#                 if img.width > max_size or img.height > max_size:
#                     ratio = min(max_size / img.width, max_size / img.height)
#                     new_size = (int(img.width * ratio), int(img.height * ratio))
#                     img = img.resize(new_size, Image.LANCZOS)

#                 # Save to memory
#                 img_io = BytesIO()
#                 img.save(img_io, format='WEBP', quality=80)
#                 img_io.seek(0)

#                 if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                     try:
#                         delete_old_image(small_field)
#                     except Exception as e:
#                         print(f"Could not delete old small image: {e}")

#                 unique_filename = f"{uuid.uuid4().hex}.webp"
#                 getattr(instance, small_field_name).save(unique_filename, ContentFile(img_io.read()), save=False)
#                 updated_fields.append(small_field_name)

#             except Exception as e:
#                 print(f"Error processing {small_field_name}: {e}")


#     # # Save all updated fields at once
#     # instance.save(update_fields=[small for _, (small, _) in SMALL_IMAGE_FIELDS.items()])
#     if updated_fields:
#         instance._processed_deck_images = True  # Prevent recursion
#         instance.save(update_fields=updated_fields)



# @receiver(post_save, sender=Vagabond)
# @receiver(post_save, sender=Hireling)
# @receiver(post_save, sender=Landmark)
# @receiver(post_save, sender=Tweak)
# def generate_other_images(sender, instance, **kwargs):
    
#     # Prevent recursion
#     if hasattr(instance, '_processed_other_images'):
#         return
#     print('processing other')
#     updated_fields = []
#     for original_field_name, targets in OTHER_IMAGE_FIELDS.items():
#         original_field = getattr(instance, original_field_name, None)

#         if not original_field or not hasattr(original_field, 'path') or not os.path.exists(original_field.path):
#             continue

#         for small_field_name, max_size in targets:
#             small_field = getattr(instance, small_field_name, None)

#             # Check if small image already exists and is newer
#             if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                 image_time = os.path.getmtime(original_field.path)
#                 small_image_time = os.path.getmtime(small_field.path)
#                 if small_image_time >= image_time:
#                     continue  # Already processed

#             try:
#                 img = Image.open(original_field.path)
#                 img = img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB")

#                 # Resize if necessary
#                 if img.width > max_size or img.height > max_size:
#                     ratio = min(max_size / img.width, max_size / img.height)
#                     new_size = (int(img.width * ratio), int(img.height * ratio))
#                     img = img.resize(new_size, Image.LANCZOS)

#                 # Save to memory
#                 img_io = BytesIO()
#                 img.save(img_io, format='WEBP', quality=80)
#                 img_io.seek(0)

#                 if small_field and hasattr(small_field, 'path') and os.path.exists(small_field.path):
#                     try:
#                         delete_old_image(small_field)
#                     except Exception as e:
#                         print(f"Could not delete old small image: {e}")

#                 unique_filename = f"{uuid.uuid4().hex}.webp"
#                 getattr(instance, small_field_name).save(unique_filename, ContentFile(img_io.read()), save=False)
#                 updated_fields.append(small_field_name)

#             except Exception as e:
#                 print(f"Error processing {small_field_name}: {e}")


#     # # Save all updated fields at once
#     # instance.save(update_fields=[small for _, (small, _) in SMALL_IMAGE_FIELDS.items()])
#     if updated_fields:
#         instance._processed_other_images = True  # Prevent recursion
#         instance.save(update_fields=updated_fields)