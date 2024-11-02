from django.contrib import admin, messages
from django.urls import path, reverse
from django.shortcuts import render
from django.http import HttpResponseRedirect 
from .models import (Post, Map, Deck, Landmark, Vagabond, Hireling, Faction, Expansion,
                     Warrior, Building, Token, Card, OtherPiece)
from .forms import FactionImportForm, WarriorForm, BuildingForm, TokenForm, CardForm, OtherPieceForm
from django.contrib.auth.models import User
from the_warroom.admin import CsvImportForm
from datetime import datetime
from django.utils import timezone

class MapAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable', 'clearings')
    search_fields = ['title']
    raw_id_fields = ['designer']
class DeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable', 'card_total')
    search_fields = ['title']
    raw_id_fields = ['designer']
class LandmarkAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable')
    search_fields = ['title']
    raw_id_fields = ['designer']
class VagabondAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable', 'animal')
    search_fields = ['title']
    raw_id_fields = ['designer']
class HirelingAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable', 'animal')
    search_fields = ['title']
    raw_id_fields = ['designer']
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable')
    search_fields = ['title']
    raw_id_fields = ['designer']
class ExpansionAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer')
class WarriorAdmin(admin.ModelAdmin):
    list_display = ('name', 'faction', 'hireling', 'quantity', 'suited')
class BuildingAdmin(admin.ModelAdmin):
    list_display = ('name', 'faction', 'hireling', 'quantity', 'suited')
class TokenAdmin(admin.ModelAdmin):
    list_display = ('name', 'faction', 'hireling', 'quantity', 'suited')
class CardAdmin(admin.ModelAdmin):
    list_display = ('name', 'faction', 'hireling', 'quantity', 'suited')
class OtherPieceAdmin(admin.ModelAdmin):
    list_display = ('name', 'faction', 'hireling', 'quantity', 'suited')
class FactionAdmin(admin.ModelAdmin):
    list_display = ('title', 'designer', 'official', 'stable', 'type', 'reach', 'animal')
    search_fields = ['title']
    raw_id_fields = ['designer']


    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-faction-csv/', self.upload_csv)]
        return new_urls + urls


    #### DON'T LOOK..... THIS ISN'T PRETTY......
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

                if len(fields) < 58:  # Check to ensure there are enough fields
                    print("Not enough fields in this row.")
                    continue  # Skip to the next iteration if not enough fields

                user_instance, _ = User.objects.get_or_create(username=fields[4])
                if fields[5] == '':
                    expansion_instance = None
                else:
                    expansion_instance, _ = Expansion.objects.get_or_create(title=fields[5])
                stable = True if fields[7] == "Stable" else False
                official = True if fields[6] == "Y" else False
                print(fields[1], fields[7], stable)
                bgg_link = None if fields[54] == '' else fields[54]
                tts_link = None if fields[55] == '' else fields[55]
                ww_link = None if fields[56] == '' else fields[56]
                wr_link = None if fields[57] == '' else fields[57]
                pnp_link = None if fields[58] == '' else fields[58]
                reach = int(fields[9])

                if fields[3] != "":
                    year_string = fields[3]
                # Convert the string to a datetime object
                # Assuming you want to set it to January 1st, 2020 at midnight
                    date_posted = datetime.strptime(year_string, "%Y")
                else:
                    date_posted = timezone.now() 



                faction_data = {
                    'title': fields[1],
                    'designer': user_instance,
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
                    'stable': stable,
                    'official': official,
                    'date_posted': date_posted,
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
                            'faction': faction_instance,
                        }

                        card_form = CardForm(card_data)
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
                            'faction': faction_instance,
                        }

                        warrior_form = WarriorForm(warrior_data)
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
                            'faction': faction_instance,
                        }

                        warrior_form = WarriorForm(warrior_data)
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
                            'faction': faction_instance,
                        }

                        warrior_form = WarriorForm(warrior_data)
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
                            'faction': faction_instance,
                            'suited': suited
                        }

                        building_form = BuildingForm(building_data)
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
                            'faction': faction_instance,
                        }

                        building_form = BuildingForm(building_data)
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
                            'faction': faction_instance,
                        }

                        building_form = BuildingForm(building_data)
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
                            'faction': faction_instance,
                            'suited': suited
                        }

                        token_form = TokenForm(token_data)
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
                            'faction': faction_instance,
                        }

                        token_form = TokenForm(token_data)
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
                            'faction': faction_instance,
                        }

                        token_form = TokenForm(token_data)
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
                            'faction': faction_instance,
                        }

                        token_form = TokenForm(token_data)
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
                            'faction': faction_instance,
                        }

                        other_piece_form = OtherPieceForm(other_piece_data)
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
                            'faction': faction_instance,
                        }

                        other_piece_form = OtherPieceForm(other_piece_data)
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
                            'faction': faction_instance,
                        }

                        other_piece_form = OtherPieceForm(other_piece_data)
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
                            'faction': faction_instance,
                        }

                        other_piece_form = OtherPieceForm(other_piece_data)
                        if other_piece_form.is_valid():
                            print('Valid OtherPiece')
                            other_piece_form.save()
                        else:
                            print(f"Invalid form data: {other_piece_form.errors}") 






                else:
                    # Handle the invalid form case (e.g., log errors or notify the user)
                    messages.error(request, f"Error with row: {x}. Errors: {form.errors}")


            url = reverse('admin:index')
            return HttpResponseRedirect(url)

        form = CsvImportForm()
        data = {'form': form}
        return render(request, 'admin/csv_upload.html', data)



# Registering the models with the GroupedModelAdmin
admin.site.register(Warrior, WarriorAdmin)
admin.site.register(Building, BuildingAdmin)
admin.site.register(Token, TokenAdmin)
admin.site.register(Card, CardAdmin)
admin.site.register(OtherPiece, OtherPieceAdmin)

admin.site.register(Post, PostAdmin)
admin.site.register(Map, MapAdmin)
admin.site.register(Deck, DeckAdmin)
admin.site.register(Landmark, LandmarkAdmin)
admin.site.register(Vagabond, VagabondAdmin)
admin.site.register(Hireling, HirelingAdmin)
admin.site.register(Faction, FactionAdmin)
admin.site.register(Expansion, ExpansionAdmin)