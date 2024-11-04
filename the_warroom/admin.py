from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from django import forms
from .models import Game, Effort, Tournament
from blog.models import Deck, Map, Faction, Vagabond
from the_gatehouse.models import Profile
from django.http import HttpResponseRedirect 
from .forms import GameCreateForm, EffortCreateForm
from django.core.exceptions import ObjectDoesNotExist
import re

class CsvImportForm(forms.Form):
    csv_upload = forms.FileField() 

class EffortAdmin(admin.ModelAdmin):
    list_display = ('date_posted', 'player', 'faction', 'score', 'win', 'game', 'game__league')
    search_fields = ['faction__title', 'player__discord', 'player__dwd', 'player__display_name']

class EffortInline(admin.StackedInline):
    model = Effort
    extra = 0
    # fields = ['player', 'faction', 'score', 'win']

class GameAdmin(admin.ModelAdmin):
    list_display = ('id', 'date_posted', 'deck', 'map', 'type', 'platform', 'league', 'recorder__discord')
    inlines = [EffortInline]
     
    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-game-csv/', self.upload_csv)]
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
                print(len(fields))

                if len(fields) < 22:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields


                random = True
                # Strip whitespace before comparing
                if fields[20].strip() == '':
                    random = False

                # Attempt to find the Map by title
                map_instance = Map.objects.filter(title=fields[18]).first()

                # Attempt to find the Deck by title
                deck_instance = Deck.objects.filter(title=fields[19]).first()

                if "Vagabond" in fields[9].strip():
                    faction = "Vagabond"
                else:
                    faction = fields[9]
                faction_instance = Faction.objects.filter(title=faction).first()

                game_data = {
                    'date_posted': fields[0],
                    'undrafted_faction': faction_instance, #Need reference
                    'map': map_instance,
                    'deck': deck_instance, #Need reference
                    'random_clearing': random,
                    'type': fields[21],
                    'link': fields[22],
                    'platform': 'Root Digital',
                    'league': True,
                }
                # Use GameCreateForm for validation
                form = GameCreateForm(game_data)

                if form.is_valid():
                    game_instance = form.save()




                        
                    for i in range(4):
                        player_instance = Profile.objects.get_or_create(discord=fields[1+i])
                        coalition_faction = None
                        dominance = False
                        win = False
                        found_vb = None
                        if "Vagabond" in fields[5+i].strip():
                            faction = "Vagabond"
                            match = re.search(r'\((.*?)\)', fields[5+i])  # This will search for text inside parentheses
                            if match:
                                found_vb = match.group(1)  # This will get the text inside the parentheses
                                print(found_vb)  # For example, this will print 'Harrier'
                            else:
                                print("No VB found")
                        else:
                            faction = fields[5+i]
                        vagabond_instance = Vagabond.objects.filter(title=found_vb).first()
                        faction_instance = Faction.objects.filter(title=faction).first()
                        if float(fields[14+i]) > 0:
                            win = True
                        if fields[10+i].isnumeric():
                            score = fields[10+i]
                        else:
                            score = None
                            if 'Coalition w/' in fields[10+i]:
                                start_index = fields[10+i].find('Coalition w/') + len('Coalition w/')
                                coalition_faction = fields[10+i][start_index:].strip()
                            else:
                                dominance = True
                        coalition_instance = Faction.objects.filter(title=coalition_faction).first()

                        print(f"Player:{player_instance}, Seat:{1+i}, Faction:{faction_instance}, Vagabond:{vagabond_instance}, Win:{win}, Score:{score}, Coalition Faction:{coalition_instance}, Dominance:{dominance}, Game:{game_instance}")

                        # Record each player's results
                        player_data = {
                            'seat': 1+i,
                            'player': player_instance[0],
                            'faction': faction_instance,
                            'vagabond': vagabond_instance,
                            'win': win,
                            'score': score,
                            'coalition_with': coalition_instance,
                            'dominance': dominance,
                            'game': game_instance,
                        }

                        effort_form = EffortCreateForm(player_data)
                        if effort_form.is_valid():
                            print('Valid Effort')
                            effort_form.save()






                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {x}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)



# Register your models here.
admin.site.register(Game, GameAdmin)
admin.site.register(Effort, EffortAdmin)
admin.site.register(Tournament)
