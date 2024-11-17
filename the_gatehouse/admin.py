from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from .models import Profile
from django import forms
from django.http import HttpResponseRedirect 
from django.db import transaction





class CsvImportForm(forms.Form):
    csv_upload = forms.FileField() 

class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'creative', 'display_name', 'discord', 'dwd', 'league')
    search_fields = ('display_name', 'discord', 'dwd',)
    actions = ['merge_profiles']
     
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
            messages.error(request, "No user was selected to inherit selected players.")

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
                discord_value = fields[0].split('+')[0]
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


admin.site.register(Profile, ProfileAdmin)
