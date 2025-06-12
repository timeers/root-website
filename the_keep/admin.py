from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from django.http import HttpResponseRedirect 
import csv
from io import StringIO
from .models import (Map, Deck, Landmark, Vagabond, Hireling, Faction, Expansion,
                     PostBookmark, Piece, Tweak, PNPAsset, PostTranslation, FAQ, LawGroup, Law)
from .forms import (FactionImportForm, MapImportForm, VagabondImportForm, DeckImportForm,
                   PieceImportForm, LandmarkImportForm, HirelingImportForm)
from the_gatehouse.models import Profile
from the_warroom.admin import CsvImportForm
from datetime import datetime
from django.utils import timezone

class PieceInline(admin.StackedInline):
    model = Piece
    extra = 0

class PostTranslationInline(admin.StackedInline):
    model = PostTranslation
    extra = 0

class FAQAdmin(admin.ModelAdmin):
    list_display = ('post__title', 'question', 'language')
    search_fields = ('post__title', 'question')

class LawGroupAdmin(admin.ModelAdmin):
    list_display = ('abbreviation', 'title', 'type', 'position')
    search_fields = ('post__title', 'title', 'abbreviation')

class LawAdmin(admin.ModelAdmin):
    list_display = ('law_code', 'title', 'group__post', 'language')
    search_fields = ('group__post__title', 'title', 'law_code')

class TranslationAdmin(admin.ModelAdmin):
    list_display = ('translated_title', 'language', 'post__title', 'post__language')
    search_fields = ('translated_title', 'post__title')

class PNPAssetAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'date_updated', 'shared_by__display_name', 'pinned')
    search_fields = ('title', 'description')

class MapAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status', 'clearings')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]

    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-map-csv/', self.upload_map_csv)]
        return new_urls + urls

    def upload_map_csv(self, request):

        if request.method == 'POST':
            print("action is posted")


            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                print(len(fields))

                if len(fields) < 13:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                # profile_instance, _ = Profile.objects.get_or_create(discord=fields[3].lower())
                profile_instance, _ = Profile.objects.update_or_create(
                    discord=fields[3].lower(),
                    defaults={
                        'display_name': fields[3]
                        }
                    )
                
                if fields[4] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[4])

                if fields[15].strip() != "":
                    artist_instance, _ = Profile.objects.get_or_create(discord=fields[15].strip().lower())
                else:
                    artist_instance = None
                lore = None if fields[14] == '' else fields[14]
                in_root_digital = True if fields[16] == 'TRUE' else False


                status = fields[6]
                official = True if fields[5] == "Y" else False
                
                description = None if fields[8] == '' else fields[8]
                bgg_link = None if fields[9] == '' else fields[9]
                tts_link = None if fields[10] == '' else fields[10]
                ww_link = None if fields[11] == '' else fields[11]
                wr_link = None if fields[12] == '' else fields[12]
                pnp_link = None if fields[13] == '' else fields[13]
                leder_games_link = None if fields[17] == '' else fields[17]
                clearings = int(fields[7])

                if fields[2] != "":
                    year_string = fields[2]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 
                map_data = {
                    'title': fields[1],
                    'clearings': clearings,
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    'description': description,
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'lore': lore,
                    'leder_games_link': leder_games_link,
                }

               
                form = MapImportForm(map_data)

                if form.is_valid():
                    # form.save()
                    form.save()

                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)


class DeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status', 'card_total')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]

    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-deck-csv/', self.upload_deck_csv)]
        return new_urls + urls

    def upload_deck_csv(self, request):

        if request.method == 'POST':

            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                if len(fields) < 12:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                profile_instance, _ = Profile.objects.get_or_create(discord=fields[3].lower())
                if fields[4] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[4])
                    print(expansion_instance)
                status = fields[6]
                official = True if fields[5] == "Y" else False
                bgg_link = None if fields[8] == '' else fields[8]
                tts_link = None if fields[9] == '' else fields[9]
                ww_link = None if fields[10] == '' else fields[10]
                wr_link = None if fields[11] == '' else fields[11]
                pnp_link = None if fields[12] == '' else fields[12]
                leder_games_link = None if fields[16] == '' else fields[16]
                card_total = int(fields[7])

                if fields[15].strip() != "":
                    artist_instance, _ = Profile.objects.get_or_create(discord=fields[15].strip().lower())
                else:
                    artist_instance = None
                lore = None if fields[13] == '' else fields[13]
                in_root_digital = True if fields[14] == 'TRUE' else False



                if fields[2] != "":
                    year_string = fields[2]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 
                deck_data = {
                    'title': fields[1],
                    'card_total': card_total,
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'lore': lore,
                    'leder_games_link': leder_games_link,
                }

               
                form = DeckImportForm(deck_data)

                if form.is_valid():
                    # form.save()
                    form.save()
                    
                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        print(data)
        return render(request, 'admin/csv_upload.html', data)

class TweakAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'based_on', 'official', 'status')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]

class LandmarkAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]

    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-landmark-csv/', self.upload_landmark_csv)]
        return new_urls + urls

    def upload_landmark_csv(self, request):

        if request.method == 'POST':
            print("action is posted")


            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                print(len(fields))

                if len(fields) < 13:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                # profile_instance, _ = Profile.objects.get_or_create(discord=fields[3].lower())
                profile_instance, _ = Profile.objects.update_or_create(
                    discord=fields[3].lower(),
                    defaults={
                        'display_name': fields[3]
                        }
                    )
                
                if fields[4] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[4])

                # if fields[15].strip() != "":
                #     artist_instance, _ = Profile.objects.get_or_create(discord=fields[15].strip().lower())
                # else:
                #     artist_instance = None
                lore = None if fields[13] == '' else fields[13]
                in_root_digital = True if fields[14] == 'TRUE' else False
              

                status = fields[6]
                official = True if fields[5] == "Y" else False
                
                # description = None if fields[8] == '' else fields[8]
                bgg_link = None if fields[8] == '' else fields[8]
                tts_link = None if fields[9] == '' else fields[10]
                ww_link = None if fields[10] == '' else fields[10]
                wr_link = None if fields[11] == '' else fields[11]
                pnp_link = None if fields[12] == '' else fields[12]
                leder_games_link = None if fields[15] == '' else fields[15]
                card_text = None if fields[7] == '' else fields[7]

                if fields[2] != "":
                    year_string = fields[2]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 
                landmark_data = {
                    'title': fields[1],
                    'card_text': card_text,
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    # 'description': description,
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    # 'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'lore': lore,
                    'leder_games_link': leder_games_link,
                }

               
                form = LandmarkImportForm(landmark_data)

                if form.is_valid():
                    # form.save()
                    form.save()

                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)





class HirelingAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status', 'animal', 'other_side__title')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]

    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-hireling-csv/', self.upload_hireling_csv)]
        return new_urls + urls

    def upload_hireling_csv(self, request):

        if request.method == 'POST':
            print("action is posted")


            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                print(len(fields))

                if len(fields) < 17:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                # profile_instance, _ = Profile.objects.get_or_create(discord=fields[3].lower())
                profile_instance, _ = Profile.objects.update_or_create(
                    discord=fields[3].lower(),
                    defaults={
                        'display_name': fields[3]
                        }
                    )
                
                if fields[4] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[4])

                if fields[16].strip() != "":
                    artist_instance, _ = Profile.objects.get_or_create(discord=fields[16].strip().lower())
                else:
                    artist_instance = None
                lore = None if fields[15] == '' else fields[15]
                in_root_digital = True if fields[17] == 'TRUE' else False

                status = fields[6]
                official = True if fields[5] == "Y" else False
                
                if fields[9].strip() != "":
                    try:
                        based_instance = Faction.objects.get(title=fields[9])
                    except:
                        based_instance = None
                else:
                    based_instance = None

                # description = None if fields[8] == '' else fields[8]
                bgg_link = None if fields[10] == '' else fields[10]
                tts_link = None if fields[11] == '' else fields[11]
                ww_link = None if fields[12] == '' else fields[12]
                wr_link = None if fields[13] == '' else fields[12]
                pnp_link = None if fields[14] == '' else fields[14]
                leder_games_link = None if fields[18] == '' else fields[18]
                hireling_type = fields[7]
                animal = fields[8]

                if fields[2] != "":
                    year_string = fields[2]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 
                hireling_data = {
                    'title': fields[1],
                    'type': hireling_type,
                    'animal': animal,
                    'based_on': based_instance,
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'lore': lore,
                    'leder_games_link': leder_games_link,
                }

               
                form = HirelingImportForm(hireling_data)

                if form.is_valid():
                    # form.save()
                    form.save()

                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)





class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status')
    search_fields = ['title']
    raw_id_fields = ['designer']

class ExpansionAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer')
    search_fields = ['title']
    raw_id_fields = ['designer']
    
class FactionAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status', 'type', 'reach', 'animal')
    search_fields = ['title']
    raw_id_fields = ['designer', 'artist']
    inlines = [PieceInline, PostTranslationInline]



    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-faction-csv/', self.upload_faction_csv)]
        return new_urls + urls


    #### DON'T LOOK..... THIS ISN'T PRETTY......
    def upload_faction_csv(self, request):

        if request.method == 'POST':
            # print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info )

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                print(len(fields))

                if len(fields) < 58:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                profile_instance, _ = Profile.objects.get_or_create(discord=fields[4].strip().lower())
                if fields[5] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[5])
                profile_instance.save()
                status = fields[7]
                official = True if fields[6] == "Y" else False

                bgg_link = None if fields[54] == '' else fields[54]
                tts_link = None if fields[55] == '' else fields[55]
                ww_link = None if fields[56] == '' else fields[56]
                wr_link = None if fields[57] == '' else fields[57]
                pnp_link = None if fields[58] == '' else fields[58]
                leder_games_link = None if fields[61] == '' else fields[61]
                reach = int(fields[9])

                if fields[2] != "":
                    year_string = fields[2]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 

                lore = None if fields[59] == '' else fields[59]
                if fields[60].strip() != "":
                    artist_instance, _ = Profile.objects.get_or_create(discord=fields[60].strip().lower())
                else:
                    artist_instance = None
                in_root_digital = True if fields[3] == 'TRUE' else False

                if fields[15] != "":
                    color = fields[15]
                else:
                    color = None

                if fields[19].strip() != "":
                    try:
                        based_instance = Faction.objects.get(title=fields[19])
                    except:
                        based_instance = None
                else:
                    based_instance = None

                faction_data = {
                    'title': fields[1],
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    'type': fields[8],
                    'reach': reach,
                    'complexity': fields[10],
                    'card_wealth': fields[11],
                    'aggression': fields[12],
                    'crafting_ability': fields[13],
                    'animal': fields[16],
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    'lore': lore,
                    'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'color': color,
                    'based_on': based_instance,
                    'leder_games_link': leder_games_link,
                }

                # Use FactionImportForm for validation
                form = FactionImportForm(faction_data)

                if form.is_valid():
                    # form.save()
                    faction_instance = form.save()


                    #Cards
                    if fields[44] != "" and fields[44] != "0":
                        print(f"Name:{'Card'}, Quantity:{fields[44]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        card_data = {
                            'name': "Card",
                            'quantity': int(fields[44]),
                            'parent': faction_instance,
                            'type': 'C'
                        }

                        card_form = PieceImportForm(card_data)
                        if card_form.is_valid():
                            print('Valid Effort')
                            card_form.save()
                        else:
                            print(f"Invalid form data: {card_form.errors}") 

                    #Warrior 1
                    if fields[21] != "" and fields[21] != "0":
                        print(f"Name:{fields[20]}, Quantity:{fields[21]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        warrior_data = {
                            'name': fields[20],
                            'quantity': int(fields[21]),
                            'parent': faction_instance,
                            'type': 'W',
                        }

                        warrior_form = PieceImportForm(warrior_data)
                        if warrior_form.is_valid():
                            print('Valid Warrior')
                            warrior_form.save()
                        else:
                            print(f"Invalid form data: {warrior_form.errors}") 
                    # Warrior 2
                    if fields[23] != "" and fields[23] != "0":
                        print(f"Name:{fields[22]}, Quantity:{fields[23]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        warrior_data = {
                            'name': fields[22],
                            'quantity': int(fields[23]),
                            'parent': faction_instance,
                            'type': 'W',
                        }

                        warrior_form = PieceImportForm(warrior_data)
                        if warrior_form.is_valid():
                            print('Valid Warrior')
                            warrior_form.save()
                        else:
                            print(f"Invalid form data: {warrior_form.errors}") 
                    # Warrior 3
                    if fields[25] != "" and fields[25] != "0":
                        print(f"Name:{fields[24]}, Quantity:{fields[25]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        warrior_data = {
                            'name': fields[24],
                            'quantity': int(fields[25]),
                            'parent': faction_instance,
                            'type': 'W',
                        }

                        warrior_form = PieceImportForm(warrior_data)
                        if warrior_form.is_valid():
                            print('Valid Warrior')
                            warrior_form.save()
                        else:
                            print(f"Invalid form data: {warrior_form.errors}") 

                    #Building 1
                    if fields[28] != "" and fields[28] != "0":
                        suited = True if fields[29] == "TRUE" else False
                        print(f"Name:{fields[27]}, Quantity:{fields[28]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        building_data = {
                            'name': fields[27],
                            'quantity': int(fields[28]),
                            'parent': faction_instance,
                            'suited': suited,
                            'type': 'B',
                        }

                        building_form = PieceImportForm(building_data)
                        if building_form.is_valid():
                            print('Valid Building')
                            building_form.save()
                        else:
                            print(f"Invalid form data: {building_form.errors}") 
                    # Building 2
                    if fields[31] != "" and fields[31] != "0":
                        print(f"Name:{fields[30]}, Quantity:{fields[31]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        building_data = {
                            'name': fields[30],
                            'quantity': int(fields[31]),
                            'parent': faction_instance,
                            'type': 'B',
                        }

                        building_form = PieceImportForm(building_data)
                        if building_form.is_valid():
                            print('Valid Building')
                            building_form.save()
                        else:
                            print(f"Invalid form data: {building_form.errors}") 
                    # Building 3
                    if fields[33] != "" and fields[33] != "0":
                        print(f"Name:{fields[32]}, Quantity:{fields[33]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        building_data = {
                            'name': fields[32],
                            'quantity': int(fields[33]),
                            'parent': faction_instance,
                            'type': 'B',
                        }

                        building_form = PieceImportForm(building_data)
                        if building_form.is_valid():
                            print('Valid Building')
                            building_form.save()
                        else:
                            print(f"Invalid form data: {building_form.errors}") 


                    #Token 1
                    if fields[36] != "" and fields[36] != "0":
                        suited = True if fields[37] == "TRUE" else False
                        print(f"Name:{fields[35]}, Quantity:{fields[36]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        token_data = {
                            'name': fields[35],
                            'quantity': int(fields[36]),
                            'parent': faction_instance,
                            'suited': suited,
                            'type': 'T',
                        }

                        token_form = PieceImportForm(token_data)
                        if token_form.is_valid():
                            print('Valid Token')
                            token_form.save()
                        else:
                            print(f"Invalid form data: {token_form.errors}") 
                    # Token 2
                    if fields[39] != "" and fields[39] != "0":
                        print(f"Name:{fields[38]}, Quantity:{fields[39]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        token_data = {
                            'name': fields[38],
                            'quantity': int(fields[39]),
                            'parent': faction_instance,
                            'type': 'T',
                        }

                        token_form = PieceImportForm(token_data)
                        if token_form.is_valid():
                            print('Valid Token')
                            token_form.save()
                        else:
                            print(f"Invalid form data: {token_form.errors}") 
                    # Token 3
                    if fields[41] != "" and fields[41] != "0":
                        print(f"Name:{fields[40]}, Quantity:{fields[41]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        token_data = {
                            'name': fields[40],
                            'quantity': int(fields[41]),
                            'parent': faction_instance,
                            'type': 'T',
                        }

                        token_form = PieceImportForm(token_data)
                        if token_form.is_valid():
                            print('Valid Token')
                            token_form.save()
                        else:
                            print(f"Invalid form data: {token_form.errors}") 
                    # Token 4
                    if fields[43] != "" and fields[43] != "0":
                        print(f"Name:{fields[42]}, Quantity:{fields[43]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        token_data = {
                            'name': fields[42],
                            'quantity': int(fields[43]),
                            'parent': faction_instance,
                            'type': 'T',
                        }

                        token_form = PieceImportForm(token_data)
                        if token_form.is_valid():
                            print('Valid Token')
                            token_form.save()
                        else:
                            print(f"Invalid form data: {token_form.errors}") 




                    # OtherPiece 1
                    if fields[47] != "" and fields[47] != "0":
                        print(f"Name:{fields[46]}, Quantity:{fields[47]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        other_piece_data = {
                            'name': fields[46],
                            'quantity': int(fields[47]),
                            'parent': faction_instance,
                            'type': 'O',
                        }

                        other_piece_form = PieceImportForm(other_piece_data)
                        if other_piece_form.is_valid():
                            print('Valid OtherPiece')
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 
                    # OtherPiece 2
                    if fields[49] != "" and fields[49] != "0":
                        print(f"Name:{fields[48]}, Quantity:{fields[49]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        other_piece_data = {
                            'name': fields[48],
                            'quantity': int(fields[49]),
                            'parent': faction_instance,
                            'type': 'O',
                        }

                        other_piece_form = PieceImportForm(other_piece_data)
                        if other_piece_form.is_valid():
                            print('Valid OtherPiece')
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 
                    # OtherPiece 3
                    if fields[51] != "" and fields[51] != "0":
                        print(f"Name:{fields[50]}, Quantity:{fields[51]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        other_piece_data = {
                            'name': fields[50],
                            'quantity': int(fields[51]),
                            'parent': faction_instance,
                            'type': 'O',
                        }

                        other_piece_form = PieceImportForm(other_piece_data)
                        if other_piece_form.is_valid():
                            print('Valid OtherPiece')
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 
                    # OtherPiece 4
                    if fields[53] != "" and fields[53] != "0":
                        print(f"Name:{fields[52]}, Quantity:{fields[53]}, Description:{None}, Faction:{faction_instance}")

                        # Record each player's results
                        other_piece_data = {
                            'name': fields[52],
                            'quantity': int(fields[53]),
                            'parent': faction_instance,
                            'type': 'O',
                        }

                        other_piece_form = PieceImportForm(other_piece_data)
                        if other_piece_form.is_valid():
                            print('Valid OtherPiece')
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 






                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)




class VagabondAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'status', 'animal')
    search_fields = ['title']
    raw_id_fields = ['designer']
    inlines = [PieceInline, PostTranslationInline]
    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-vagabond-csv/', self.upload_vagabond_csv)]
        return new_urls + urls

    def upload_vagabond_csv(self, request):

        if request.method == 'POST':
            print("action is posted")


            print("action is posted")
            csv_file = request.FILES['csv_upload']

            if not csv_file.name.endswith('.csv'):
                messages.warning(request, 'Wrong file type was uploaded')
                return HttpResponseRedirect(request.path_info)

            file_data = csv_file.read().decode('utf-8')
            
            # Use StringIO to treat the string as a file
            csv_reader = csv.reader(StringIO(file_data))
            
            for fields in csv_reader:
                print(len(fields))

                if len(fields) < 22:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                # profile_instance, _ = Profile.objects.get_or_create(discord=fields[2])

                profile_instance, _ = Profile.objects.update_or_create(
                    discord=fields[2].lower(),
                    defaults={
                        'display_name': fields[2]
                        }
                    )


                lore = None if fields[25] == '' else fields[25]
                if fields[26].strip() != "":
                    artist_instance, _ = Profile.objects.get_or_create(discord=fields[26].strip().lower())
                else:
                    artist_instance = None
                in_root_digital = True if fields[27] == 'TRUE' else False


                if fields[3] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[3])
                status = fields[4]
                official = True if fields[5] == "Y" else False
                ability_item = 'None' if fields[17] == '' else fields[17]
                ability = None if fields[18] == '' else fields[18]
                ability_description = None if fields[19] == '' else fields[19]
                bgg_link = None if fields[20] == '' else fields[20]
                tts_link = None if fields[21] == '' else fields[21]
                ww_link = None if fields[22] == '' else fields[22]
                wr_link = None if fields[23] == '' else fields[23]
                pnp_link = None if fields[24] == '' else fields[24]
                leder_games_link = None if fields[28] == '' else fields[28]

                if fields[1] != "":
                    year_string = fields[1]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 
                torch = 0 if fields[7] == '' else int(fields[7])
                bag = 0 if fields[8] == '' else int(fields[8])
                boot = 0 if fields[9] == '' else int(fields[9])
                bow = 0 if fields[10] == '' else int(fields[10])
                hammer = 0 if fields[11] == '' else int(fields[11])
                sword = 0 if fields[12] == '' else int(fields[12])
                tea = 0 if fields[13] == '' else int(fields[13])
                coins = 0 if fields[14] == '' else int(fields[14])
                # print(fields[0])
                # print(isinstance(torch, int))
                # print(isinstance(bag, int))
                # print(isinstance(boot, int))
                # print(isinstance(bow, int))
                # print(isinstance(hammer, int))
                # print(isinstance(sword, int))
                # print(isinstance(tea, int))
                # print(isinstance(coins, int))
                # print(f'torch:{torch} bag:{bag} boot:{boot} bow:{bow} hammer:{hammer} sword:{sword} tea:{tea} coins:{coins}')


                vagabond_data = {
                    'title': fields[0],
                    'designer': profile_instance,
                    'expansion': expansion_instance,
                    'animal': fields[6],
                    'starting_torch': torch,
                    'starting_bag': bag,
                    'starting_boots': boot,
                    'starting_crossbow': bow,
                    'starting_hammer': hammer,
                    'starting_sword': sword,
                    'starting_tea': tea,
                    'starting_coins': coins,
                    'ability_item': ability_item,
                    'ability': ability,
                    'ability_description': ability_description,
                    'bgg_link': bgg_link,
                    'tts_link': tts_link,
                    'ww_link': ww_link,
                    'wr_link': wr_link,
                    'pnp_link': pnp_link,
                    'status': status,
                    'official': official,
                    'date_posted': date_posted,
                    'artist': artist_instance,
                    'in_root_digital': in_root_digital,
                    'lore': lore,
                    'leder_games_link': leder_games_link,
                }

                # Use FactionImportForm for validation
                form = VagabondImportForm(vagabond_data)

                if form.is_valid():
                    # form.save()
                    vagabond_instance = form.save()



                    # OtherPiece 1
                    if fields[15] != "" and fields[15] != "0":
                        print(f"Name:{fields[16]}, Quantity:{fields[15]}, Description:{None}, VB:{vagabond_instance}")

                        # Record each player's results
                        other_piece_data = {
                            'name': fields[16],
                            'quantity': int(fields[15]),
                            'parent': vagabond_instance,
                            'type': 'O',
                        }

                        other_piece_form = PieceImportForm(other_piece_data)
                        if other_piece_form.is_valid():
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 

                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {fields}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)





class PostBookmarkAdmin(admin.ModelAdmin):
    list_display = ('id', 'player', 'post', 'public')
    search_fields = ['post__title', 'player__discord', 'player__dwd', 'player__display_name']

admin.site.register(PostBookmark, PostBookmarkAdmin)


admin.site.register(PNPAsset, PNPAssetAdmin)


# Registering the models with the GroupedModelAdmin

# admin.site.register(Post, PostAdmin)
admin.site.register(Map, MapAdmin)
admin.site.register(Deck, DeckAdmin)
admin.site.register(Tweak, TweakAdmin)
admin.site.register(Landmark, LandmarkAdmin)
admin.site.register(Vagabond, VagabondAdmin)
admin.site.register(Hireling, HirelingAdmin)
admin.site.register(Faction, FactionAdmin)
admin.site.register(Expansion, ExpansionAdmin)
admin.site.register(PostTranslation, TranslationAdmin)
admin.site.register(FAQ, FAQAdmin)
admin.site.register(LawGroup, LawGroupAdmin)
admin.site.register(Law, LawAdmin)