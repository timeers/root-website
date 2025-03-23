from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.urls import path, reverse
from django.shortcuts import render
from .models import Profile, PlayerBookmark, Theme, BackgroundImage, ForegroundImage
from django import forms
from django.http import HttpResponseRedirect 
from django.db import transaction
from django.utils.translation import gettext_lazy as _


class BackgroundInline(admin.StackedInline):
    model = BackgroundImage
    extra = 0
class ForegroundInline(admin.StackedInline):
    model = ForegroundImage
    extra = 0

class ThemeAdmin(admin.ModelAdmin):
    list_display = ['name']
    inlines = [BackgroundInline, ForegroundInline]

class BackgroundImageAdmin(admin.ModelAdmin):
    list_display = ['name', 'page', 'theme__name', 'image']
    search_fields = ('name', 'page', 'theme__name')

class ForegroundImageAdmin(admin.ModelAdmin):
    list_display = ['name', 'page', 'theme__name', 'location', 'image']
    search_fields = ('name', 'page', 'theme__name')


class CsvImportForm(forms.Form):
    csv_upload = forms.FileField() 



class ProfileAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'discord', 'group', 'theme', 'weird')
    search_fields = ('display_name', 'discord', 'dwd',)
    actions = ['merge_profiles']


    def get_form(self, request, obj = None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        is_superuser = request.user.is_superuser
        # These fields should not be changed
        form.base_fields['user'].disabled = True
        # form.base_fields['discord'].disabled = True
        if not is_superuser:
            form.base_fields['slug'].disabled = True
            if obj and obj.group == 'A':
                # This would only disable the group field
                # form.base_fields['group'].disabled = True

                # This disables all fields of admin profiles. I think it's best to not let admins change other admins.
                for field in form.base_fields.values():
                    field.disabled = True
            else:
                # Limit choices so that new Admins cannot be created
                form.base_fields['group'].choices = [
                    ('O', 'Outcast'),
                    ('P', 'Player'),
                    ('D', 'Designer'),
                    ('B', 'Banned'),
                ]


        return form
    
    @admin.action(description="Merge Profiles")
    def merge_profiles(self, request, queryset):
        users_qs = queryset.exclude(user=None)
        players_to_merge = queryset.filter(user=None)
        players = list(players_to_merge)
        users = list(users_qs)

        if len(users) == 1 and players:
            user = users[0]
            self.merge_user_with_players(request, user, players)
            self.message_user(request, f"Player(s) merged successfully with {user}.")
        elif len(users) > 1:
            messages.error(request, "Only one user may be selected to absorb other players.")
        else:
            # Merge profiles together into the first profile. Cannot select which profile inherits.
            user = players[0]
            merge_players = players[1:]
            self.merge_user_with_players(request, user, merge_players)
            self.message_user(request, f"Player(s) merged successfully with {user}")
            # messages.error(request, "No user was selected to inherit selected players.")

    # This should makes it so that if the full merge is not successful it will undo the changes.
    @transaction.atomic
    def merge_user_with_players(self, request, user, players):
        for player in players:
            efforts = player.efforts.all()
            if efforts.exists():
                for effort in efforts:
                    effort.player = user
                    effort.save()
                self.message_user(request, f"Player {player}'s games merged with {user}.")
            designer_posts = player.posts.all()
            if designer_posts.exists():
                for post in designer_posts:
                    post.designer = user
                    post.save()
                self.message_user(request, f"Designer {player}'s components merged with {user}.")
            artist_posts = player.artist_posts.all()
            if artist_posts.exists():
                for art in artist_posts:
                    art.artist = user
                    art.save()
                self.message_user(request, f"Artist {player}'s art credit merged with {user}.")
            # Delete the player
            player.delete()


    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-profile-csv/', self.upload_csv)]
        return new_urls + urls

    def upload_csv(self, request):

        if request.method == 'POST':
            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info )

            file_data = csv_file.read().decode('utf-8')
            csv_data = file_data.split('\n')

            for x in csv_data:

                fields = x.split(',')
                if fields[0] == '':  # Check to ensure there are enough fields
                    print("No Data in Field")
                    continue  # Skip to the next iteration if not enough fields
                discord_value = fields[0].split('+')[0].lower()
                profile, created = Profile.objects.update_or_create(
                    discord=discord_value,
                    defaults={
                        'dwd': fields[0],
                        'display_name': discord_value
                    }
                )
                # profile.dwd = fields[0]
                # profile.display_name = discord_value
            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)



class CustomUserAdmin(UserAdmin):
    # Customize the fields displayed in the admin interface
    list_display = ('username', 'profile__display_name', 'profile__group', 'profile__weird', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active')
    

    # def get_list_display(self, request):
    #     return [
    #         ('username', 'Username'),
    #         ('profile__display_name', 'Display Name'),
    #         ('profile__group', 'Group'),
    #         ('profile__weird', 'Fan Content'),
    #         ('is_staff', 'Staff Status'),
    #         ('is_active', 'Active Status'),
    #     ]


    # Customize the fieldsets
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name', 'email')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login',)}),
    )
    

    @admin.display(description='Display Name')
    def profile__display_name(self, obj):
        return obj.profile.display_name

    @admin.display(description='Group')
    def profile__group(self, obj):
        # return obj.profile.group
        return obj.profile.get_group_display()
    
    @admin.display(description='Fan Content')
    def profile__weird(self, obj):
        return obj.profile.weird

    # # Optionally override save_model
    # def save_model(self, request, obj, form, change):
    #     # Add any custom behavior here
    #     super().save_model(request, obj, form, change)


class PlayerBookmarkAdmin(admin.ModelAdmin):
    list_display = ('id', 'player', 'friend', 'public')
    search_fields = ['player__discord', 'player__dwd', 'player__display_name', 'friend__discord', 'friend__dwd', 'friend__display_name']

admin.site.register(PlayerBookmark, PlayerBookmarkAdmin)


# Unregister the default User admin
admin.site.unregister(User)
# Register the customized User admin
admin.site.register(User, CustomUserAdmin)





admin.site.register(Profile, ProfileAdmin)
admin.site.register(Theme, ThemeAdmin)
admin.site.register(BackgroundImage, BackgroundImageAdmin)
admin.site.register(ForegroundImage, ForegroundImageAdmin)
