from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from django import forms
from .models import Game, Effort, Tournament, GameBookmark, ScoreCard, TurnScore, Round
from the_keep.models import Deck, Map, Faction, Vagabond, Tweak, Landmark, Hireling
from the_gatehouse.models import Profile
from django.http import HttpResponseRedirect 
from .forms import GameImportForm, EffortImportForm
from django.core.exceptions import ObjectDoesNotExist
import re
import csv
from io import StringIO
from datetime import datetime

class CsvImportForm(forms.Form):
    csv_upload = forms.FileField()

class RoundInline(admin.StackedInline):
    model = Round
    fk_name = 'tournament'
    extra = 0

class TournamentAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'platform', 'include_fan_content')
    search_fields = ('name', 'description')
    inlines = [RoundInline]

class RoundAdmin(admin.ModelAdmin):
    list_display = ('name', 'tournament', 'round_number', 'start_date', 'end_date')
    search_fields = ['name', 'tournament__name']

class TurnInline(admin.StackedInline):
    model = TurnScore
    extra = 0

class ScoreCardAdmin(admin.ModelAdmin):
    list_display = ('id', 'turn_count', 'faction__title', 'total_score', 'recorder', 'date_posted', 'final')
    inlines = [TurnInline]

    def turn_count(self, obj):
        return obj.turns.count()  # Count the related TurnScore instances for each ScoreCard

    def total_score(self, obj):
        # Initialize sum
        total = 0
        # Iterate over the turns queryset and sum up the total_points for each turn
        for turn in obj.turns.all():
            total += turn.total_points
        return total

    turn_count.short_description = 'Turns'



class EffortAdmin(admin.ModelAdmin):
    list_display = ('date_posted', 'player', 'faction', 'score', 'win', 'game')
    search_fields = ['faction__title', 'player__discord', 'player__dwd', 'player__display_name']

class EffortInline(admin.StackedInline):
    model = Effort
    extra = 0
    # fields = ['player', 'faction', 'score', 'win']

class GameAdmin(admin.ModelAdmin):
    list_display = ('id', 'nickname', 'recorder', 'date_posted', 'deck', 'map', 'type', 'platform', 'round', 'final')
    search_fields = ['id', 'deck__title', 'map__title', 'type', 'platform', 'efforts__player__discord', 'efforts__faction__title', 'nickname']
    inlines = [EffortInline]
     
    def get_urls(self):
        urls = super().get_urls()
        new_urls = [
            path('upload-game-csv/', self.upload_csv),
            path('upload-weird-root-csv/', self.upload_weird_root_csv)
            ]
        return new_urls + urls

    def upload_csv(self, request):

        if request.method == 'POST':
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info )

            file_data = csv_file.read().decode('utf-8')
            csv_data = file_data.split('\n')

            for x in csv_data:
                fields = x.split(',')
                # print(len(fields))

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
                deck = fields[19]
                if fields[19] == "E&P":
                    deck = "Exiles & Partisans"

                deck_instance = Deck.objects.filter(title=deck).first()
                found_vb = None
                if "Vagabond" in fields[9].strip():
                    faction = "Vagabond"
                    match = re.search(r'\((.*?)\)', fields[9])  # This will search for text inside parentheses
                    if match:
                        found_vb = match.group(1)  # This will get the text inside the parentheses
                    #     print(found_vb)  # For example, this will print 'Harrier'
                    # else:
                    #     print("No VB found")
                else:
                    faction = fields[9]
                vagabond_instance = Vagabond.objects.filter(title=found_vb).first()
                faction_instance = Faction.objects.filter(title=faction).first()

                season_name = fields[23].strip()
                match = re.findall(r'\d+', fields[23])
                

                date_obj = datetime.strptime(fields[0], '%m/%d/%Y %H:%M:%S')

                if season_name:
                    if match:
                        # Join all found digits, then take the last two digits
                        digits = ''.join(match)[-2:]
                        season_number = int(digits)
                    else:
                        # Handle the case where no digits are found (if necessary)
                        season_number = 0 
                    # season_instance = Round.objects.get(name=season_name)
                    try:
                        season_instance = Round.objects.get(name=season_name, tournament__name="Root Digital League")
                    except:
                        try:
                            series = Tournament.objects.get(name="Root Digital League")
                        except:
                            series = Tournament.objects.create(name="Root Digital League")
                        season_instance = Round.objects.create(name=season_name, tournament=series, start_date=date_obj, round_number=season_number)
                else:
                    season_instance = None



                game_data = {
                    'date_posted': date_obj,
                    'undrafted_faction': faction_instance, #Need reference
                    'undrafted_vagabond': vagabond_instance,
                    'map': map_instance,
                    'deck': deck_instance, #Need reference
                    'random_clearing': random,
                    'type': fields[21],
                    'link': fields[22],
                    'platform': 'Root Digital',
                    'official': True,
                    'round': season_instance,
                    'final': True,
                }
                # Use GameCreateForm for validation
                form = GameImportForm(game_data)

                if form.is_valid():
                    game_instance = form.save()




                        
                    for i in range(4):
                        discord_value = fields[1+i].split('+')[0].lower()
                        # player_instance = Profile.objects.get_or_create(discord=fields[1+i])
                        # print(discord_value)
                        # print(fields[1+i])

                        player_instance, _ = Profile.objects.update_or_create(
                            discord=discord_value,
                            defaults={
                                'dwd': fields[1+i],
                                'display_name': fields[1+i].split('+')[0]
                                }
                            )

                        coalition_faction = None
                        dominance = None
                        win = False
                        found_vb = None
                        if "Vagabond" in fields[5+i].strip():
                            faction = "Vagabond"
                            match = re.search(r'\((.*?)\)', fields[5+i])  # This will search for text inside parentheses
                            if match:
                                found_vb = match.group(1)  # This will get the text inside the parentheses
                            #     print(found_vb)  # For example, this will print 'Harrier'
                            # else:
                            #     print("No VB found")
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
                                dominance = fields[10+i].split()[0] 
                                if dominance == 'Bunny':
                                    dominance = 'Rabbit'
                        coalition_instance = Faction.objects.filter(title=coalition_faction).first()

                        # print(f"Player:{player_instance}, Seat:{1+i}, Faction:{faction_instance}, Vagabond:{vagabond_instance}, Win:{win}, Score:{score}, Coalition Faction:{coalition_instance}, Dominance:{dominance}, Game:{game_instance}")

                        # Record each player's results
                        player_data = {
                            'date_posted': fields[0],
                            'seat': 1+i,
                            'player': player_instance,
                            'faction': faction_instance,
                            'vagabond': vagabond_instance,
                            'win': win,
                            'score': score,
                            'coalition_with': coalition_instance,
                            'dominance': dominance,
                            'game': game_instance,
                        }

                        effort_form = EffortImportForm(player_data)
                        if effort_form.is_valid():
                            print('Valid Effort')
                            effort_form.save()
                        else:
                            print('Invalid Effort')
                            print(effort_form.errors) 





                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {x}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)









    def upload_weird_root_csv(self, request):

        if request.method == 'POST':

            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                if fields and fields[0].strip().lower().startswith("submitted by"):
                    continue
                # print(len(fields))

                if len(fields) < 17:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                recorder_instance, created = Profile.objects.get_or_create(
                    discord=fields[0].lower()
                )
                notes = fields[18]
                if notes:
                    notes += " (Imported from WR Playtesting Master Sheet)"
                else:
                    notes = "Imported from WR Playtesting Master Sheet"
                # Attempt to find the Map by title
                map_instance = Map.objects.filter(title__iexact=fields[15]).first()

                if fields[15] and not map_instance:
                   
                    map_instance = Map.objects.filter(title__iexact="Hypercube").first()

                # Attempt to find the Deck by title
                tweaks = None
                if fields[16].strip().lower() == "action!":
                    deck_instance = Deck.objects.filter(title__iexact="Exiles & Partisans").first()
                    tweaks = Tweak.objects.filter(title__iexact="Action!")
                else:
                    deck_instance = Deck.objects.filter(title__iexact=fields[16]).first()

                landmark_list = [item.strip() for item in fields[17].split(',') if item.strip()]
                landmark_qs = Landmark.objects.none()  # start with an empty queryset

                for name in landmark_list:
                    try:
                        matched_landmark = Landmark.objects.get(title__iexact=name)
                        landmark_qs |= Landmark.objects.filter(pk=matched_landmark.pk)
                    except Landmark.DoesNotExist:
                        pass

                hireling_list = [item.strip() for item in fields[19].split(',') if item.strip()]
                hireling_qs = Hireling.objects.none()  # start with an empty queryset

                for name in hireling_list:
                    try:
                        matched_hireling = Hireling.objects.get(title__iexact=name)
                        hireling_qs |= Hireling.objects.filter(pk=matched_hireling.pk)
                    except Hireling.DoesNotExist:
                        pass


                if not map_instance:
                    print(f"Map not found: {fields[15]}")
                if not deck_instance:
                    print(f"Deck not found: {fields[16]}")
                

                # List of potential formats
                date_formats = ['%m/%d/%Y %H:%M:%S', '%m/%d/%y %H:%M:%S', '%m/%d/%Y', '%m/%d/%y']

                # Attempt to parse the date with different formats
                date_obj = None
                for date_format in date_formats:
                    try:
                        date_obj = datetime.strptime(fields[1], date_format)
                        break  # Stop once a format works
                    except ValueError:
                        continue  # Try the next format if this one fails
                year = date_obj.year
                jan_first = datetime(date_obj.year, 1, 1)

                try:
                    series = Tournament.objects.get(name="Weird Root Playtests")
                except Tournament.DoesNotExist:
                    series = Tournament.objects.create(name="Weird Root Playtests")

                season_number = series.rounds.count() + 1
                try:
                    season_instance = Round.objects.get(name=year, tournament=series)
                except:
                    season_instance = Round.objects.create(name=year, tournament=series, start_date=jan_first, round_number=season_number)



                game_data = {
                    'date_posted': date_obj,
                    'map': map_instance,
                    'deck': deck_instance, #Need reference
                    'platform': 'Tabletop Simulator',
                    'round': season_instance,
                    'final': True,
                    'recorder': recorder_instance,
                    'notes': notes,
                    'type': "Live",
                    'tweaks': tweaks,
                    'landmarks': landmark_qs,
                    'hirelings': hireling_qs,
                }
                # Use GameImportForm for validation
                form = GameImportForm(game_data)

                if form.is_valid():
                    game_instance = form.save()
                    seat = 1
                    for i in range(3, 15, 2):
                        # print(fields[i], fields[i+1])
                        coalition_faction = None
                        coalition_instance = None
                        vagabond_instance = None
                        dominance = None
                        win = False
                        found_vb = None
                        try:
                        
                            if fields[i].startswith("Vagabond ") or fields[i].startswith("VagaBuddy ") or fields[i].startswith("Vagabot "):
                                parts = fields[i].split(None, 1)  # Split on first whitespace
                                faction = parts[0]  # "Vagabond"
                                found_vb = parts[1] if len(parts) > 1 else None  # "Scoundrel", or None if missing
                            else:
                                faction = fields[i]
                            vagabond_instance = Vagabond.objects.filter(title__iexact=found_vb).first()
                            faction_instance = Faction.objects.filter(title__iexact=faction).first()
                            if faction and not faction_instance:
                                # print(f'Faction not Found {faction}')
                                faction_instance = Faction.objects.filter(title__iexact="Treetop Utopia").first()
                            # print(f'Faction: {faction_instance}')
                            if fields[i+1].isnumeric():
                                score = int(fields[i+1])
                                if score and score >= 30:
                                    win = True
                            else:
                                score = None
                                if 'CO ' in fields[i+1]:
                                    start_index = fields[i+1].find('CO') + len('CO')
                                    coalition_faction = fields[i+1][start_index:].strip()
                                else:
                                    dominance = fields[i+1].split()[0] 
                                    if dominance == 'Bunny':
                                        dominance = 'Rabbit'
                            # print(f'Score: {score}')
                            coalition_instance = Faction.objects.filter(title__iexact=coalition_faction).first()


                            # Skip empty entries
                            if faction:


                                # Record each player's results
                                player_data = {
                                    'date_posted': date_obj,
                                    'seat': seat,
                                    'faction': faction_instance,
                                    'vagabond': vagabond_instance,
                                    'win': win,
                                    'score': score,
                                    'coalition_with': coalition_instance,
                                    'dominance': dominance,
                                    'game': game_instance,
                                }

                                effort_form = EffortImportForm(player_data)

                                if effort_form.is_valid():
                                    print('Valid Effort')
                                    effort_form.save()
                                else:
                                    print('Invalid Effort')
                                    print(effort_form.errors) 

                                seat = seat + 1


                        except IndexError:
                            break  # Reached the end of fields without a complete pair
                        except ValueError:
                            print(f"Invalid score value at index {i+1}: {fields[i+1]}")
                            continue  # Skip this player if score isn't a valid number


                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)








class GameBookmarkAdmin(admin.ModelAdmin):
    list_display = ('id', 'player', 'game', 'public')
    search_fields = ['player__discord', 'player__dwd', 'player__display_name']    

admin.site.register(GameBookmark, GameBookmarkAdmin)

# Register your models here.
admin.site.register(Game, GameAdmin)
# admin.site.register(Effort, EffortAdmin)
admin.site.register(Tournament, TournamentAdmin)
admin.site.register(Round, RoundAdmin)
admin.site.register(ScoreCard, ScoreCardAdmin)
# admin.site.register(TurnScore)
