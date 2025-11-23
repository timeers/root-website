import random
import yaml
import re
import markdown
import difflib
import os

from collections import defaultdict

from django.utils import timezone 
from django.utils.translation import gettext as _
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseBadRequest, FileResponse
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.db.models import (Count, F, Q, Value, Sum, ProtectedError, Count, 
                                CharField, IntegerField, FloatField, ExpressionWrapper,
                                Case, When)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import PermissionDenied, MultipleObjectsReturned, FieldError, ObjectDoesNotExist
from django.conf import settings
from django.db import IntegrityError
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from django.utils.translation import get_language

from the_warroom.filters import GameFilter
from django.views import View
from django.views.generic import (
    ListView, 
    DetailView, 
    CreateView,
    UpdateView,
    DeleteView
)
from the_warroom.models import Game, ScoreCard, Effort, Tournament, Round
from the_gatehouse.models import Profile, Language, Website
from the_gatehouse.views import (designer_required_class_based_view, designer_required, tester_required,
                                 player_required, player_required_class_based_view,
                                 admin_onboard_required, admin_required, editor_onboard_required, editor_required, editor_required_class_based_view)
from the_gatehouse.services.discordservice import send_discord_message, send_rich_discord_message
from the_gatehouse.utils import get_uuid, build_absolute_uri, int_to_alpha, int_to_roman
from the_gatehouse.services.context_service import get_theme, get_thematic_images
from .tasks import sync_rules_task
from .models import (
    Post, Expansion,
    Faction, Vagabond,
    Map, Deck,
    Hireling, Landmark,
    Piece, Tweak,
    PNPAsset, ColorChoices, PostTranslation,
    FAQ, LawGroup, Law, duplicate_laws_for_language, RulesFile,
    StatusChoices,
    )
from .forms import (MapCreateForm, 
                    DeckCreateForm, LandmarkCreateForm,
                    HirelingCreateForm, VagabondCreateForm,
                    FactionCreateForm, ExpansionCreateForm,
                    PieceForm, ClockworkCreateForm,
                    StatusConfirmForm, TweakCreateForm,
                    PNPAssetCreateForm, TranslationCreateForm,
                    AddLawForm, EditLawForm, EditLawDescriptionForm,
                    FAQForm, CopyLawGroupForm, LanguageSelectionForm, EditLawGroupForm,
                    YAMLUploadForm
)
from .utils import clean_meta_description, generate_comparison_markdown
from .services.law_update import (compare_structure_strict, update_laws_by_structure, get_translated_title, 
                                          serialize_group, update_laws_from_yaml, NoPrimeLawError, load_uploaded_yaml)
from the_tavern.forms import PostCommentCreateForm
from the_tavern.views import bookmark_toggle

from django.db import models
from django.db.models import OuterRef, Subquery, F, Value
from django.db.models.functions import Coalesce

from yaml.representer import SafeRepresenter

class DoubleQuoted(str):
    pass

class QuotedDumper(yaml.SafeDumper):
    pass

def quoted_presenter(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')

QuotedDumper.add_representer(DoubleQuoted, quoted_presenter)
QuotedDumper.add_representer(str, quoted_presenter)



def expansion_detail_view(request, slug):

    language_code = get_language()
    language_code_object = Language.objects.filter(code=language_code).first()


    expansion = get_object_or_404(Expansion, slug=slug)

    posts = Post.objects.filter(expansion=expansion).annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_code_object  # Assuming you want the current language
                ).values('translated_title')[:1],
                output_field=models.CharField()
            ),
            'title'  # Fall back to the default title if there's no translation
        ),
        selected_description=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_code_object
                ).values('translated_description')[:1],
                output_field=models.TextField()
            ),
            'description'  # Fall back to the default description if there's no translation
        ),
        selected_lore=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_code_object
                ).values('translated_lore')[:1],
                output_field=models.TextField()
            ),
            'lore'  # Fall back to the default lore if there's no translation
        )
    )
    factions = posts.filter(component='Faction')
    maps = posts.filter(component='Map')
    decks = posts.filter(component='Deck')
    vagabonds = posts.filter(component='Vagabond')
    landmarks = posts.filter(component='Landmark')
    hirelings = posts.filter(component='Hireling')
    clockworks = posts.filter(component='Clockwork')
    tweaks = posts.filter(component='Tweak')

    links_count = expansion.count_links(request.user)

    tts_link = expansion.tts_link
    bgg_link = expansion.bgg_link
    pnp_link = expansion.pnp_link


    if expansion.open_roster and (expansion.end_date > timezone.now() or not expansion.end_date):
        open_expansion = True
    else:
        open_expansion = False


    context = {
        'expansion': expansion,
        'posts': posts,
        'factions': factions,
        'maps': maps,
        'decks': decks,
        'vagabonds': vagabonds,
        'landmarks': landmarks,
        'hirelings': hirelings,
        'clockworks': clockworks,
        'tweaks': tweaks,
        'links_count': links_count,
        'open_expansion': open_expansion,
        'tts_link': tts_link,
        'bgg_link': bgg_link,
        'pnp_link': pnp_link,
    }

    return render(request, 'the_keep/expansion_detail.html', context)

class ExpansionFactionsListView(ListView):
    model = Faction
    context_object_name = 'objects'

@designer_required_class_based_view
class ExpansionCreateView(LoginRequiredMixin, CreateView):
    model = Expansion
    form_class = ExpansionCreateForm

    def form_valid(self, form):
        form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user
        return super().form_valid(form)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs
    
@designer_required_class_based_view
class ExpansionUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Expansion
    form_class = ExpansionCreateForm
    def form_valid(self, form):
        form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user
        return super().form_valid(form)
    
    def test_func(self):
        obj = self.get_object()
        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.designer
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs
class ExpansionDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Expansion
    success_url = '/'  # The default success URL after the post is deleted

    def test_func(self):
        expansion = self.get_object()
        return self.request.user.profile == expansion.designer  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        expansion = self.get_object()
        name = expansion.title

        try:
            # Attempt to delete the post
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The expansion '{name}' was successfully deleted and has been removed from any related posts.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The expansion '{name}' cannot be deleted because it has been used in a game.")
            # Redirect back to the post detail page
            return redirect('expansion-detail', expansion.slug)
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this post.")
            return redirect('expansion-detail', expansion.slug) 







# START CREATE VIEWS
@designer_required
def new_components(request):
    context = {

    }
    return render(request, 'the_keep/new.html', context=context)



class PostCreateView(LoginRequiredMixin, CreateView):
    """
    A base class for all CreateViews that require a designer field to be set to
    the current logged-in user's profile and also pass the user to the form.
    """
    def form_valid(self, form):
        if not form.instance.designer:
            form.instance.designer = self.request.user.profile  # Set the designer to the logged-in user

        response = super().form_valid(form)

        post = self.object
        fields = []
        fields.append({
                'name': 'Posted by:',
                'value': self.request.user.profile.name
            })
        send_rich_discord_message(f'[{post.title}](https://therootdatabase.com{post.get_absolute_url()})', category='Post Created', title=f'Posted {post.component}', fields=fields)

        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form
        return kwargs

@designer_required_class_based_view
class MapCreateView(PostCreateView):
    model = Map
    form_class = MapCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class DeckCreateView(PostCreateView):
    model = Deck
    form_class = DeckCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class LandmarkCreateView(PostCreateView):
    model = Landmark
    form_class = LandmarkCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class TweakCreateView(PostCreateView):
    model = Tweak
    form_class = TweakCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class HirelingCreateView(PostCreateView):
    model = Hireling
    form_class = HirelingCreateForm
    template_name = 'the_keep/post_form.html'

    def get_form_kwargs(self):
        # Get the default form kwargs
        kwargs = super().get_form_kwargs()
        # Add user to the kwargs
        kwargs['designer'] = self.request.user.profile
        return kwargs

@designer_required_class_based_view
class VagabondCreateView(PostCreateView):
    model = Vagabond
    form_class = VagabondCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class FactionCreateView(PostCreateView):
    model = Faction
    form_class = FactionCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class ClockworkCreateView(PostCreateView):
    model = Faction
    form_class = ClockworkCreateForm
    template_name = 'the_keep/post_form.html'

# END CREATE VIEWS

@editor_required_class_based_view  
class PostUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """
    A base class for all UpdateViews that require:
    1. The designer to be set to the current logged-in user.
    2. The date_updated field to be set to the current timestamp.
    3. Checking if the logged-in user is the designer of the object.
    """

    def form_valid(self, form):
        # Ensure the designer is set to the logged-in user's profile
        # if not self.request.user.profile.admin:
        #     form.instance.designer = self.request.user.profile
        form.instance.date_updated = timezone.now()  # Set the updated timestamp
        response = super().form_valid(form)

        post = self.object
        fields = []
        fields.append({
                'name': 'Edited by:',
                'value': self.request.user.profile.name
            })
        send_rich_discord_message(f'[{post.title}](https://therootdatabase.com{post.get_absolute_url()})', category='Post Edited', title=f'Edited {post.component}', fields=fields)


        return response

    def test_func(self):
        obj = self.get_object()
        if self.request.user.profile.admin:
            if not obj.designer.designer:
                if obj.designer.editor_onboard and self.request.method != "POST":
                    messages.warning(self.request, f'{obj.designer} is authorized to edit {obj.title}. Ensure you have their permission to edit before making any changes.')
                return True
            elif self.request.user.profile != obj.designer:
                messages.error(self.request, f'The {obj.component} "{obj.title}" can only be edited by {obj.designer}.')

        # Only allow access if the logged-in user is the designer of the object
        return self.request.user.profile == obj.designer

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Store the object to be reused in other methods if needed
        self._obj = obj
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # Pass the current user to the form

        # Pass the 'expansion' from the object (fetched in get_object)
        kwargs['expansion'] = self._obj.expansion if hasattr(self, '_obj') else None

        return kwargs

    

class MapUpdateView(PostUpdateView):
    model = Map
    form_class = MapCreateForm
    template_name = 'the_keep/post_form.html'


class DeckUpdateView(PostUpdateView):
    model = Deck
    form_class = DeckCreateForm
    template_name = 'the_keep/post_form.html'


class LandmarkUpdateView(PostUpdateView):
    model = Landmark
    form_class = LandmarkCreateForm
    template_name = 'the_keep/post_form.html'


class TweakUpdateView(PostUpdateView):
    model = Tweak
    form_class = TweakCreateForm
    template_name = 'the_keep/post_form.html'


class HirelingUpdateView(PostUpdateView):
    model = Hireling
    form_class = HirelingCreateForm
    template_name = 'the_keep/post_form.html'

    def get_form_kwargs(self):
        # Get the default form kwargs
        kwargs = super().get_form_kwargs()
        # Add user to the kwargs
        kwargs['designer'] = self.request.user.profile
        return kwargs


class VagabondUpdateView(PostUpdateView):
    model = Vagabond
    form_class = VagabondCreateForm
    template_name = 'the_keep/post_form.html'

  
class FactionUpdateView(PostUpdateView):
    model = Faction
    form_class = FactionCreateForm
    template_name = 'the_keep/post_form.html'

  
class ClockworkUpdateView(PostUpdateView):
    model = Faction
    form_class = ClockworkCreateForm
    template_name = 'the_keep/post_form.html'

@designer_required_class_based_view
class PostDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Post
    success_url = '/'  # The default success URL after the post is deleted

    def test_func(self):
        post = self.get_object()
        # print("testing delete function")
        return self.request.user.profile == post.designer  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        post = self.get_object()
        name = post.title

        component_mapping = {
                "Map": Map,
                "Deck": Deck,
                "Landmark": Landmark,
                "Tweak": Tweak,
                "Hireling": Hireling,
                "Vagabond": Vagabond,
                "Faction": Faction,
                "Clockwork": Faction,
            }
        Klass = component_mapping.get(post.component)
        object = get_object_or_404(Klass, slug=post.slug)

        try:
            # # Attempt to delete the post
            # response = self.delete(request, *args, **kwargs)
            # # Add success message upon successful deletion
            # messages.success(request, f"The {post.component} '{name}' was successfully deleted.")
            # return response
            games = object.get_games_queryset()
            if not games.exists():
                # Abandon the post without deleting
                post.status = 100  # Set the status to abandoned
                rand_int = random.randint(100, 999)
                post.title = f'Deleted {post.component}-{rand_int}'
                if post.component == 'Hireling':
                    hireling = Hireling.objects.get(slug=post.slug)
                    if hireling.other_side:
                        other_side = hireling.other_side
                        other_side.other_side = None
                        hireling.other_side = None
                        other_side.save()
                        hireling.save()

                post.save()
                messages.success(request, f'The {post.component} "{name}" was successfully deleted.')
                return redirect('archive-home')
            else:
                # Do not delete posts with games recorded.
                post.status = 4  # Set the status to inactive
                post.save()
                messages.error(request, f'The {post.component} "{name}" cannot be deleted because it has been used in a game. Status has been set to "Inactive".')
                # Redirect back to the post detail page
                return redirect(object.get_absolute_url())
            

        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The {post.component} '{name}' cannot be deleted because it has been used in a game.")
            # Redirect back to the post detail page
            return redirect(object.get_absolute_url())
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this post.")
            return redirect(object.get_absolute_url()) 



def about(request, *args, **kwargs):
    latest_law = RulesFile.objects.filter(post__isnull=True, status=RulesFile.Status.ACTIVE, language__code='en').first()
    if latest_law:
        law_date = latest_law.commit_date
    else:
        law_date = None

    context = {
        'title': 'About',
        'law_date': law_date
    }

    return render(request, 'the_keep/about.html', context)


def home(request, *args, **kwargs):

    # faction_count = Faction.objects.filter(status__lte=4, official=False).count()
    # deck_count = Deck.objects.filter(status__lte=4, official=False).count()
    # map_count = Map.objects.filter(status__lte=4, official=False).count()
    # official_faction_count = Faction.objects.filter(status__lte=4, official=True).count()
    # official_deck_count = Deck.objects.filter(status__lte=4, official=True).count()
    # official_map_count = Map.objects.filter(status__lte=4, official=True).count()
    # game_count = Game.objects.filter(final=True).count()

    if request.user.is_authenticated:
        send_discord_message(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) on Home Page')
    
    context = {
        'title': 'Home',
        # 'faction_count': faction_count,
        # 'deck_count': deck_count,
        # 'map_count': map_count,
        # 'official_faction_count': official_faction_count,
        # 'official_deck_count': official_deck_count,
        # 'official_map_count': official_map_count,
        # 'game_count': game_count,
    }

    return render(request, 'the_keep/home.html', context)


def translations_view(request, slug):

    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)
    # Get a list of other available translations
    other_translations = object.translations.all()
    languages_count = Language.objects.all().count() - 1
    # print(languages_count)
    if languages_count > other_translations.count():
        available_languages = True
    else:
        available_languages = False
    # print(available_languages)
    context = {
        'object': object,
        'other_translations': other_translations,
        'available_languages': available_languages,
    }

    return render(request, 'the_keep/post_translations.html', context)

@editor_required
def create_post_translation(request, slug, lang=None):
    post = get_object_or_404(Post, slug=slug)  # Get the post object
    
    if not post.designer==request.user.profile and not request.user.profile.admin:
        messages.error(request, f'You are not authorized to translate { post.title }.')
        raise PermissionDenied() 

    if lang:
        # Check if there's an existing translation for this post and language
        existing_translation = PostTranslation.objects.filter(post=post, language__code=lang).first()
    else:
        existing_translation = None

    if request.method == 'POST':
                # If updating, prepopulate the form with the existing translation, otherwise create a new one
        if existing_translation:
            form = TranslationCreateForm(request.POST, request.FILES, instance=existing_translation, post=post, user=post.designer)
        else:
            form = TranslationCreateForm(request.POST, request.FILES, post=post, user=post.designer)

        if form.is_valid():
            # Explicitly set the 'post' field to the post passed into the form
            translation = form.save(commit=False)
            translation.post = post  # Make sure post is set

            is_new = translation.pk is None
            # Save the translation
            translation.save()

            post_url = post.get_absolute_url()
    
            fields = []
            if is_new:
                fields.append({
                        'name': 'Posted by:',
                        'value': request.user.profile.name
                    })
                send_rich_discord_message(f'[{translation.translated_title}](https://therootdatabase.com{translation.get_absolute_url()})', category='Post Created', title=f'New {translation.post.component} Translation', fields=fields)
            else:
                fields.append({
                        'name': 'Edited by:',
                        'value': request.user.profile.name
                    })
                send_rich_discord_message(f'[{translation.translated_title}](https://therootdatabase.com{translation.get_absolute_url()})', category='Post Edited', title=f'Edited {translation.post.component} Translation', fields=fields)


            # Append the lang query parameter to the URL
            redirect_url = f"{post_url}?lang={translation.language.code}"

            return redirect(redirect_url)
    else:
        # If GET request, initialize the form with either a new instance or an existing one
        if existing_translation:
            form = TranslationCreateForm(instance=existing_translation, post=post, user=post.designer)
        else:
            form = TranslationCreateForm(post=post, user=post.designer)
    
    context = {
        'form': form,
        'post': post
    }

    return render(request, 'the_keep/translation_form.html', context)



def ultimate_component_view(request, slug):
    
    language_code = request.GET.get('lang') or get_language()
    language = Language.objects.filter(code=language_code).first()


    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    # Get the translation if available, fallback to default
    object_translation = object.translations.filter(language=language).first()

    existing_law = Law.objects.filter(group__post=post).first()
    if existing_law:
        editable_law = Law.objects.filter(group__post=post, language=language, prime_law=True).first()
        available_law = Law.objects.filter(group__post=post, language=language, prime_law=True, group__public=True).first()
    else:  
        editable_law = None
        available_law = None

    existing_faq = FAQ.objects.filter(post=post).first()
    if existing_faq:
        available_faq = FAQ.objects.filter(post=post, language=language).first()
    else:  
        available_faq = None

    if available_faq and available_law:
        col_class = 'w-100'
    else:
        col_class = 'w-50'


    available_translations = object.translations.all().count()


    object_title = object_translation.translated_title if object_translation and object_translation.translated_title else object.title
    object_lore = object_translation.translated_lore if object_translation and object_translation.translated_lore else object.lore
    object_description = object_translation.translated_description if object_translation and object_translation.translated_description else object.description

    object_animal = object_translation.translated_animal if object_translation and object_translation.translated_animal else object.animal

    object_ability = None
    object_ability_description = None
    if object.component == 'Vagabond':
            object_ability = object_translation.ability if object_translation and object_translation.ability else object.ability
            object_ability_description = object_translation.ability_description if object_translation and object_translation.ability_description else object.ability_description

    object_board_image = object_translation.translated_board_image if object_translation and object_translation.translated_board_image else object.board_image
    if object_board_image:
        object_board_image_url = object_board_image.url
    else:
        object_board_image_url = None

    object_board_2_image = object_translation.translated_board_2_image if object_translation and object_translation.translated_board_2_image else object.board_2_image
    if object_board_2_image:
        object_board_2_image_url = object_board_2_image.url
    else:
        object_board_2_image_url = None


    small_board_image = object_translation.small_board_image if object_translation and object_translation.translated_board_image else object.small_board_image
    small_board_2_image = object_translation.small_board_2_image if object_translation and object_translation.translated_board_2_image else object.small_board_2_image
    if small_board_image:
        small_board_image_url = small_board_image.url
    else:
        small_board_image_url = None

    if small_board_2_image:
        small_board_2_image_url = small_board_2_image.url
    else:
        small_board_2_image_url = None
    

    object_card_image = object_translation.translated_card_image if object_translation and object_translation.translated_card_image else object.card_image
    if object_card_image:
        object_card_image_url = object_card_image.url
    else:
        object_card_image_url = None

    object_card_2_image = object_translation.translated_card_2_image if object_translation and object_translation.translated_card_2_image else object.card_2_image
    if object_card_2_image:
        object_card_2_image_url = object_card_2_image.url
    else:
        object_card_2_image_url = None

    # Links
    tts_link = object_translation.tts_link if object_translation and object_translation.tts_link else object.tts_link
    bgg_link = object_translation.bgg_link if object_translation and object_translation.bgg_link else object.bgg_link
    pnp_link = object_translation.pnp_link if object_translation and object_translation.pnp_link else object.pnp_link


    if object.based_on:
        translation = PostTranslation.objects.filter(
            post=object.based_on,
            language=language
        ).values_list('translated_title', flat=True).first()

        based_on_title = translation or object.based_on.title
    else:
        based_on_title = None



    if request.user.is_authenticated:
        send_discord_message(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) viewed {object.component}: {object.title} ({language_code})')
    else:
        send_discord_message(f'{get_uuid(request)} viewed {object.component}: {object.title} ({language_code})')

    # print(f'Stable Ready: {stable_ready}')
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status

        
    related_posts = Post.objects.filter(based_on=object, status__lte=view_status)



    # Add the post that the current object is based on (if it exists)
    if object.based_on:
        related_posts |= Post.objects.filter(id=object.based_on.id, status__lte=view_status)
        related_posts |= Post.objects.filter(based_on=object.based_on, status__lte=view_status).exclude(id=object.id)

    if object.component == 'Vagabond':
        related_posts |= Post.objects.filter(title='Vagabond')

    related_posts = related_posts.annotate(
            selected_title=Coalesce(
                Subquery(
                    PostTranslation.objects.filter(
                        post=OuterRef('pk'),
                        language=language  # Assuming you want the current language
                    ).values('translated_title')[:1],
                    output_field=models.CharField()
                ),
                'title'  # Fall back to the default title if there's no translation
            )
        ).distinct()
    
    # Start with the base queryset
    games = object.get_games_queryset()

    stable_ready = None
    testing_ready = None
    if request.user.is_authenticated:
        if object.designer == request.user.profile:
            stable_ready_result = object.stable_check()
            stable_ready = stable_ready_result.stable_ready
            if object.status == '3' and games.count() > 0:
                testing_ready = True

    # Apply the conditional filter if needed
    if request.user.is_authenticated:
        if not request.user.profile.weird:
            games = games.filter(official=True)

    playtests = games.filter(test_match=True).count()

    games_total = games.count()

    if games_total == 1:
        if playtests:
            games_label = _("View 1 Playtest")
        else:
            games_label = _("View 1 Game")
    elif playtests == games_total and playtests != 0:
        games_label = _("View %(count)d Playtests") % {'count': games_total}
    elif playtests:
        if playtests == 1:
            games_label = _("View %(count)d Games (1 Playtest)") % {
                'count': games_total
            }
        else:
            games_label = _("View %(count)d Games (%(tests)d Playtests)") % {
                'count': games_total,
                'tests': playtests,
            }
    else:
        games_label = _("View %(count)d Games") % {'count': games_total}


    
    if post.component == "Faction" or post.component == "Clockwork":
        efforts = Effort.objects.filter(game__in=games, faction=post)
    elif post.component == "Vagabond":
        efforts = Effort.objects.filter(game__in=games, vagabond=post)
    else:
        efforts = Effort.objects.filter(game__in=games)

    if language_code == 'ru' or language_code == 'pl':
        # Special width for languages with large "low" translation
        attribute_map = {
            "L": '50%',     
            "M": '76%',
            "H": '100%',
            "N": '2%',
        }
    elif language_code == 'nl':

        attribute_map = {
            "L": '30%',
            "M": '59%',
            "H": '100%',
            "N": '2%',
        }
    elif language_code == 'fr':
        attribute_map = {
            "L": '37%',
            "M": '65%',
            "H": '100%',
            "N": '2%',
        }
    else:
        attribute_map = {
            "L": '28%',
            "M": '59%',
            "H": '100%',
            "N": '2%',
        }

    if object.component == 'Faction':
        complexity_value = attribute_map.get(object.complexity, 1)
        aggression_value = attribute_map.get(object.aggression, 1)
        card_wealth_value = attribute_map.get(object.card_wealth, 1)
        crafting_ability_value = attribute_map.get(object.crafting_ability, 1)
    else:
        complexity_value = None
        aggression_value = None
        card_wealth_value = None
        crafting_ability_value = None

    # Get top players for factions
    top_players = []
    most_players = []

    scorecard_count = None
    detail_scorecard_count = None

    if object.component == "Faction":
        top_players = Profile.leaderboard(effort_qs=efforts, limit=10, game_threshold=5)
        most_players = Profile.leaderboard(effort_qs=efforts, limit=10, top_quantity=True, game_threshold=1)

        scorecard_count = ScoreCard.objects.filter(faction__slug=object.slug, effort__isnull=False).count()
        detail_scorecard_count = ScoreCard.objects.filter(faction__slug=object.slug, effort__isnull=False, total_generic_points=0).count()


    links_count = object.count_links(request.user)

    if object.color_group:
        color_group = ColorChoices.get_color_group_by_hex(object.color_group)
        color_label = ColorChoices.get_color_label_by_hex(object.color_group)
        object_color = object.color_group
    else:
        if object.based_on and object.based_on.color_group:
            color_group = ColorChoices.get_color_group_by_hex(object.based_on.color_group)
            color_label = ColorChoices.get_color_label_by_hex(object.based_on.color_group)
            object_color = object.based_on.color_group
        else:
            color_group = None
            color_label = None
            object_color = None

    absolute_uri = build_absolute_uri(request, object.get_absolute_url())
    context = {
        'object': object,
        'games_total': games_total,
        'games_label': games_label,
        'top_players': top_players,
        'most_players': most_players,
        'stable_ready': stable_ready,
        'testing_ready': testing_ready,
        'related_posts': related_posts,
        'links_count': links_count,
        'scorecard_count': scorecard_count,
        'detail_scorecard_count': detail_scorecard_count,
        'color_group': color_group,
        'color_label': color_label,
        'object_color': object_color,
        'object_title': object_title,
        'object_lore': object_lore,
        'object_description': object_description,
        'object_animal': object_animal,
        'object_ability': object_ability,
        'object_ability_description': object_ability_description,
        'object_board_image_url': object_board_image_url,
        'object_board_2_image_url': object_board_2_image_url,
        'object_card_image_url': object_card_image_url,
        'object_card_2_image_url': object_card_2_image_url,

        'small_board_image_url': small_board_image_url,
        'small_board_2_image_url': small_board_2_image_url,


        'object_translation': object_translation,
        'available_translations': available_translations,
        'language_code': language_code,
        'language': language,

        'based_on_title': based_on_title,

        'crafting_ability_value': crafting_ability_value,
        'card_wealth_value': card_wealth_value,
        'aggression_value': aggression_value,
        'complexity_value': complexity_value,

        'absolute_uri': absolute_uri,

        'available_law': available_law,
        'existing_law': existing_law,
        'editable_law': editable_law,

        'available_faq': available_faq,
        'existing_faq': existing_faq,

        'col_class': col_class,

        'tts_link': tts_link,
        'bgg_link': bgg_link,
        'pnp_link': pnp_link,

    }
    if request.htmx:
            return render(request, 'the_keep/partials/game_list.html', context)

    return render(request, 'the_keep/post_detail.html', context)



def color_group_view(request, color_name):

    language = get_language()
    language_object = Language.objects.filter(code=language).first()

    color_name = str(color_name).capitalize()
    color_group = ColorChoices.get_color_by_name(color_name)
    color_label = ColorChoices.get_color_label_by_hex(color_group)


    matching_colors = Post.objects.filter(Q(color_group=color_group) | Q(based_on__color_group=color_group)).annotate(
                selected_title=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object  # Assuming you want the current language
                        ).values('translated_title')[:1],
                        output_field=models.CharField()
                    ),
                    'title'  # Fall back to the default title if there's no translation
                ),
                selected_description=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_description')[:1],
                        output_field=models.TextField()
                    ),
                    'description'  # Fall back to the default description if there's no translation
                ),
                selected_lore=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_lore')[:1],
                        output_field=models.TextField()
                    ),
                    'lore'  # Fall back to the default lore if there's no translation
                )
            ).distinct()

    if not color_group:
        # Get the referring URL
        referer = request.META.get('HTTP_REFERER')
        messages.error(request, f'No posts matching the color "{color_name}" were found.')
        # If there's a valid referer, redirect back to it, otherwise redirect to home
        if referer:
            return redirect(request.META.get('HTTP_REFERER'))
        else:
            return redirect('archive-home')
        
    match_title = _("{} Components").format(color_label)
    context = {
        'posts': matching_colors,
        'match_title': match_title,
        'color_group': color_group,
    }
    return render(request, 'the_keep/similar_posts.html', context)

def animal_match_view(request, slug):

    language = get_language()
    language_object = Language.objects.filter(code=language).first()
    # post = get_object_or_404(Post, slug=slug)
    post = Post.objects.filter(slug=slug).annotate(
                selected_title=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object  # Assuming you want the current language
                        ).values('translated_title')[:1],
                        output_field=models.CharField()
                    ),
                    'title'  # Fall back to the default title if there's no translation
                ),
                selected_description=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_description')[:1],
                        output_field=models.TextField()
                    ),
                    'description'  # Fall back to the default description if there's no translation
                ),
                selected_lore=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_lore')[:1],
                        output_field=models.TextField()
                    ),
                    'lore'  # Fall back to the default lore if there's no translation
                )
            ).first()
    matching_posts = post.matching_animals().annotate(
                selected_title=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object  # Assuming you want the current language
                        ).values('translated_title')[:1],
                        output_field=models.CharField()
                    ),
                    'title'  # Fall back to the default title if there's no translation
                ),
                selected_description=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_description')[:1],
                        output_field=models.TextField()
                    ),
                    'description'  # Fall back to the default description if there's no translation
                ),
                selected_lore=Coalesce(
                    Subquery(
                        PostTranslation.objects.filter(
                            post=OuterRef('pk'),
                            language=language_object
                        ).values('translated_lore')[:1],
                        output_field=models.TextField()
                    ),
                    'lore'  # Fall back to the default lore if there's no translation
                )
            ).distinct()
    language = get_language()
    language_object = Language.objects.filter(code=language).first()
    # Try to get the translated animal name from PostTranslation
    translated_animal = None
    if language_object:
        translation = post.translations.filter(language=language_object).first()
        if translation and translation.translated_animal:
            translated_animal = translation.translated_animal

    # Use translated animal if it exists, otherwise fallback to post.animal
    animal_name = translated_animal or post.animal or ""

    match_title = _("{} Components").format(animal_name)
    description = _("Other {} Components:").format(animal_name)
    
    context = {
        'original_post': post,
        'posts': matching_posts,
        'match_title': match_title,
        'description': description
    }
    return render(request, 'the_keep/similar_posts.html', context)



def component_games(request, slug):
    
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    # Start with the base queryset
    games = object.get_games_queryset()

    # Apply the conditional filter if needed
    if request.user.is_authenticated:
        if not request.user.profile.weird:
            games = games.filter(official=True)

    # Apply distinct and prefetch_related to all cases  
    prefetch_values = [
        'efforts__player', 'efforts__faction', 'efforts__vagabond', 'round__tournament', 
        'hirelings', 'landmarks', 'tweaks', 'map', 'deck', 'undrafted_faction', 'undrafted_vagabond'
    ]
    games = games.distinct().prefetch_related(*prefetch_values)

    game_filter = GameFilter(request.GET, user=request.user, queryset=games)

    # Get the filtered queryset
    filtered_games = game_filter.qs.distinct()


    # if post.component == "Faction" or post.component == "Clockwork":
    #     efforts = Effort.objects.filter(game__in=filtered_games, faction=post)
    # elif post.component == "Vagabond":
    #     efforts = Effort.objects.filter(game__in=filtered_games, vagabond=post)
    # else:
    #     efforts = Effort.objects.filter(game__in=filtered_games)
    
    # Get top players for factions
    win_count = 0
    coalition_count = 0
    win_rate = 0
    tourney_points = 0
    total_efforts = 0
    # On first load get faction and VB Stats
    page_number = request.GET.get('page')  # Get the page number from the request
    print(page_number)
    if not page_number:
        
        if object.component == "Faction":
            efforts = Effort.objects.filter(game__in=filtered_games, faction=object)
        elif object.component == "Vagabond":
            efforts = Effort.objects.filter(game__in=filtered_games, vagabond=object)
        else:
            efforts = Effort.objects.filter(game__in=filtered_games)

        game_values = efforts.aggregate(
            total_efforts=Count('id'),
            win_count=Count('id', filter=Q(win=True)),
            coalition_count=Count('id', filter=Q(win=True, game__coalition_win=True))
        )

        if object.component == "Faction" or object.component == "Vagabond":
            # Access the aggregated values from the dictionary returned by .aggregate()
            total_efforts = game_values['total_efforts']
            win_count = game_values['win_count']
            coalition_count = game_values['coalition_count']
        if total_efforts > 0:
            win_rate = (win_count - (coalition_count / 2)) / total_efforts * 100
        else:
            win_rate = 0
        tourney_points = win_count - (coalition_count / 2)
    # Paginate games
    paginate_by = settings.PAGE_SIZE
    paginator = Paginator(filtered_games, paginate_by)  # Use the queryset directly
    try:
        page_obj = paginator.get_page(page_number)  # Get the specific page of games
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)  # Redirect to the last page if invalid




    context = {
        'object': object,
        'games_total': games.count(),
        'filtered_games': filtered_games.count(),
        'games': page_obj,  # Pagination applied here
        'is_paginated': len(filtered_games) > paginate_by,
        'form': game_filter.form,
        'filterset': game_filter,
        'win_count': win_count,
        'coalition_count': coalition_count,
        'win_rate': win_rate,
        'tourney_points': tourney_points,
        'total_efforts': total_efforts,
    }
    if request.htmx:
            return render(request, 'the_keep/partials/game_list.html', context)
    return render(request, 'the_keep/post_games.html', context)




@login_required
@bookmark_toggle(Post)
def bookmark_post(request, object):
    return render(request, 'the_keep/partials/bookmarks.html', {'object': object })



# Search Page
def list_view(request, slug=None):

    if request.user.is_authenticated:
        send_discord_message(f'[{request.user}]({build_absolute_uri(request, request.user.profile.get_absolute_url())}) viewing The Archive')
    
    theme = get_theme(request)

    background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(theme=theme, page='library')

    posts, search, search_type, designer, faction_type, reach_value, status, language_code, expansion = _search_components(request, slug)
    # designers = Profile.objects.annotate(posts_count=Count('posts')).filter(posts_count__gt=0)
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
        if request.user.profile.weird:
            # Filter designers who have at least one post with a status less than or equal to the user's view_status
            designers = Profile.objects.annotate(
                posts_count=Count('posts'),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(
                official_posts_count=Count('posts', filter=Q(posts__official=True)),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
                ).filter(official_posts_count__gt=0, valid_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(
            posts_count=Count('posts'),
            valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
    
    used_languages = Language.objects.filter(
            Q(post__isnull=False) | Q(posttranslation__isnull=False)
        ).distinct()
    expansions = Expansion.objects.all()
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        'faction_type': faction_type or "",
        'reach_value': reach_value or "",
        'status': status or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': False,
        'slug': slug,
        'background_image': background_image,
        'foreground_images': foreground_images,
        'background_pattern': background_pattern,
        # 'theme_artists': theme_artists,
        'used_languages': used_languages,
        'language_code': language_code,
        'selected_expansion': expansion,
        'expansions': expansions,
        }
    # if request.htmx:
    #     return render(request, "the_keep/partials/search_body.html", context)    

    return render(request, "the_keep/list.html", context)


def search_view(request, slug=None):
    
    if not request.htmx:
        return redirect('archive-home')
    
    posts, search, search_type, designer, faction_type, reach_value, status, language_code, expansion = _search_components(request, slug)
    # Get all designers (Profiles) who have at least one post
    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
        if request.user.profile.weird:
            # Filter designers who have at least one post with a status less than or equal to the user's view_status
            designers = Profile.objects.annotate(
                posts_count=Count('posts'),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
        else:
            # Filter designers who have at least one post with 'official' property set to True
            designers = Profile.objects.annotate(
                official_posts_count=Count('posts', filter=Q(posts__official=True)),
                valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
                ).filter(official_posts_count__gt=0, valid_posts_count__gt=0)
    else:
        designers = Profile.objects.annotate(
            posts_count=Count('posts'),
            valid_posts_count=Count('posts', filter=Q(posts__status__lte=view_status))
            ).filter(posts_count__gt=0, valid_posts_count__gt=0)
    expansions = Expansion.objects.all()
    context = {
        "posts": posts, 
        'search': search or "", 
        'search_type': search_type or "",
        'faction_type': faction_type or "",
        'reach_value': reach_value or "",
        'status': status or "",
        "designers": designers,
        'designer': designer,
        'is_search_view': True,
        'slug': slug,
        'language_code': language_code,
        'selected_expansion': expansion,
        'expansions': expansions,
        }

    
    return render(request, "the_keep/partials/search_results.html", context)


def _search_components(request, slug=None):
    search = request.GET.get('search')
    search_type = request.GET.get('search_type', '')
    faction_type = request.GET.get('faction_type', '')
    reach_value = request.GET.get('reach_value', '')
    status = request.GET.get('status', '')
    designer = request.GET.get('designer') 
    language_code = request.GET.get('language_code', '')
    expansion = request.GET.get('expansion', '')
    page = request.GET.get('page')

    view_status = 4
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status

    if faction_type or reach_value:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Faction.objects.filter(status__lte=view_status).prefetch_related('designer')
            else:
                posts = Faction.objects.filter(official=True, status__lte=view_status).prefetch_related('designer')
        else:
            posts = Faction.objects.filter(status__lte=view_status).prefetch_related('designer')

        if faction_type:
            posts = posts.filter(type=faction_type, status__lte=view_status)
        if reach_value:
            posts = posts.filter(reach=reach_value, status__lte=view_status)

    else:
        if request.user.is_authenticated:
            if request.user.profile.weird:
                posts = Post.objects.filter(status__lte=view_status).prefetch_related('designer')
            else:
                posts = Post.objects.filter(official=True, status__lte=view_status).prefetch_related('designer')
        else:
            posts = Post.objects.filter(status__lte=view_status).prefetch_related('designer')

    if slug:
        player = get_object_or_404(Profile, slug=slug)
        posts = posts.filter(designer=player)
    if search:
        # posts = posts.filter(title__icontains=search)
        posts = posts.filter(Q(title__icontains=search)|Q(animal__icontains=search)|Q(translations__translated_title__icontains=search)|Q(expansion__title__icontains=search))
        
    if search_type:
        posts = posts.filter(component__icontains=search_type)

    if designer:
        posts = posts.filter(designer__id=designer)

    if expansion:
        posts = posts.filter(expansion__id=expansion)



    if status:
        if status == 'Official':
            posts = posts.filter(official=True)
        else:
            posts = posts.filter(status=status)

    if language_code:
        posts = posts.filter(Q(language__code=language_code)|Q(translations__language__code=language_code))
        language_object = Language.objects.filter(code=language_code).first()
    else:
        language = get_language()
        language_object = Language.objects.filter(code=language).first()
    if not language_object:
        # Fallback to default language or raise an appropriate exception
        language_object = Language.objects.get(code='en')

    # print(language, language_object)
    # Annotate each post with the translated title and description, using Coalesce to fall back to default values
    posts = posts.annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object  # Assuming you want the current language
                ).values('translated_title')[:1],
                output_field=models.CharField()
            ),
            'title'  # Fall back to the default title if there's no translation
        ),
        selected_description=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_description')[:1],
                output_field=models.TextField()
            ),
            'description'  # Fall back to the default description if there's no translation
        ),
        selected_lore=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_lore')[:1],
                output_field=models.TextField()
            ),
            'lore'  # Fall back to the default lore if there's no translation
        )
    ).distinct().order_by('sorting', '-official', 'status', '-date_posted', 'id')
    
    paginator = Paginator(posts, settings.PAGE_SIZE)

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    try:
        posts = paginator.page(page)
    except PageNotAnInteger:
        posts = paginator.page(1)
    except EmptyPage:
        posts = paginator.page(paginator.num_pages)
    return posts, search or "", search_type or "", designer or "", faction_type or "", reach_value or "", status or "", language_code or "", expansion or ""









# ADVANCED SEARCH VIEWS

def get_view_status(user):
    if user.is_authenticated:
        return user.profile.view_status
    return 4


def get_profiles(user, view_status, component, selected_id, related_name):
    """
    To fetch profiles based on the related post type.
    :user: The current user
    :view_status: Integer representing max visible post status
    :component: The component to filter posts on
    :selected_id: A profile ID to ensure is always included
    :related_name: The related name for posts (e.g., 'posts', 'artist_posts')
    """

    # Build dynamic filter keys
    posts_field = f"{related_name}"
    official_filter = Q(**{f"{posts_field}__official": True})
    valid_filter = Q(**{f"{posts_field}__status__lte": view_status, f"{posts_field}__component": component})

    # Annotate
    base_queryset = Profile.objects.annotate(
        posts_count=Count(posts_field),
        official_posts_count=Count(posts_field, filter=official_filter),
        valid_posts_count=Count(posts_field, filter=valid_filter)
    )

    # Apply filters depending on user
    if not user.is_authenticated or user.profile.weird:
        filtered_queryset = base_queryset.filter(
            posts_count__gt=0,
            valid_posts_count__gt=0
        )
    else:
        filtered_queryset = base_queryset.filter(
            official_posts_count__gt=0,
            valid_posts_count__gt=0
        )

    # Always include selected profile
    if selected_id:
        filtered_queryset = filtered_queryset | base_queryset.filter(id=selected_id)

    return filtered_queryset.distinct()


def get_designers(user, view_status, component, selected_id):
    return get_profiles(user, view_status, component, selected_id, related_name='posts')

def get_artists(user, view_status, component, selected_id):
    return get_profiles(user, view_status, component, selected_id, related_name='artist_posts')


def build_post_queryset(user, view_status, component):

    component_mapping = {
        "Post": Post,
        "Map": Map,
        "Deck": Deck,
        "Landmark": Landmark,
        "Tweak": Tweak,
        "Hireling": Hireling,
        "Vagabond": Vagabond,
        "Faction": Faction,
        "Clockwork": Faction,
    }
    Klass = component_mapping.get(component)

    qs = Klass.objects.prefetch_related('designer').filter(status__lte=view_status, component=component)

    if user.is_authenticated and not user.profile.weird:
        qs = qs.filter(official=True)

    return qs

def apply_filters(qs, filters, component):

    if filters.get('official'):
        official_value = filters['official']
        if isinstance(official_value, bool):
            qs = qs.filter(official=official_value)

    if filters.get('search'):
        search_term = filters['search']
        qs = qs.filter(
            Q(title__icontains=search_term) |
            Q(translations__translated_title__icontains=search_term)
        )

    if filters.get('designer'):
        qs = qs.filter(designer__id=filters['designer'])

    if filters.get('artist'):
        qs = qs.filter(artist__id=filters['artist'])

    if filters.get('expansion'):
        qs = qs.filter(expansion__id=filters['expansion'])

    if filters.get('status'):
        qs = qs.filter(status=filters['status'])

    if filters.get('language_code'):
        qs = qs.filter(
            Q(language__code=filters['language_code']) |
            Q(translations__language__code=filters['language_code'])
        )

    if component in ["Faction", "Hireling", "Vagabond", "Clockwork"]:
        if filters.get('color'):
            qs = qs.filter(color_group=filters['color'])
        if filters.get('animal'):
            search_animal = filters['animal']
            qs = qs.filter(
                Q(animal__icontains=search_animal) |
                Q(translations__translated_animal__icontains=search_animal)
            )
    
    # Map specific filters
    if component == "Map":
        if filters.get('clearings_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="clearings", quantity=filters['clearings_qty'], comparator=filters['clearings_op'])
        if filters.get('forests_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="forests", quantity=filters['forests_qty'], comparator=filters['forests_op'])
        if filters.get('river_clearings_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="river_clearings", quantity=filters['river_clearings_qty'], comparator=filters['river_clearings_op'])
        if filters.get('building_slots_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="building_slots", quantity=filters['building_slots_qty'], comparator=filters['building_slots_op'])
        if filters.get('ruins_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="ruins", quantity=filters['ruins_qty'], comparator=filters['ruins_op'])

    # Vagabond specific filters
    if component == "Vagabond":
        if filters.get('ability'):
            qs = qs.filter(ability__icontains=filters['ability'])
        if filters.get('ability_description'):
            qs = qs.filter(ability_description__icontains=filters['ability_description'])
        if filters.get('ability_item'):
            qs = qs.filter(ability_item__iexact=filters['ability_item'])
        if filters.get('coin_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_coins", quantity=filters['coin_qty'], comparator=filters['coin_op'])
        if filters.get('boot_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_boots", quantity=filters['boot_qty'], comparator=filters['boot_op'])
        if filters.get('bag_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_bag", quantity=filters['bag_qty'], comparator=filters['bag_op'])
        if filters.get('tea_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_tea", quantity=filters['tea_qty'], comparator=filters['tea_op'])
        if filters.get('hammer_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_hammer", quantity=filters['hammer_qty'], comparator=filters['hammer_op'])
        if filters.get('crossbow_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_crossbow", quantity=filters['crossbow_qty'], comparator=filters['crossbow_op'])
        if filters.get('sword_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_sword", quantity=filters['sword_qty'], comparator=filters['sword_op'])
        if filters.get('torch_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="starting_torch", quantity=filters['torch_qty'], comparator=filters['torch_op'])

    # Faction specific filters
    if component == "Faction":
        if filters.get('aggression'):
            qs = qs.filter(aggression=filters['aggression'])
        if filters.get('complexity'):
            qs = qs.filter(complexity=filters['complexity'])
        if filters.get('card_wealth'):
            qs = qs.filter(card_wealth=filters['card_wealth'])
        if filters.get('crafting_ability'):
            qs = qs.filter(crafting_ability=filters['crafting_ability'])

        if filters.get('faction_type'):
            qs = qs.filter(type=filters['faction_type'])
        if filters.get('reach_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="reach", quantity=filters['reach_qty'], comparator=filters['reach_op'])


    # Hireling specific filters
    if component == "Hireling":
        if filters.get('hireling_type'):
            qs = qs.filter(type=filters['hireling_type'])

    # Deck specific filters
    if component == "Deck":
        if filters.get('card_total_qty'):
            qs = filter_by_field_quantity(queryset=qs, field_type="card_total", quantity=filters['card_total_qty'], comparator=filters['card_total_op'])
        
    # Pieces
    if filters.get('warrior_qty'):
        qs = filter_by_piece_quantity(queryset=qs, piece_type="W", quantity=filters['warrior_qty'], comparator=filters['warrior_op'])
    if filters.get('building_qty'):
        qs = filter_by_piece_quantity(queryset=qs, piece_type="B", quantity=filters['building_qty'], comparator=filters['building_op'])
    if filters.get('token_qty'):
        qs = filter_by_piece_quantity(queryset=qs, piece_type="T", quantity=filters['token_qty'], comparator=filters['token_op'])
    if filters.get('card_qty'):
        qs = filter_by_piece_quantity(queryset=qs, piece_type="C", quantity=filters['card_qty'], comparator=filters['card_op'])
    if filters.get('other_qty'):
        qs = filter_by_piece_quantity(queryset=qs, piece_type="O", quantity=filters['other_qty'], comparator=filters['other_op'])

    if filters.get('games_qty'):
        if component in ["Faction", "Clockwork", "Vagabond"]:
            qs = qs.annotate(
                    total_games=Count(
                        'efforts__game',
                        filter=Q(
                            efforts__game__final=True
                        )
                    )
                )     
        else:
            qs = qs.annotate(
                    total_games=Count(
                        'games',
                        filter=Q(
                            games__final=True
                        )
                    )
                )   
            
        games_qty = filters.get('games_qty')
        games_op = filters.get('games_op')

        # Apply exclusion of total_games=0 *unless* it's asking for "0" or "< 1"
        if not (games_qty == "0" or (games_qty == "1" and games_op == "lt")):
            qs = qs.exclude(total_games=0)

        qs = filter_by_field_quantity(queryset=qs, field_type="total_games", quantity=games_qty, comparator=games_op)

    # Winrate
    if component in ["Faction", "Vagabond", "Clockwork"]:
        try:
            winrate_min = float(filters.get('winrate_min', 0))
            winrate_max = float(filters.get('winrate_max', 100))

            if winrate_min != 0 or winrate_max != 100:
                qs = qs.annotate(
                    total_wins=Count(
                        'efforts__game',
                        filter=Q(
                            efforts__win=True,
                            # efforts__game__test_match=False,
                            efforts__game__final=True
                        )
                    ),
                    total_games=Count(
                        'efforts__game',
                        filter=Q(
                            # efforts__game__test_match=False,
                            efforts__game__final=True
                        )
                    ),
                    coalition_count=Count(
                        'efforts__game', 
                        filter=Q(
                            efforts__win=True, 
                            efforts__game__coalition_win=True
                        )
                    )
                ).filter(
                    total_games__gt=0
                ).annotate(
                    calculated_winrate=ExpressionWrapper(
                        100.0 * F('total_wins') / F('total_games'),
                        output_field=FloatField()
                    )
                ).filter(
                    calculated_winrate__gte=winrate_min,
                    calculated_winrate__lte=winrate_max
                )
        except (TypeError, ValueError):
            pass


    return qs

def generate_filters(request):
    filters = {
        'search': request.GET.get('search', ''),
        'status': request.GET.get('status', ''),
        'designer': request.GET.get('designer', ''),
        'artist': request.GET.get('artist', ''),
        'language_code': request.GET.get('language_code', ''),
        'expansion': request.GET.get('expansion', ''),
        'official': request.GET.get('official', ''),
        'animal': request.GET.get('animal', ''),
        'color': request.GET.get('color', ''),
        # Winrate
        'winrate_min': request.GET.get('winrate_min', ''),
        'winrate_max': request.GET.get('winrate_max', ''),  
        # Games played
        'games_qty': request.GET.get('games_qty', ''),
        'games_op': request.GET.get('games_op', ''),
        # factions
        'faction_type': request.GET.get('faction_type', ''),
        'reach_qty': request.GET.get('reach_qty', ''),
        'reach_op': request.GET.get('reach_op', ''),
        'aggression': request.GET.get('aggression', ''),
        'complexity': request.GET.get('complexity', ''),
        'card_wealth': request.GET.get('card_wealth', ''),
        'crafting_ability': request.GET.get('crafting_ability', ''),
        # maps
        'clearings_qty': request.GET.get('clearings_qty', ''),
        'clearings_op': request.GET.get('clearings_op', ''),
        'forests_qty': request.GET.get('forests_qty', ''),
        'forests_op': request.GET.get('forests_op', ''),
        'river_clearings_qty': request.GET.get('river_clearings_qty', ''),
        'river_clearings_op': request.GET.get('river_clearings_op', ''),
        'building_slots_qty': request.GET.get('building_slots_qty', ''),
        'building_slots_op': request.GET.get('building_slots_op', ''),
        'ruins_qty': request.GET.get('ruins_qty', ''),
        'ruins_op': request.GET.get('ruins_op', ''),
        # vagabonds
        'ability': request.GET.get('ability', ''),
        'ability_description': request.GET.get('ability_description', ''),
        'ability_item': request.GET.get('ability_item', ''),
        'coin_qty': request.GET.get('coin_qty', ''),
        'coin_op': request.GET.get('coin_op', ''),
        'boot_qty': request.GET.get('boot_qty', ''),
        'boot_op': request.GET.get('boot_op', ''),
        'bag_qty': request.GET.get('bag_qty', ''),
        'bag_op': request.GET.get('bag_op', ''),
        'tea_qty': request.GET.get('tea_qty', ''),
        'tea_op': request.GET.get('tea_op', ''),
        'hammer_qty': request.GET.get('hammer_qty', ''),
        'hammer_op': request.GET.get('hammer_op', ''),
        'crossbow_qty': request.GET.get('crossbow_qty', ''),
        'crossbow_op': request.GET.get('crossbow_op', ''),
        'sword_qty': request.GET.get('sword_qty', ''),
        'sword_op': request.GET.get('sword_op', ''),
        'torch_qty': request.GET.get('torch_qty', ''),
        'torch_op': request.GET.get('torch_op', ''),
        # hirelings
        'hireling_type': request.GET.get('hireling_type', ''),
        # decks
        'card_total_qty': request.GET.get('card_total_qty', ''),
        'card_total_op': request.GET.get('card_total_op', ''),
        # clockwork
        # house rule
        # pieces
        'warrior_qty': request.GET.get('warrior_qty', ''),
        'warrior_op': request.GET.get('warrior_op', ''),
        'building_qty': request.GET.get('building_qty', ''),
        'building_op': request.GET.get('building_op', ''),
        'token_qty': request.GET.get('token_qty', ''),
        'token_op': request.GET.get('token_op', ''),
        'card_qty': request.GET.get('card_qty', ''),
        'card_op': request.GET.get('card_op', ''),
        'other_qty': request.GET.get('other_qty', ''),
        'other_op': request.GET.get('other_op', ''),
    }

    return filters

LABEL_MAP = {
    "search": "Search Term",
    "designer": "Designer",
    "artist": "Artist",
    "language_code": "Language",
    "expansion": "Expansion",
    "status": "Status",
    "animal": "Animal",
    "color": "Color",
    "games": "Games Played",

    # Faction
    "faction_type": "Faction Type",
    "reach": "Reach",
    "aggression": "Aggression",
    "complexity": "Complexity",
    "card_wealth": "Card Wealth",
    "crafting_ability": "Crafting Ability",

    # Map
    "clearings": "Clearings",
    "forests": "Forests",
    "river_clearings": "River Clearings",
    "building_slots": "Building Slots",
    "ruins": "Ruins",

    # Vagabond
    "ability": "Ability",
    "ability_description": "Ability Description",
    "ability_item": "Ability Item",
    "coin": "Coins",
    "boot": "Boots",
    "bag": "Bags",
    "tea": "Tea",
    "hammer": "Hammers",
    "crossbow": "Crossbows",
    "sword": "Swords",
    "torch": "Torches",

    # Deck
    "card_total": "Deck Card Count",

    # Hireling
    "hireling_type": "Hireling Type",

    # Pieces
    "warrior": "Warrior Count",
    "building": "Building Count",
    "token": "Token Count",
    "card": "Card Count",
    "other": "Other Count",
}


OPERATOR_SYMBOLS = {
    'lt': '<',
    'lte': '≤',
    'gt': '>',
    'gte': '≥',
    'exact': '=',
}


def get_name_from_id(model, id_value):
    try:
        obj = model.objects.get(id=id_value)
        return str(obj)
    except (ObjectDoesNotExist, ValueError):
        return id_value  # Fallback to showing the raw value

def describe_filters(filters):
    descriptions = []
    used_keys = set()

    ID_LOOKUPS = {
        'designer': Profile,
        'artist': Profile,
        'expansion': Expansion,
    }

    for key, value in filters.items():
        
        if key.startswith('winrate'):
            if key == 'winrate_min':
                winrate_min = value
                winrate_max = filters.get('winrate_max', '100')
                if not (winrate_min == "0" and winrate_max == "100") and (winrate_min or winrate_max):
                    if winrate_min == "0":
                        winrate_description = f"Winrate ≤ {winrate_max}%"
                    elif winrate_max == '100':
                        winrate_description = f"Winrate ≥ {winrate_min}%"
                    elif winrate_max == winrate_min:
                        winrate_description = f"Winrate = {winrate_max}%"
                    else:
                        winrate_description = f"Winrate: {winrate_min}%-{winrate_max}%"
                    descriptions.append(winrate_description)
                    used_keys.update({'winrate_max', 'winrate_min'})
        else:

            if not value or key in used_keys:
                continue

            # Quantity/Operator pair
            if key.endswith('_qty'):
                prefix = key[:-4]
                qty = value
                op_key = f"{prefix}_op"
                op = filters.get(op_key, 'exact')

                label = LABEL_MAP.get(prefix, prefix.replace('_', ' ').title())
                symbol = OPERATOR_SYMBOLS.get(op, '=')

                descriptions.append(f"{label} {symbol} {qty}")
                used_keys.update({key, op_key})

            elif key.endswith('_op') and f"{key[:-3]}_qty" in filters:
                continue  # Already handled

            # Handle ID lookups
            elif key in ID_LOOKUPS:
                label = LABEL_MAP.get(key, key.replace('_', ' ').title())
                name = get_name_from_id(ID_LOOKUPS[key], value)
                descriptions.append(f"{label}: {name}")
                used_keys.add(key)
            # Handle statuses
            elif key == "status":
                label = LABEL_MAP.get("status", "Status")
                try:
                    status_label = StatusChoices(value).label
                    descriptions.append(f"{label}: {status_label}")
                except ValueError:
                    descriptions.append(f"{label}: {value}")  # fallback
            elif key == "official":
                if value == "True":
                    descriptions.append("Official Only")
                elif value == "False":
                    descriptions.append("Fan Content Only")

            else:
                label = LABEL_MAP.get(key, key.replace('_', ' ').title())
                descriptions.append(f"{label}: {value}")
                used_keys.add(key)

    return ", ".join(descriptions)



def filter_by_piece_quantity(queryset, *, piece_type, quantity=None, comparator='exact'):
    """
    Filters Posts by the total quantity of a given piece (W, B, T, C, O).
    Input:
        queryset: The base Post queryset.
        piece_type: 'W' (Warrior), 'B' (Building), 'T' (Token), 'C' (Card), 'O' (Other)
        quantity: An integer value to compare against.
        comparator: One of 'lt', 'lte', 'gt', 'gte', 'exact'.
    Output:
        filtered Posts
    """
    # Safety check for valid comparator otherwise return qs
    if comparator not in ['lt', 'lte', 'gt', 'gte', 'exact']:
        return queryset
    # Build the annotation name dynamically
    annotation_name = f'total_{piece_type.lower()}_pieces'
    # Annotate total quantity for the given piece type
    queryset = queryset.annotate(
        **{
            annotation_name: Coalesce(
                Sum('pieces__quantity', filter=Q(pieces__type=piece_type)),
                0
            )
        }
    )
    # Apply the filter if quantity is provided
    try:
        if quantity is not None:
            quantity = int(quantity)
    except (ValueError, TypeError):
        return queryset
    try:
        filter_lookup = {f'{annotation_name}__{comparator}': quantity}
        queryset = queryset.filter(**filter_lookup)
    except FieldError:
        pass
    return queryset

def filter_by_field_quantity(queryset, *, field_type, quantity=None, comparator='exact'):
    """
    Filters Posts by the total quantity of a given field.
    Input:
        queryset: The base Post queryset.
        field_type: the name of the field (eg. clearings, card_amount, forests, building_slots)
        quantity: An integer value to compare against.
        comparator: One of 'lt', 'lte', 'gt', 'gte', 'exact'.
    Output:
        filtered Posts
    """
 
    print(f'Comparator:{comparator}, Field Type:{field_type}, Quantity:{quantity}')
    # Safety check for valid comparator otherwise return qs
    if comparator not in ['lt', 'lte', 'gt', 'gte', 'exact']:
        return queryset
    # Apply the filter if quantity is provided
    try:
        if quantity is not None:
            quantity = int(quantity)
    except (ValueError, TypeError):
        return queryset
    
    try:
        filter_lookup = {f'{field_type}__{comparator}': quantity}
        print(filter_lookup)
        queryset = queryset.filter(**filter_lookup)
    except FieldError:
        pass

    return queryset


def annotate_with_translations(qs, language_object):
    return qs.annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_title')[:1],
                output_field=models.CharField()
            ),
            'title'
        ),
        selected_description=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_description')[:1],
                output_field=models.TextField()
            ),
            'description'
        ),
        selected_lore=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_lore')[:1],
                output_field=models.TextField()
            ),
            'lore'
        )
    ).distinct().order_by('sorting', '-official', 'status', '-date_posted', 'id')


def paginate(queryset, page_number):
    paginator = Paginator(queryset, settings.PAGE_SIZE)
    try:
        return paginator.page(page_number)
    except (PageNotAnInteger, EmptyPage, ValueError):
        return paginator.page(1)

COMPONENT_TYPE_MAP = {
    "map": Post.ComponentChoices.MAP,
    "deck": Post.ComponentChoices.DECK,
    "faction": Post.ComponentChoices.FACTION,
    "vagabond": Post.ComponentChoices.VAGABOND,
    "landmark": Post.ComponentChoices.LANDMARK,
    "hireling": Post.ComponentChoices.HIRELING,
    "clockwork": Post.ComponentChoices.CLOCKWORK,
    "tweak": Post.ComponentChoices.TWEAK,
    "houserule": Post.ComponentChoices.TWEAK,  # tweak alias
    "post": "Post",  # special case if you're handling raw posts
}

def advanced_search(request, component_type='faction'):
    component = COMPONENT_TYPE_MAP.get(component_type.lower())
    
    if not component:
        raise Http404("Invalid component type")

    filters = generate_filters(request)

    page = request.GET.get('page', 1)
    view_status = get_view_status(request.user)

    selected_designer = filters.get('designer', "")
    designers = get_designers(request.user, view_status, component, selected_designer)
    selected_artist = filters.get('artist', "")
    artists = get_artists(request.user, view_status, component, selected_artist)
    if component != "Post":
        expansions = Expansion.objects.filter(posts__component=component).distinct()
    else:
        expansions = Expansion.objects.all()

    posts = build_post_queryset(request.user, view_status, component)
    posts = apply_filters(posts, filters, component)

    # Language logic
    used_languages = Language.objects.filter(
        Q(post__component=component) |
        Q(posttranslation__post__component=component)
    ).distinct()
    language_code = filters.get('language_code') or get_language()
    language_object = Language.objects.filter(code=language_code).first() or Language.objects.get(code='en')

    post_count = posts.count()
    posts = annotate_with_translations(posts, language_object)
    posts = paginate(posts, page)
    tabs = ["Faction", "Map", "Deck", "Vagabond", "Landmark", "Hireling", "Clockwork", "Tweak"]
    
    # Meta data
    if component == "Tweak":
        meta_title = "House Rule Search"
    else:
        meta_title = f"{component} Search"
    filter_description = describe_filters(filters=filters)
    if filter_description:
        meta_description = f'Results: {post_count}, {filter_description}'
    else:
        if component == "Clockwork":
            meta_description = f'Search {post_count} Root Clockwork Fan Factions using advanced filters'
        elif component == "Tweak":
            meta_description = f'Search {post_count} Root House Rules using advanced filters'
        else:
            meta_description = f'Search {post_count} Root Fan {component}s using advanced filters'

    # Determine if filters are necessary
    has_piece_type = {
        type_choice.label.lower(): Piece.objects.filter(
            type=type_choice.value,
            parent__component=component
        ).exists()
        for type_choice in Piece.TypeChoices
    }

    # Selected status and available statuses
    selected_status = filters.get('status', "")
    has_status = {
        status_choice.name.lower(): Post.objects.filter(
            component=component,
            status=status_choice.value
        ).exists()
        for status_choice in StatusChoices
    }
    # Add the selected status if it's valid
    if selected_status:
        try:
            selected_enum = StatusChoices(selected_status)  # Convert value to enum
            label_key = selected_enum.label.lower()
            has_status[label_key] = True
        except ValueError:
            pass  # In case selected_status is not a valid enum value


    if component == "Vagabond":
        used_item_choices = [
            {"value": item_choice.value, "label": item_choice.label}
            for item_choice in Vagabond.AbilityChoices
            if Vagabond.objects.filter(component=component, ability_item=item_choice.value).exists()
        ]
    else:
        used_item_choices = {}

    # Selected color and available colors
    selected_color = filters.get('color', '')
    # Build the list of used color choices
    used_color_choices = [
        {"value": color_choice.value, "label": color_choice.label}
        for color_choice in ColorChoices
        if Post.objects.filter(component=component, color_group=color_choice.value).exists()
    ]

    if component in ["Faction", "Hireling", "Vagabond", "Clockwork"]:
        # If the selected color is valid but not in the list, add it
        if selected_color and selected_color not in [c["value"] for c in used_color_choices]:
            try:
                # Get the label from ColorChoices enum
                selected_enum = ColorChoices(selected_color)
                used_color_choices.append({"value": selected_enum.value, "label": selected_enum.label})
            except ValueError:
                pass  # Skip adding if it's not a valid ColorChoice
    else:
        selected_color = ""

    theme = get_theme(request)
    if selected_color:
        initial_color = selected_color
    else:
        initial_color = theme.theme_color

    context = {
        "initial_color": initial_color,
        "posts": posts,
        "designers": designers,
        "artists": artists,
        "expansions": expansions,
        "used_languages": used_languages,
        "language_code": language_code,
        "tabs": tabs,
        "is_search_view": True,
        "component_type": component,
        "meta_title": meta_title,
        "meta_description": meta_description,
        "has_piece_type": has_piece_type,
        "has_status": has_status,
        "color_choices": used_color_choices,


        "search": filters.get('search', ""),
        "status": selected_status,
        "selected_designer": selected_designer,
        "selected_artist": selected_artist,
        "selected_expansion": filters.get('expansion', ""),
        "selected_language": filters.get('language_code', ""),
        "color": selected_color,
        "animal": filters.get('animal', ''),
        "official": filters.get('official', ''),

        # Winrate
        "winrate_min": filters.get('winrate_min', ''),
        "winrate_max": filters.get('winrate_max', ''),

        # Games Played
        'games_qty': filters.get('games_qty', ''),
        'games_op': filters.get('games_op', ''),        

        # Faction Specific
        "faction_style_choices": Faction.StyleChoices.choices,
        "faction_type": filters.get('faction_type', ""),
        "reach_qty": filters.get('reach_qty', ""),
        "reach_op": filters.get('reach_op', ""),
        'aggression': filters.get('aggression', ''),
        'complexity': filters.get('complexity', ''),
        'card_wealth': filters.get('card_wealth', ''),
        'crafting_ability': filters.get('crafting_ability', ''),
        # Map
        'clearings_qty': filters.get('clearings_qty', ''),
        'clearings_op': filters.get('clearings_op', ''),
        'forests_qty': filters.get('forests_qty', ''),
        'forests_op': filters.get('forests_op', ''),
        'river_clearings_qty': filters.get('river_clearings_qty', ''),
        'river_clearings_op': filters.get('river_clearings_op', ''),
        'building_slots_qty': filters.get('building_slots_qty', ''),
        'building_slots_op': filters.get('building_slots_op', ''),
        'ruins_qty': filters.get('ruins_qty', ''),
        'ruins_op': filters.get('ruins_op', ''),
        # Deck
        'card_total_qty': filters.get('card_total_qty', ''),
        'card_total_op': filters.get('card_total_op', ''),
        # Hireling
        'hireling_type': filters.get('hireling_type', ''),
        # Vagabond
        'item_choices': used_item_choices,
        'ability': filters.get('ability', ''),
        'ability_description': filters.get('ability_description', ''),
        'ability_item': filters.get('ability_item', ''),

        'coin_qty': filters.get('coin_qty', ''),
        'coin_op': filters.get('coin_op', ''),
        'boot_qty': filters.get('boot_qty', ''),
        'boot_op': filters.get('boot_op', ''),
        'bag_qty': filters.get('bag_qty', ''),
        'bag_op': filters.get('bag_op', ''),
        'tea_qty': filters.get('tea_qty', ''),
        'tea_op': filters.get('tea_op', ''),
        'hammer_qty': filters.get('hammer_qty', ''),
        'hammer_op': filters.get('hammer_op', ''),
        'crossbow_qty': filters.get('crossbow_qty', ''),
        'crossbow_op': filters.get('crossbow_op', ''),
        'sword_qty': filters.get('sword_qty', ''),
        'sword_op': filters.get('sword_op', ''),
        'torch_qty': filters.get('torch_qty', ''),
        'torch_op': filters.get('torch_op', ''),
        # Landmark

        # Pieces
        "warrior_qty": filters.get('warrior_qty', ""),
        "warrior_op": filters.get('warrior_op', ""),
        "building_qty": filters.get('building_qty', ""),
        "building_op": filters.get('building_op', ""),
        "token_qty": filters.get('token_qty', ""),
        "token_op": filters.get('token_op', ""),
        "card_qty": filters.get('card_qty', ""),
        "card_op": filters.get('card_op', ""),
        "other_qty": filters.get('other_qty', ""),
        "other_op": filters.get('other_op', ""),
    }

    if request.htmx:
        return render(request, "the_keep/partials/advanced_search_results.html", context)    
    else:
        return render(request, "the_keep/advanced_search.html", context)

# END ADVANCED SEARCHES


@editor_required
def add_piece(request, id=None):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    if id:
        obj = get_object_or_404(Piece, id=id)
        #Check if user owns this object
        if obj.parent.designer!=request.user.profile and not request.user.profile.admin:
            raise PermissionDenied() 
    else:
        obj = Piece()  # Create a new Piece instance but do not save it yet

    form = PieceForm(request.POST or None, request.FILES or None, instance=obj)

    piece_type = request.GET.get('piece')
    slug = request.GET.get('slug')

    # parent = Post.objects.get(slug=slug)
    parent = get_object_or_404(Post, slug=slug)

    context = {
        'form': form,
        'object': parent,
        'piece_type': piece_type,
        'piece': obj,
    }

    if request.method == 'POST':
        if form.is_valid():
            # Form is valid, save the piece
            child = form.save(commit=False)
            child.parent = parent
            child.type = piece_type
            child.save()

            # Return a partial to indicate the piece has been updated
            return render(request, 'the_keep/partials/piece_line.html', context)
        else:
            # If form is not valid, it will still return the form with error messages
            return render(request, 'the_keep/partials/piece_add.html', context)  # Render the form with error messages
    
    # If GET request, render the form without errors (initial state)

    if id:
        return render(request, 'the_keep/partials/piece_update.html', context)
    else:
        return render(request, 'the_keep/partials/piece_add.html', context)

@editor_required
def delete_piece(request, id):
    if not request.htmx:
        raise Http404("Not an HTMX request")
    piece = get_object_or_404(Piece, id=id)
    # Check if user owns this object
    if piece.parent.designer==request.user.profile or request.user.profile.admin:
        piece.delete()
        return HttpResponse('')
    else:
        raise PermissionDenied() 


# This view is used to check the status of a Post and return playtest details. The page is intentionally 'game-ified' to encourage playtests with different components.

def status_check(request, slug):
    # Get the Post object based on the slug from the URL
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    language = get_language()
    language_object = Language.objects.filter(code=language).first()
    object_translation = object.translations.filter(language=language_object).first()
    object_title = object_translation.translated_title if object_translation and object_translation.translated_title else object.title



    stable = object.stable_check()

    stable_ready = stable.stable_ready
    play_count = stable.play_count
    play_threshold = stable.game_threshold
    player_count = stable.unique_players
    player_threshold = stable.player_threshold
    official_faction_count = stable.official_faction_count
    official_faction_threshold = stable.faction_threshold
    official_map_count = stable.official_map_count
    official_map_threshold = stable.map_threshold
    official_deck_count = stable.official_deck_count
    official_deck_threshold = stable.deck_threshold
    official_faction_queryset = stable.official_faction_queryset
    unplayed_faction_queryset = stable.unplayed_faction_queryset
    official_map_queryset = stable.official_map_queryset
    unplayed_map_queryset = stable.unplayed_map_queryset
    official_deck_queryset = stable.official_deck_queryset
    unplayed_deck_queryset = stable.unplayed_deck_queryset

    
    if object.component == 'Faction' or object.component == 'Vagabond' or object.component == 'Clockwork':
        win_count = stable.win_count
        loss_count = stable.loss_count
        if win_count != 0:
            win_completion = '100%'
        else:
            win_completion = '1%'

        if loss_count != 0:
            loss_completion = '100%'
        else:
            loss_completion = '1%'
    else:
        win_count = 0
        loss_count = 0
        win_completion = '100%'
        loss_completion = '100%'

    play_calculation = max(min(100, play_count/play_threshold*100),1)
    if play_count:
        play_calculation = max(play_calculation,24)
    play_completion = f'{play_calculation}%'

    player_calculation = max(min(100, player_count/player_threshold*100),1)
    if player_count:
        player_calculation = max(player_calculation, 24)
    player_completion = f'{player_calculation}%'

    official_faction_calculation = max(min(100, official_faction_count/official_faction_threshold*100),1)
    if official_faction_count:
        official_faction_calculation = max(official_faction_calculation,24)
    official_faction_completion = f'{official_faction_calculation}%'

    faction_icon_width = f'{1/official_faction_threshold*100}%'

    if official_map_threshold != 0:
        official_map_calculation = max(min(100, official_map_count/official_map_threshold*100),1)
    else:
        official_map_calculation = 1
    if official_map_count:
        official_map_calculation = max(official_map_calculation,24)
    official_map_completion = f'{official_map_calculation}%'
    if official_map_threshold:
        map_icon_width = f'{1/official_map_threshold*100}%'
    else:
        map_icon_width = '100%'

    if official_deck_threshold != 0:
        official_deck_calculation = max(min(100, official_deck_count/official_deck_threshold*100),1)
    else:
        official_deck_calculation = 1
    if official_deck_count:
        official_deck_calculation = max(official_deck_calculation,24)
    official_deck_completion = f'{official_deck_calculation}%'

    if official_deck_threshold:
        deck_icon_width = f'{1/official_deck_threshold*100}%'
    else:
        deck_icon_width = '100%'

    total_threshold = play_threshold + player_threshold + official_deck_threshold + official_faction_threshold + official_map_threshold
    if object.component == 'Faction' or object.component == 'Vagabond' or object.component == 'Clockwork':
        total_threshold += 2
    total_count = min(play_count,play_threshold) + min(player_count,player_threshold) + min(official_deck_count,official_deck_threshold) + min(official_faction_count,official_faction_threshold) + min(official_map_count,official_map_threshold) + min(win_count,1) + min(loss_count,1)
    total_completion = max(min(100, total_count / total_threshold * 100),1)
    if play_count:
        total_completion = max(total_completion,16)
    total_completion = f'{total_completion}%'

    if object.color:
        object_color = object.color
    else:
        if request.user.is_authenticated:
            if request.user.profile.theme:
                object_color = request.user.profile.theme.theme_color
            else:
                object_color = "#5f788a"
        else:
            object_color = "#5f788a"

    context = {
        'object': object,
        'object_color': object_color,
        'object_component': object.component,
        'object_title': object_title,
        'stable_ready': stable_ready,

        'play_count': play_count,
        'play_threshold': play_threshold,
        'play_completion': play_completion,

        'player_count': player_count,
        'player_threshold': player_threshold,
        'player_completion': player_completion,

        'official_faction_count': official_faction_count,
        'official_faction_threshold': official_faction_threshold,
        'official_faction_completion': official_faction_completion,
        'official_faction_queryset': official_faction_queryset,
        'unplayed_faction_queryset': unplayed_faction_queryset,
        'faction_icon_width': faction_icon_width,

        'official_map_count': official_map_count,
        'official_map_threshold': official_map_threshold,
        'official_map_completion': official_map_completion,
        'official_map_queryset': official_map_queryset,
        'unplayed_map_queryset': unplayed_map_queryset,
        'map_icon_width': map_icon_width,

        'official_deck_count': official_deck_count,
        'official_deck_threshold': official_deck_threshold,
        'official_deck_completion': official_deck_completion,
        'official_deck_queryset': official_deck_queryset,
        'unplayed_deck_queryset': unplayed_deck_queryset,
        'deck_icon_width': deck_icon_width,

        'win_count': win_count,
        'win_completion': win_completion,

        'loss_count': loss_count,
        'loss_completion': loss_completion,

        'total_count': total_count,
        'total_threshold': total_threshold,
        'total_completion': total_completion,
    }

    return render(request, 'the_keep/status_check.html', context)


# This view is so that the owner of a Post can mark it as Stable directly
@player_required
def confirm_stable(request, slug):
    # Get the Post object based on the slug from the URL
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    stable = object.stable_check()
    # print(stable)
    # if stable[0] == False:
    if stable.stable_ready == False:
        messages.info(request, _('{} has not yet met the stability requirements.').format(object))
        return redirect('status-check', slug=object.slug)
    
    # Check if the current user is the designer
    if object.designer != request.user.profile:
        messages.error(request, _("You are not authorized to make this change."))
        return redirect('status-check', slug=object.slug)
    

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Update the `stable` property to True
        object.status = 1
        object.save()

        # Redirect to a success page or back to the post detail page
        messages.success(request, _('{} has been marked as "Stable".').format(object.title))
        return redirect(object.get_absolute_url())

    # If GET request, render the confirmation form
    form = StatusConfirmForm()
    context = {
        'form': form, 
        'post': object,
        'post_title': post.title,
        'post_component': post.component,
    }
    return render(request, 'the_keep/confirm_stable.html', context)

# This view is so that the owner of a Post can mark it as Testing directly
@player_required
def confirm_testing(request, slug):
    # Get the Post object based on the slug from the URL
    post = get_object_or_404(Post, slug=slug)
    component_mapping = {
            "Map": Map,
            "Deck": Deck,
            "Landmark": Landmark,
            "Tweak": Tweak,
            "Hireling": Hireling,
            "Vagabond": Vagabond,
            "Faction": Faction,
            "Clockwork": Faction,
        }
    Klass = component_mapping.get(post.component)
    object = get_object_or_404(Klass, slug=slug)

    testing = object.get_games_queryset().count() > 0

    # print(stable)

    if testing == False:
        messages.info(request, _('{} has not yet recorded a playtest.').format(object))
        return redirect('status-check', slug=object.slug)
    
    # Check if the current user is the designer
    if object.designer != request.user.profile:
        messages.error(request, _("You are not authorized to make this change."))
        return redirect('status-check', slug=object.slug)

    # If form is submitted (POST request)
    if request.method == 'POST':
        # Update the `testing` property to True
        object.status = 2
        object.save()

        # Redirect to a success page or back to the post detail page
        messages.success(request, _('{} has been marked as "Testing".').format(object.title))
        return redirect(object.get_absolute_url())

    # If GET request, render the confirmation form
    form = StatusConfirmForm()
    context = {
        'form': form, 
        'post': object,
        'post_title': post.title,
        'post_component': post.component,
    }
    return render(request, 'the_keep/confirm_testing.html', context)



#### PNP Assets

@player_required_class_based_view
class PNPAssetCreateView(CreateView):
    model = PNPAsset
    form_class = PNPAssetCreateForm
    template_name = 'the_keep/asset_form.html'

    def form_valid(self, form):
        # Set the 'shared_by' field to the current user's profile
        if not self.request.user.profile.admin:
            form.instance.shared_by = self.request.user.profile
         # Unpin resource
        form.instance.pinned = False
        return super().form_valid(form)

    def get_form_kwargs(self):
        # Add the current user to the form kwargs
        kwargs = super().get_form_kwargs()
        kwargs['profile'] = self.request.user.profile
        return kwargs    
    
    def get_success_url(self):
        asset = self.object
        fields = [
            {'name': 'Posted by:', 'value': self.request.user.profile.name},
        ]
        send_rich_discord_message(f'{asset}', category='FAQ Law', title='New Asset', fields=fields)
        # Redirect to the asset list
        return reverse_lazy('asset-list') 




@player_required_class_based_view
class PNPAssetUpdateView(UpdateView):
    model = PNPAsset
    form_class = PNPAssetCreateForm  # Reusing the form
    template_name = 'the_keep/asset_form.html'


    def form_valid(self, form):
        # Unpin resource
        # Access the old link value from the model instance
        old_link = form.instance.pk and PNPAsset.objects.get(pk=form.instance.pk).link

        # Check if the link has changed
        if old_link != form.cleaned_data['link']:
            # If the link has changed, set pinned to False
            form.instance.pinned = False
         
        return super().form_valid(form)

    # Optionally, override `get_object` to ensure permissions or ownership checks
    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
                # Ensure the current user can update the object
        if obj.shared_by != self.request.user.profile and not self.request.user.profile.admin:
            raise PermissionDenied("You are not authorized to edit this resource.")
        return obj

    def get_form_kwargs(self):
        # Add the current user to the form kwargs
        kwargs = super().get_form_kwargs()
        kwargs['profile'] = self.request.user.profile
        return kwargs 

    def get_success_url(self):
        asset = self.object
        fields = [
            {'name': 'Edited by:', 'value': self.request.user.profile.name},
        ]
        send_rich_discord_message(f'{asset}', category='FAQ Law', title='Edited Asset', fields=fields)
        # Redirect to the asset list
        return reverse_lazy('asset-list') 

class PNPAssetDetailView(DetailView):
    model = PNPAsset
    context_object_name = 'resource'
    template_name = 'the_keep/resource_detail.html'



class PNPAssetListView(ListView):
    model = PNPAsset
    template_name = 'the_keep/asset_list.html'
    context_object_name = 'objects'

        
        # Filter the queryset if there is a search query
    def get_queryset(self):
        queryset = super().get_queryset()

        player_slug = self.kwargs.get('slug')

        if player_slug:
            # Filter for assets shared by player
            queryset = queryset.filter(shared_by__slug=player_slug)
        else:
            # Filter for only pinned assets
            queryset = queryset.filter(pinned=True)
        
        # Get the search query from the GET parameters
        search_query = self.request.GET.get('search', '')
        search_type = self.request.GET.get('search_type', '')
        file_type = self.request.GET.get('file_type', '')

        # If a search query is provided, filter the queryset based on title, category, and shared_by
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(shared_by__discord__icontains=search_query)
            )
        if search_type:
            queryset = queryset.filter(category__icontains=search_type)

        if file_type:
            queryset = queryset.filter(file_type__icontains=file_type)

        return queryset


    def get_context_data(self, **kwargs):
        # Add current user to the context data
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = self.request.user.profile  # Adding the user to the context
            context['shared_assets'] = PNPAsset.objects.filter(shared_by__slug=self.request.user.profile.slug)
            # theme = self.request.user.profile.theme
        else:
            context['profile'] = None
            context['shared_assets'] = None
            # theme = None
        theme = get_theme(self.request)
        # background_image = BackgroundImage.objects.filter(theme=theme, page="resources").order_by('?').first()
        # all_foreground_images = ForegroundImage.objects.filter(theme=theme, page="resources")
        # # Group the images by location
        # grouped_by_location = groupby(sorted(all_foreground_images, key=lambda x: x.location), key=lambda x: x.location)
        # # Select a random image from each location
        # foreground_images = [random.choice(list(group)) for _, group in grouped_by_location]
        background_image, foreground_images, theme_artists, background_pattern = get_thematic_images(theme=theme, page='resources')
        context['background_image'] = background_image
        context['foreground_images'] = foreground_images
        context['background_pattern'] = background_pattern
        # context['theme_artists'] = theme_artists

        
        # Get the search query from the GET parameters
        search_query = self.request.GET.get('search', '')
        search_type = self.request.GET.get('search_type', '')
        file_type = self.request.GET.get('file_type', '')
        if search_query or search_type or file_type:
            queryset = PNPAsset.objects.filter(pinned=False)
        else:
            queryset = PNPAsset.objects.none()

        # If a search query is provided, filter the queryset based on title, category, and shared_by
        if search_query:
            queryset = queryset.filter(
                Q(title__icontains=search_query) |
                Q(shared_by__discord__icontains=search_query)
            )
        if search_type:
            queryset = queryset.filter(category__icontains=search_type)

        if file_type:
            queryset = queryset.filter(file_type__icontains=file_type)

     

        context['unpinned_assets'] = queryset

        return context
    
    def render_to_response(self, context, **response_kwargs):

        # Check if it's an HTMX request
        if self.request.headers.get('HX-Request') == 'true':
            # Only return the part of the template that HTMX will update
            return render(self.request, 'the_keep/partials/asset_list_table.html', context)

        if self.request.user.is_authenticated:
            send_discord_message(f'[{self.request.user}]({build_absolute_uri(self.request, self.request.user.profile.get_absolute_url())}) viewing The Workshop')
        else:
            send_discord_message(f'{get_uuid(self.request)} viewing The Workshop')

        return super().render_to_response(context, **response_kwargs)
    
@player_required_class_based_view
class PNPAssetDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = PNPAsset
    template_name = 'the_keep/asset_confirm_delete.html'
    success_url = reverse_lazy('asset-list')   # The default success URL after deletion

    def test_func(self):
        asset = self.get_object()
        # print("testing delete function")
        return self.request.user.profile == asset.shared_by or self.request.user.profile.admin  # Ensure only the designer can delete

    def post(self, request, *args, **kwargs):
        # print('Trying to delete')
        asset = self.get_object()
        name = asset.title
        try:
            # Attempt to delete the asset
            response = self.delete(request, *args, **kwargs)
            # Add success message upon successful deletion
            messages.success(request, f"The asset link '{name}' was successfully deleted.")
            return response
        except ProtectedError:
            # Handle the case where the deletion fails due to foreign key protection
            messages.error(request, f"The asset link '{name}' cannot be deleted.")
            # Redirect back to the asset detail page
            return redirect('asset-list')
        except IntegrityError:
            # Handle other integrity errors (if any)
            messages.error(request, "An error occurred while trying to delete this asset.")
            return redirect('asset-list')
        

@admin_required
def pin_asset(request, id):

    object = get_object_or_404(PNPAsset, id=id)
    asset_pinned = object.pinned
    if asset_pinned:
        object.pinned = False
    else:
        object.pinned = True
    object.save()

    return render(request, 'the_keep/partials/asset_pins.html', {'obj': object })



def search_translatable_posts(model, query, language_object, view_status):
    return model.objects.filter(
        Q(title__icontains=query) |
        Q(designer__display_name__icontains=query) |
        Q(designer__discord__icontains=query) |
        Q(translations__translated_title__icontains=query, translations__language=language_object),
        status__lte=view_status
    ).annotate(
        selected_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('pk'),
                    language=language_object
                ).values('translated_title')[:1],
                output_field=models.CharField()
            ),
            'title'
        )
    ).distinct().order_by('status')


def universal_search(request):
    query = request.GET.get('query', '')
    
    
    if request.user.is_authenticated:
        view_status = request.user.profile.view_status
    else:
        view_status = 4

    # If the query is empty, set all results to empty QuerySets
    scorecards = ScoreCard.objects.none()
    translations = PostTranslation.objects.none()
    translated_posts = Post.objects.none()

    language = get_language()
    language_object = Language.objects.filter(code=language).first()

    if not query:
        factions = Faction.objects.none()
        maps = Map.objects.none()
        decks = Deck.objects.none()
        vagabonds = Vagabond.objects.none()
        landmarks = Landmark.objects.none()
        hirelings = Hireling.objects.none()
        tweaks = Tweak.objects.none()
        expansions = Expansion.objects.none()
        players = Profile.objects.none()
        games = Game.objects.none()
        tournaments = Tournament.objects.none()
        rounds = Round.objects.none()
        resources = PNPAsset.objects.none()
        pieces = Piece.objects.none()
        color_group = None
        laws = Law.objects.none()
        bot_laws = Law.objects.none()
        fan_laws = Law.objects.none()
    else:
        # If the query is not empty, perform the search as usual
        factions = search_translatable_posts(Faction, query, language_object, view_status)
        maps = search_translatable_posts(Map, query, language_object, view_status)
        decks = search_translatable_posts(Deck, query, language_object, view_status)
        vagabonds = search_translatable_posts(Vagabond, query, language_object, view_status)
        landmarks = search_translatable_posts(Landmark, query, language_object, view_status)
        hirelings = search_translatable_posts(Hireling, query, language_object, view_status)
        tweaks = search_translatable_posts(Tweak, query, language_object, view_status)

        expansions = Expansion.objects.filter(
            Q(title__icontains=query)|Q(designer__display_name__icontains=query)|Q(designer__discord__icontains=query))
        players = Profile.objects.filter(Q(display_name__icontains=query)|Q(discord__icontains=query)|Q(dwd__icontains=query))
        
        if request.user.is_authenticated:
            if request.user.profile.player:
                scorecards = ScoreCard.objects.filter(game_group__icontains=query, effort=None, recorder=request.user.profile)
        
        games = Game.objects.filter(nickname__icontains=query)     
        tournaments = Tournament.objects.filter(name__icontains=query, start_date__lte=timezone.now())  
        rounds = Round.objects.filter(Q(name__icontains=query)|Q(tournament__name__icontains=query), start_date__lte=timezone.now())   
        resources = PNPAsset.objects.filter(Q(title__icontains=query)|Q(shared_by__display_name__icontains=query)|Q(shared_by__discord__icontains=query), pinned=True)
        pieces = Piece.objects.filter(Q(name__icontains=query), parent__status__lte=view_status).order_by('parent__status')
        color_group = ColorChoices.get_color_by_name(color_name=query)
        if len(query) > 3:
            translations = PostTranslation.objects.filter(translated_title__icontains=query).exclude(language=language_object)
            translated_posts = Post.objects.filter(
                title__icontains=query,
                language__isnull=False,
                status__lte=view_status,
                translations__isnull=False).exclude(language=language_object).distinct().order_by('status')
            
        # Laws, look for exact matches first, then contains matches
        laws = Law.objects.filter(
            language__code=language,
            group__type__in=['Official', 'Appendix'],
            group__public=True
        ).filter(
            (Q(plain_title__iexact=query) | Q(law_code__iexact=query) | Q(plain_description__iexact=query))
        )
        if not laws:
            laws = Law.objects.filter(
                language__code=language,
                group__type__in=['Official', 'Appendix'],
                group__public=True
            ).filter(
                (Q(plain_title__icontains=query) | Q(plain_description__icontains=query))
            )

        bot_laws = Law.objects.filter(
            language__code=language,
            group__type='Bot',
            group__public=True
        ).filter(
            (Q(plain_title__iexact=query) | Q(law_code__iexact=query))
        )
        if not bot_laws:
            bot_laws = Law.objects.filter(
                language__code=language,
                group__type='Bot',
                group__public=True
            ).filter(
                (Q(plain_title__icontains=query))
            )

        fan_laws = Law.objects.filter(
            language__code=language,
            group__type='Fan',
            group__public=True
        ).filter(
            (Q(plain_title__iexact=query) | Q(law_code__iexact=query))
        )
        if not fan_laws:
            fan_laws = Law.objects.filter(
                language__code=language,
                group__type='Fan',
                group__public=True
            ).filter(
                (Q(plain_title__icontains=query))
            )

    if color_group:
        color_count = 1
    else:
        color_count = 0

    total_results = (factions.count() + maps.count() + decks.count() + vagabonds.count() +
                     landmarks.count() + hirelings.count() + expansions.count() + 
                     players.count() + games.count() + scorecards.count() + 
                     tournaments.count() + rounds.count() + tweaks.count() + 
                     resources.count() + pieces.count() + color_count + translations.count() + translated_posts.count() +
                     laws.count() + bot_laws.count() + fan_laws.count()
                     )
    
    no_results = total_results == 0

    if total_results < 10:
        result_count = total_results
    elif total_results < 16:
        result_count = 4
    else:
        result_count = 3

    context = {
        'factions': factions[:result_count],
        'maps': maps[:result_count],
        'decks': decks[:result_count],
        'vagabonds': vagabonds[:result_count],
        'landmarks': landmarks[:result_count],
        'hirelings': hirelings[:result_count],
        'tweaks': tweaks[:result_count],
        'expansions': expansions[:result_count],
        'players': players[:result_count],
        'games': games[:result_count],
        'scorecards': scorecards[:result_count],
        'tournaments': tournaments[:result_count],
        'rounds': rounds[:result_count],
        'resources': resources[:result_count],
        'pieces': pieces[:result_count],
        'color_group': color_group,
        'translations': translations[:result_count],
        'translated_posts': translated_posts[:result_count],
        'laws': laws[:result_count],
        'bot_laws': bot_laws[:result_count],
        'fan_laws': fan_laws[:result_count],
        'no_results': no_results,
        'query': query,
    }

    return render(request, 'the_keep/partials/universal_results.html', context)

# Law of Root views
def with_neighbors(laws):
    neighbors = []
    for i, law in enumerate(laws):
        prev = laws[i - 1] if i > 0 else None
        next = laws[i + 1] if i + 1 < len(laws) else None
        law.prev_law = prev
        law.next_law = next
        neighbors.append(law)
    return neighbors

def apply_neighbors_recursively(laws):
    laws = list(laws)  # Make sure it's a list for indexing
    laws = with_neighbors(laws)
    for law in laws:
        children = sorted(law.children.all(), key=lambda c: c.position)
        
        apply_neighbors_recursively(children)
    return laws

def assign_full_urls(laws, request):
    for law in laws:
        law.full_url = request.build_absolute_uri(law.get_absolute_url())
        if hasattr(law, 'children'):
            assign_full_urls(law.children.all(), request)


@editor_required
def add_law_ajax(request):

    user_profile = request.user.profile
    if request.method == 'POST':

        form = AddLawForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            group = get_object_or_404(LawGroup, id=data['group_id'])
            parent = Law.objects.filter(id=data['parent_id']).first()
            previous = Law.objects.filter(id=data['prev_id']).first()
            next_law = Law.objects.filter(id=data['next_id']).first()
          
            language = get_object_or_404(Language, id=data['language_id'])
       
            reference_ids = request.POST.getlist('reference_laws')
            reference_laws = Law.objects.filter(id__in=reference_ids)

            if group.post:
                if group.post.designer != user_profile and not user_profile.admin:
                    messages.error(request, f"You do not have authorization to add laws to {group}.")
                    raise PermissionDenied() 
            else:
                if not user_profile.admin:
                    messages.error(request, f"You do not have authorization to add to the Law of Root.")
                    raise PermissionDenied() 
                
            position = Law.get_new_position(language=language, previous_law=previous, next_law=next_law, parent_law=parent)

            law = Law.objects.create(
                group=group,
                parent=parent,
                title=data['title'],
                description=data['description'],
                position=position,
                language=language
            )
            if reference_laws:
                law.reference_laws.set(reference_laws)
            law.rebuild_law_codes(group, parent, position)

            return JsonResponse({'status': 'success', 'law_id': law.id})
        else:
            print('bad form')
            for field, errors in form.errors.items():
                print(f"{field}: {errors}")
            return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@editor_required
def move_law_ajax(request, law_id, direction):
    user_profile = request.user.profile
    if request.method == 'POST':
        law = get_object_or_404(Law, id=law_id)

        if law.group.post:
            if law.group.post.designer != user_profile and not user_profile.admin:
                messages.error(request, f"You do not have authorization to move laws in {law.group}.")
                raise PermissionDenied() 
        else:
            if not user_profile.admin:
                messages.error(request, f"You do not have authorization to move in the Law of Root.")
                raise PermissionDenied() 

        # Get all sibling laws in order
        siblings = list(
            Law.objects.filter(group=law.group, parent=law.parent, prime_law=False, language=law.language)
            .order_by('position')
        )

        # Annotate with prev/next
        siblings = with_neighbors(siblings)

        # Find the same law in the updated list
        law = next(l for l in siblings if l.id == law_id)

        if direction == 'up' and law.prev_law:
            new_pos = Law.get_new_position(
                language=law.language,
                previous_law=law.prev_law.prev_law,
                next_law=law.prev_law,
                parent_law=law.parent
            )
        elif direction == 'down' and law.next_law:
            new_pos = Law.get_new_position(
                language=law.language,
                previous_law=law.next_law,
                next_law=law.next_law.next_law,
                parent_law=law.parent
            )
        else:
            return JsonResponse({'status': 'error'}, status=400)

        law.position = new_pos
        law.save()
 
        # Find the other law involved in the swap
        swapped_with = law.prev_law if direction == 'up' else law.next_law

        # Recalculate both law codes and their descendant codes
        law.update_code_and_descendants()
        swapped_with.update_code_and_descendants()
        
        return JsonResponse({'status': 'success', 'law_id': law.id})
    return JsonResponse({'status': 'error'}, status=400)


@editor_required
def edit_law_ajax(request):
    user_profile = request.user.profile
    if request.method == 'POST':
        law = get_object_or_404(Law, id=request.POST.get('law_id'))
        if law.group.post:
            if law.group.post.designer != user_profile and not user_profile.admin:
                messages.error(request, f"You do not have authorization to edit laws in {law.group}.")
                raise PermissionDenied() 
        else:
            if not user_profile.admin:
                messages.error(request, f"You do not have authorization to edit the Law of Root.")
                raise PermissionDenied() 

        form = EditLawForm(request.POST, instance=law)
        if form.is_valid():
            form.save()
            return JsonResponse({'status': 'success'})
        else:
            return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)

    return JsonResponse({'status': 'error'}, status=400)

@editor_required
def edit_law_description_ajax(request):
    user_profile = request.user.profile
    if request.method == 'POST':
        law = get_object_or_404(Law, id=request.POST.get('law_id'))
        if law.group.post:
            if law.group.post.designer != user_profile and not user_profile.admin:
                messages.error(request, f"You do not have authorization to edit laws in {law.group}.")
                raise PermissionDenied() 
        else:
            if not user_profile.admin:
                messages.error(request, f"You do not have authorization to edit the Law of Root.")
                raise PermissionDenied() 

        form = EditLawDescriptionForm(request.POST, instance=law)
        if form.is_valid():
            form.save()
            return JsonResponse({'status': 'success'})
        else:
            return JsonResponse({'status': 'error', 'errors': form.errors}, status=400)

    return JsonResponse({'status': 'error'}, status=400)


@editor_required
def delete_law_ajax(request):
    user_profile = request.user.profile
    if request.method == 'POST':
        law_id = request.POST.get('law_id')
        if not law_id:
            return JsonResponse({'status': 'error', 'message': 'No ID provided'}, status=400)

        law = get_object_or_404(Law, id=law_id)
        if law.group.post:
            if law.group.post.designer != user_profile and not user_profile.admin:
                messages.error(request, f"You do not have authorization to delete laws in {law.group}.")
                raise PermissionDenied() 
        else:
            if not user_profile.admin:
                messages.error(request, f"You do not have authorization to delete the Law of Root.")
                raise PermissionDenied() 
        law.delete()

        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)


@editor_onboard_required
def create_law_group(request, slug):
    post = get_object_or_404(Post, slug=slug)


    user_profile = request.user.profile
    if post.designer != user_profile and not user_profile.admin:
        messages.error(request, f"You do not have authorization to create laws for {post}.")
        raise PermissionDenied()


    # Determine which languages are available (don't already have a LawGroup)
    existing_group = LawGroup.objects.filter(post=post).first()

    if existing_group:
        existing_law = Law.objects.filter(group=existing_group).first()
        if existing_law:
            return redirect('law-view', slug=existing_law.group.slug, lang_code=existing_law.language.code)

    available_languages = Language.objects.all()


    if request.method == 'POST':
        form = LanguageSelectionForm(request.POST)
        form.fields['language'].queryset = available_languages
        if form.is_valid():
            language = form.cleaned_data['language']

            # Determine related model
            component_mapping = {
                "Map": Map,
                "Deck": Deck,
                "Landmark": Landmark,
                "Tweak": Tweak,
                "Hireling": Hireling,
                "Vagabond": Vagabond,
                "Faction": Faction,
                "Clockwork": Faction,
            }
            Klass = component_mapping.get(post.component)
            obj = get_object_or_404(Klass, slug=post.slug)

            translation = PostTranslation.objects.filter(post=post, language=language).first()
            new_title = translation.translated_title if translation else post.title

            # LawGroup creation or redirect
            group, created = LawGroup.objects.get_or_create(
                post=post,
                defaults={'title': new_title}
            )
            if not created and group.laws.exists():
                return redirect('law-view', slug=group.slug, lang_code=language.code)

            # Prime law
            Law.objects.create(
                group=group,
                title=group.title,
                description="",
                position=1,
                locked_position=True,
                allow_description=False,
                prime_law=True,
                language=language,
            )

            # Overview law
            Law.objects.create(
                group=group,
                title=get_translated_title("Overview", language.code),
                description="",
                position=1,
                locked_position=True,
                allow_sub_laws=False,
                language=language,
            )

            # Component-specific default laws
            if post.component == 'Map':
                Law.objects.create(
                    group=group,
                    title=get_translated_title("Setup Modifications", language.code),
                    description="",
                    position=2,
                    locked_position=True,
                    language=language,
                )

            elif post.component == 'Vagabond':
                Law.objects.create(
                    group=group,
                    title=get_translated_title("Starting Items", language.code),
                    description="",
                    position=2,
                    locked_position=True,
                    language=language,
                )
                Law.objects.create(
                    group=group,
                    title=f"Special Action: {obj.ability}",
                    description=f"{obj.ability_description}",
                    position=3,
                    locked_position=True,
                    language=language,
                )

            elif post.component in ['Faction', 'Clockwork']:
                rules_law = Law.objects.create(
                    group=group,
                    title=get_translated_title("Faction Rules and Abilities", language.code),
                    description="",
                    position=2,
                    locked_position=True,
                    allow_description=False,
                    language=language,
                )
                if post.component == 'Faction':
                    Law.objects.create(
                        group=group,
                        parent=rules_law,
                        title=get_translated_title("Crafting", language.code),
                        description="",
                        position=1,
                        locked_position=True,
                        allow_sub_laws=False,
                        language=language,
                    )

                setup_law = Law.objects.create(
                    group=group,
                    title=get_translated_title("Faction Setup", language.code),
                    description="",
                    position=3,
                    locked_position=True,
                    allow_description=False,
                    language=language,
                )
          
                Law.objects.create(group=group, parent=setup_law, title="Step 1: X", description="", position=1, language=language)
                Law.objects.create(group=group, parent=setup_law, title="Step 2: XX", description="", position=2, language=language)
                Law.objects.create(group=group, parent=setup_law, title="Step 3: XXX", description="", position=3, language=language)
                

                Law.objects.create(group=group, title=get_translated_title("Birdsong", language.code), description="", position=4, locked_position=True, language=language)
                Law.objects.create(group=group, title=get_translated_title("Daylight", language.code), description="", position=5, locked_position=True, language=language)

                evening_law = Law.objects.create(group=group, title=get_translated_title("Evening", language.code), description="", position=6, locked_position=True, language=language)
                if post.component == 'Faction':
                    Law.objects.create(
                        group=group,
                        parent=evening_law,
                        title=get_translated_title("Draw and Discard", language.code),
                        description="Draw one card, plus one per uncovered draw bonus. Then, if you have more than five cards in your hand, discard cards of your choice until you have five.",
                        position=1,
                        language=language
                    )

            # Discord notification
            fields = [{
                'name': 'Posted by:',
                'value': request.user.profile.name
            }]
            send_rich_discord_message(f'{language} Law Created for {obj.title}', category='FAQ Law', title='New Law', fields=fields)

            return redirect('law-view', slug=group.slug, lang_code=language.code)
    else:
        form = LanguageSelectionForm()
        form.fields['language'].queryset = available_languages

    return render(request, 'the_keep/law_group_create.html', {
        'form': form,
        'post': post,
    })

@editor_onboard_required
def update_law_group(request, slug, lang_code):

    language = Language.objects.filter(code=lang_code).first()
    if not language:
        language = Language.objects.filter(code='en').first()

    user_profile = request.user.profile
    source_group = get_object_or_404(LawGroup, slug=slug)
    first_law = Law.objects.filter(group=source_group, language=language).first()
    if first_law:
        lang_code = first_law.language.code
    else:
        raise Http404(f"No laws found in {source_group}.")
    # Authorization
    if source_group.post:
        if source_group.post.designer != user_profile and not user_profile.admin:
            messages.error(request, f"You do not have authorization to edit {source_group}.")
            raise PermissionDenied() 
    else:
        if not user_profile.admin:
            messages.error(request, f"You do not have authorization to edit the Law of Root.")
            raise PermissionDenied() 

    if request.method == 'POST':
        form = EditLawGroupForm(request.POST, instance=source_group, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"{source_group} Law Updated.")
            return redirect('edit-law-view', slug=source_group.slug, lang_code=lang_code)
    else:
        form = EditLawGroupForm(instance=source_group, user=request.user)

    return render(request, 'the_keep/law_group_edit.html', {
        'form': form,
        'source_group': source_group,
        'lang_code': lang_code,
    })


@editor_onboard_required
def delete_law_group(request, slug, lang_code):
    user_profile = request.user.profile
    source_group = get_object_or_404(LawGroup, slug=slug)
    source_language = get_object_or_404(Language, code=lang_code)

    # Authorization
    if source_group.post:
        if source_group.post.designer != user_profile and not user_profile.admin:
            messages.error(request, f"You do not have authorization to delete laws in {source_group}.")
            raise PermissionDenied() 
    else:
        if not user_profile.admin:
            messages.error(request, f"You do not have authorization to delete the Law of Root.")
            raise PermissionDenied() 
        
    # POST: Perform deletion
    if request.method == "POST":
        laws_to_delete = Law.objects.filter(group=source_group, language=source_language)
        count = laws_to_delete.count()
        laws_to_delete.delete()
        messages.success(request, f"Deleted {count} law(s) from {source_group} in {source_language}.")
        if source_group.post:
            return redirect(source_group.post.get_absolute_url()) 
        else:
            return redirect('law-of-root', lang_code=lang_code) 

    # GET: Show confirmation page
    laws_count = Law.objects.filter(group=source_group, language=source_language).count()

    context = {
        "source_group": source_group,
        "source_language": source_language,
        "laws_count": laws_count,
        'slug': slug,
        'lang_code': lang_code,
    }

    return render(request, "the_keep/law_group_delete.html", context)

@editor_onboard_required
def copy_law_group_view(request, slug, lang_code=None):
    user_profile = request.user.profile
    source_group = get_object_or_404(LawGroup, slug=slug)
    if lang_code:
        source_language = get_object_or_404(Language, code=lang_code)
    else:
        first_law = Law.objects.filter(group=source_group).order_by('id').first()
        if not first_law:
            source_language = Language.objects.filter(code='en').first()
        else:
            source_language = first_law.language

    existing_laws = Law.objects.filter(group=source_group, language=source_language)
    if not existing_laws:
        return redirect('law-view', slug=slug, lang_code=lang_code)

    post = source_group.post
    # Admins are always allowed
    if user_profile.admin:
        pass

    # If there's a post, allow only if user is both editor and the designer of that post
    elif source_group.post:
        if not (user_profile.editor and source_group.post.designer == user_profile):
            messages.error(request, f"You do not have authorization to copy '{source_group.title}'.")
            raise PermissionDenied()

    # If no post, only admins are allowed (already checked above)
    else:
        messages.error(request, f"You do not have authorization to copy '{source_group.title}'.")
        raise PermissionDenied()
        

    if request.method == "POST":
        # form = CopyLawGroupForm(request.POST, source_group=source_group)
        form = CopyLawGroupForm(source_group=source_group, data=request.POST)
        if form.is_valid():
            target_language = form.cleaned_data['language']
            duplicate_laws_for_language(source_group, source_language, target_language)
            return redirect('law-view', slug=slug, lang_code=target_language.code)
    else:
        form = CopyLawGroupForm(source_group=source_group)

    return render(request, 'the_keep/law_group_copy.html', {
        'law_group': source_group,
        'form': form,
        'post': post,
        'lang_code': lang_code,
        'source_language': source_language,
    })


def law_table_of_contents(request, lang_code=''):

    query = request.GET.get("q", "")
    filter_type = request.GET.get('type', 'all')

    if not lang_code:
        lang_code = get_language()
    language = Language.objects.filter(code=lang_code, law__isnull=False).first()
    if not language:
        language = Language.objects.first()
    available_languages_qs = Law.objects.filter(group__public=True).exclude(language=language)
    available_languages = Language.objects.filter(
            law__in=available_languages_qs
        ).exclude(id=language.id).distinct()
    if request.user.is_authenticated and request.user.profile.admin:
        laws = Law.objects.filter(language=language)
    elif request.user.is_authenticated:
        laws = Law.objects.filter(
            Q(language=language) & (
                Q(group__public=True) | Q(group__post__designer=request.user.profile)
            )
        )
    else:
        laws = Law.objects.filter(language=language, group__public=True)

    # Filter by type
    # if filter_type == 'official':
    #     laws = laws.filter(Q(group__post__isnull=True) | Q(group__post__official=True))
    # elif filter_type == 'fan':
    #     laws = laws.filter(group__post__official=False)

    if filter_type == 'official':
        laws = laws.filter(Q(group__type='Official') | Q(group__type='Bot') | Q(group__type='Appendix'))
    elif filter_type == 'fan':
        laws = laws.filter(group__type='Fan')

    

    if query:
        search_q = Q(title__icontains=query) | Q(description__icontains=query)
        # official_laws = laws.filter(Q(search_q) & (Q(group__type="Official") | Q(group__type="Appendix"))).distinct()
        # bot_laws = laws.filter(search_q, group__type="Bot").distinct()
        # fan_laws = laws.filter(search_q, group__type="Fan").distinct()

        # Annotate laws with match strength
        laws_with_rank = laws.filter(Q(search_q)).annotate(
            match_rank=Case(
                When(title__iexact=query, then=0),
                When(title__iexact=query+".", then=0),
                When(title__icontains=query, then=1),
                When(description__icontains=query, then=2),
                default=3,
                output_field=IntegerField(),
            )
        ).order_by('match_rank', 'title')

        official_laws = laws_with_rank.filter(
            Q(group__type__in=["Official", "Appendix"])
        )

        bot_laws = laws_with_rank.filter(group__type="Bot")
        fan_laws = laws_with_rank.filter(group__type="Fan")

        # For postgres
        # query_vec = SearchQuery(query)

        # vector = (
        #     SearchVector('title', weight='A') +
        #     SearchVector('description', weight='B')
        # )

        # laws_with_rank = laws.annotate(
        #     search_vector=vector,
        #     search_rank=SearchRank(vector, query_vec)
        # ).filter(
        #     search_vector=query_vec
        # ).order_by('-search_rank')

        # official_laws = laws_with_rank.filter(group__type__in=["Official", "Appendix"])
        # bot_laws      = laws_with_rank.filter(group__type="Bot")
        # fan_laws      = laws_with_rank.filter(group__type="Fan")


    else:
        official_laws = laws.filter(
            Q(prime_law=True) & (Q(group__type="Official") | Q(group__type="Appendix"))
        )
        bot_laws = laws.filter(prime_law=True, group__type="Bot")
        fan_laws = laws.filter(prime_law=True, group__type="Fan")
    

    date_updated = None
    law_link = None
    if official_laws:
        rule_file = RulesFile.objects.filter(language=language, post__isnull=True, status=RulesFile.Status.ACTIVE).first()
        if rule_file:
            date_updated = rule_file.commit_date
            law_link = f'https://rules.ledergames.com/?product=root&locale={language.locale}&printing={rule_file.version}'

    context = {
        # 'laws': laws,
        'official_laws': official_laws,
        'bot_laws': bot_laws,
        'fan_laws': fan_laws,
        'lang_code': language.code,
        'available_languages': available_languages,
        'selected_language': language,
        'query': query,
        'date_updated': date_updated,
        'law_link': law_link,
    }

    if request.htmx:
        return render(request, "the_keep/partials/law_table.html", context)
    return render(request, "the_keep/law_table.html", context)


def get_law_group_context(request, slug, lang_code, edit_mode):

    language = Language.objects.filter(code=lang_code).first()
    if not language:
        language = Language.objects.filter(code='en').first()

    group = get_object_or_404(LawGroup, slug=slug)

    if group.type == LawGroup.TypeChoices.OFFICIAL:
        title_string = "Law of Root"
    elif group.type == LawGroup.TypeChoices.BOT:
        title_string = "Law of Rootbotics"
    elif group.type == LawGroup.TypeChoices.FAN:
        title_string = "Fan Law of Root"
    elif group.type == LawGroup.TypeChoices.APPENDIX:
        title_string = "Law of Root Appendix"


    law_title = f"{title_string} - {group.title}"


    highlight_id = request.GET.get('highlight_law')
    highlight_group_id = request.GET.get('highlight_group')
    
    law_meta_description = None
    law_meta_title = None
    if highlight_id:
        selected_law = Law.objects.filter(id=highlight_id).first()
        if selected_law:
            if selected_law.prime_law:
                law_meta_title = f"{title_string} - " + clean_meta_description(selected_law.title)
            else:
                law_meta_title = f"{title_string} - " + selected_law.group.title + ": " + selected_law.law_code + " '" + clean_meta_description(selected_law.title) + "'"
            law_meta_description = clean_meta_description(selected_law.description)
    elif highlight_group_id:
        selected_group = LawGroup.objects.filter(id=highlight_group_id).first()
        if selected_group:
            law_meta_title = f"{title_string} - " + clean_meta_description(selected_group.title)
            law_meta_description = clean_meta_description(selected_group.description)

    try:
        prime_law = Law.objects.get(group=group, prime_law=True, language=language)
    except Law.DoesNotExist:
        raise Http404("No prime law found for this group and language.")
    except MultipleObjectsReturned:
        raise Http404("Multiple prime laws found; data integrity issue.")

    # Determine other available languages for this context
    available_languages_qs = Law.objects.filter(group__slug=slug).exclude(language=language)

    available_languages = Language.objects.filter(
            law__in=available_languages_qs
        ).exclude(id=language.id).distinct()


    edit_authorized = False
    if request.user.is_authenticated:
        if request.user.profile.admin:
            edit_authorized = True
        elif request.user.profile.editor and group.post:
            if request.user.profile == group.post.designer:
                edit_authorized = True


    raw_top_level = (
        Law.objects
        .filter(group=group, parent__isnull=True, prime_law=False, language=language)
        .order_by('position')
        .select_related('group', 'group__post')
        .prefetch_related(
        'group', 'children__group', 'children__children__group', 'children__children__children__group',
        'reference_laws', 'faqs',
        'children', 'children__reference_laws', 
        'children__children', 'children__children__reference_laws', 
        'children__children__children', 'children__children__children__reference_laws', 
        'children__children__children__children__reference_laws',
        )
    )

    if edit_mode:
        top_level_laws = apply_neighbors_recursively(raw_top_level)
    else:
        top_level_laws = list(raw_top_level)
        assign_full_urls(top_level_laws, request)
    
    previous_group = group.get_previous_by_position(language=language)
    previous_name = previous_group.get_prime_law(language=language) if previous_group else None

    next_group = group.get_next_by_position(language=language)
    next_name = next_group.get_prime_law(language=language) if next_group else None

    date_updated = None
    law_link = None
    if group.type in {group.TypeChoices.OFFICIAL, group.TypeChoices.APPENDIX}:
        rule_file = RulesFile.objects.filter(language=language, post__isnull=True, status=RulesFile.Status.ACTIVE).first()
        if rule_file:
            date_updated = rule_file.commit_date
            law_link = f'https://rules.ledergames.com/?product=root&locale={language.locale}&printing={rule_file.version}'



    return {
        'post': group.post,
        'lang_code': lang_code,
        'highlight_id': highlight_id,
        'highlight_group_id': highlight_group_id,
        'law_meta_description': law_meta_description,
        'law_meta_title': law_meta_title,
        'title': law_title,
        'edit_authorized': edit_authorized,
        'selected_language': language,
        'available_languages': available_languages,
        'previous_group': previous_group,
        'previous_name': previous_name,
        'next_group': next_group,
        'next_name': next_name,
        'prime_law': prime_law,
        'top_level_laws': top_level_laws,
        'group': group,
        'edit_mode': edit_mode,
        'date_updated': date_updated,
        'law_link': law_link,
    }

def law_group_view(request, slug, lang_code):

    if request.user.is_authenticated:
        user_profile = request.user.profile
    else:
        user_profile = None

    group = get_object_or_404(LawGroup, slug=slug)
    access_denied = False
    if not group.public:
        if not user_profile:
            access_denied = True
        elif group.post:
            if not (user_profile.admin or group.post.designer == user_profile):
                access_denied = True
        else:
            if not user_profile.admin:
                access_denied = True

    if access_denied:
        messages.error(request, f"You are not authorized to view the Law of { group.title }.")
        raise PermissionDenied() 
    
    context = get_law_group_context(request, slug=slug, edit_mode=False, lang_code=lang_code)
    
    return render(request, 'the_keep/law_of_root.html', context)

@editor_onboard_required
def law_group_edit_view(request, slug, lang_code):

    user_profile = request.user.profile


    language = Language.objects.filter(code=lang_code).first()
    if not language:
        language = Language.objects.filter(code='en').first()

    group = get_object_or_404(LawGroup, slug=slug)
    if group.post:
        edit_authorized = user_profile.editor and group.post.designer == user_profile
        if not user_profile.admin and not edit_authorized:
            messages.error(request, f"You are not authorized to edit { group.post.title }'s Law.")
            raise PermissionDenied() 
    elif not user_profile.admin:
        messages.error(request, f'You are not authorized to edit the Law of Root.')
        raise PermissionDenied() 
    
    if user_profile.admin:
        all_laws = Law.objects.filter(language=language).prefetch_related('group__post')
    else:
        all_laws = Law.objects.filter(
            Q(group__type="Official") | Q(group__post__designer=user_profile),
            language=language
                ).prefetch_related('group__post')

    context = get_law_group_context(request, slug=slug, edit_mode=True, lang_code=lang_code)
    context['all_laws'] = all_laws

    return render(request, 'the_keep/law_of_root.html', context)


def export_laws_yaml_view(request, group_slug, lang_code):
    try:
        group = LawGroup.objects.get(slug=group_slug)
        language = Language.objects.get(code=lang_code)
        prime_law = Law.objects.filter(group=group, language=language, prime_law=True).first()
        if not prime_law:
            raise Http404("No prime law found.")

        try:
            output = serialize_group(prime_law, include_id=False)
        except NoPrimeLawError:
            raise Http404("No prime law found.")

        # yml_data = yaml.dump(output, allow_unicode=True, sort_keys=False)
        # Adding double quote and wide lines
        yaml.representer.SafeRepresenter.default_style = '"'
        yaml.representer.SafeRepresenter.default_width = 100000  # effectively disables line breaks

        yml_data = yaml.dump(
            output,
            allow_unicode=True,
            sort_keys=False,
            Dumper=QuotedDumper,
            width=100000  
        )

        response = HttpResponse(yml_data, content_type='application/x-yaml')
        filename = f"{group.slug}_{lang_code}.yml"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except LawGroup.DoesNotExist:
        raise Http404("Law group not found.")
    except Language.DoesNotExist:
        raise Http404("Language not found.")

def download_rule(request, rule_id):
    rule = get_object_or_404(RulesFile, pk=rule_id)
    
    try:
        file_path = rule.file.path
        if os.path.exists(file_path):
            response = FileResponse(open(file_path, 'rb'))
            response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
            response['Content-Type'] = 'application/x-yaml'
            return response
        else:
            raise Http404("File not found")
    except Exception as e:
        raise Http404("File not found")



@admin_required
def upload_and_compare_yaml_view(request, group_slug, lang_code):
    if request.method == 'POST':
        form = YAMLUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            uploaded_yaml = yaml.safe_load(uploaded_file)

            try:
                group = LawGroup.objects.get(slug=group_slug)
                language = Language.objects.get(code=lang_code)
                prime_law = Law.objects.filter(group=group, language=language, prime_law=True).first()
                if not prime_law:
                    return HttpResponse("No prime law found.", status=404)

                generated_yaml = serialize_group(prime_law)

                # Compare structures
                mismatches = compare_structure_strict(generated_yaml, uploaded_yaml)

                if not mismatches:
                    update_laws_by_structure(generated_yaml, uploaded_yaml, lang_code)
                    messages.success(request, f'Law for {prime_law.group} successfully updated!')
                    return redirect(prime_law.get_absolute_url())

                return render(request, 'the_keep/law_upload_comparison.html', {
                    'form': form,
                    'mismatches': mismatches,
                })

            except Exception as e:
                return HttpResponse(f"Error: {str(e)}", status=500)

    else:
        form = YAMLUploadForm()

    return render(request, 'the_keep/law_upload_form.html', {'form': form})


@admin_required
def update_official_laws(request, lang_code):
    language = Language.objects.get(code=lang_code)
    if request.method == 'POST':
        form = YAMLUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            try:
                uploaded_yaml = load_uploaded_yaml(uploaded_file)
                created, updated, mismatched, errors, all_mismatches = update_laws_from_yaml(uploaded_yaml, language, messages=request)

                if mismatched or errors:
                    parts = []
                    if errors:
                        parts.append(f"Errors in {', '.join(errors)}")
                    if mismatched:
                        parts.append(f"Mismatches in {', '.join(mismatched)}")
                    messages.error(request, ". ".join(parts) + ". Update aborted.")
                    comparison_report = generate_comparison_markdown(all_mismatches)
                    html_report = markdown.markdown(comparison_report, extensions=['fenced_code', 'tables'])
                    context = {
                        'report': html_report,
                        'mismatched': mismatched,
                        'mismatches': all_mismatches,
                        'errors': errors,
                    }
                    return render(request, 'the_keep/law_upload_comparison.html', context)
                else:
                    parts = []
                    if created:
                        parts.append(f"Created Laws for {', '.join(created)}")
                    if updated:
                        parts.append(f"Updated Laws for {', '.join(updated)}")
                    if parts:
                        messages.success(request, ". ".join(parts) + ".")

                return redirect('law-of-root', lang_code=lang_code)

            except Exception as e:
                return HttpResponse(f"Error processing YAML: {str(e)}", status=500)
    else:
        form = YAMLUploadForm()
    return render(request, 'the_keep/law_upload_form.html', {'form': form})

@admin_required
def manage_law_updates(request):
    new_rules = RulesFile.objects.filter(status=RulesFile.Status.NEW)
    current_rules = RulesFile.objects.filter(status=RulesFile.Status.ACTIVE)

    # Build a dict for the active and new rules
    rules_by_language = defaultdict(lambda: {'active': None, 'new': []})

    # Add active rules
    for rule in current_rules:
        rules_by_language[rule.language.name]['active'] = rule

    # Add new rules
    for rule in new_rules:
        rules_by_language[rule.language.name]['new'].append(rule)

    last_law_check = Website.get_singular_instance().last_law_check

    context = {
        'rules_by_language': dict(rules_by_language),
        'last_law_check': last_law_check,
    }
    return render(request, 'the_keep/law_of_root_select.html', context)

@admin_required
def activate_rule(request):
    if request.method == "POST":
        rule_id = request.POST.get("rule_id")

        rule = get_object_or_404(RulesFile, id=rule_id)

        uploaded_yaml = load_uploaded_yaml(rule.file)
        created, updated, mismatched, errors, all_mismatches = update_laws_from_yaml(uploaded_yaml, rule.language, messages=request)

        if mismatched or errors:
            parts = []
            if errors:
                parts.append(f"Errors in {', '.join(errors)}")
            if mismatched:
                parts.append(f"Mismatches in {', '.join(mismatched)}")
            messages.error(request, ". ".join(parts) + ". Update aborted.")
            comparison_report = generate_comparison_markdown(all_mismatches)
            html_report = markdown.markdown(comparison_report, extensions=['fenced_code', 'tables'])
            context = {
                'report': html_report,
                'mismatched': mismatched,
                'mismatches': all_mismatches,
                'errors': errors,
            }
            return render(request, 'the_keep/law_upload_comparison.html', context)
        else:
            parts = []
            if created:
                parts.append(f"Created Laws for {', '.join(created)}")
            if updated:
                parts.append(f"Updated Laws for {', '.join(updated)}")
            if parts:
                messages.success(request, ". ".join(parts) + ".")
            rule.activate()

    return redirect('manage-law-updates')


@admin_required
def archive_rule(request):
    if request.method == "POST":
        rule_id = request.POST.get("rule_id")

        rule = get_object_or_404(RulesFile, id=rule_id)

        rule.ignore()
        if rule.status == RulesFile.Status.ARCHIVE:
            messages.success(request, f"Ignored rule {rule} moved to Archive")
        else:
            messages.warning(request, f"Failed to Archive rule {rule}")

    return redirect('manage-law-updates')


@admin_required
def check_for_law_updates(request):

    message = sync_rules_task()
    messages.success(request, message)
    return redirect('manage-law-updates')


def law_preview(request, rule_id):
    rule = get_object_or_404(RulesFile, pk=rule_id)
    
    # Read the YAML file content
    try:
        with open(rule.file.path, 'r', encoding='utf-8') as f:
            file_content = f.read()
    except FileNotFoundError:
        file_content = "File not found."

    context = {
        'rule': rule,
        'file_content': file_content,
    }

    return render(request, 'the_keep/law_preview.html', context)


# FAQs FAQ Views

def faq_search(request, slug=None, lang_code=None):
    query = request.GET.get("q", "")

    if not lang_code:
        lang_code = get_language()
    language = get_object_or_404(Language, code=lang_code)
    if not language:
        language = Language.objects.first()

    faq_editable = False
    user_profile = Profile.objects.none()
    if request.user.is_authenticated:
        user_profile = request.user.profile
    if slug:
        post = get_object_or_404(Post, slug=slug)
        faqs = FAQ.objects.filter(post=post, language=language)
        if user_profile:
            if user_profile.admin or user_profile == post.designer and user_profile.editor:
                faq_editable = True
    else:
        post = None
        faqs = FAQ.objects.filter(post=None, language=language)
        if request.user.is_staff:
            faq_editable = True

    if query:
        faqs = faqs.filter(Q(question__icontains=query)|Q(answer__icontains=query))

    # Determine other available languages for this context
    if slug:
        available_languages_qs = FAQ.objects.filter(post=post).exclude(language=language)
    else:
        available_languages_qs = FAQ.objects.filter(
            Q(post=None) | Q(post__official=True)
        ).exclude(language=language)

    available_languages = Language.objects.filter(
            faq__in=available_languages_qs
        ).exclude(id=language.id).distinct()

    unavailable_languages = Language.objects.exclude(
        faq__in=available_languages_qs
    ).exclude(id=language.id).distinct()

    edit_authorized = False

    if request.user.is_staff:
        edit_authorized = True
    elif request.user.is_authenticated and post and request.user.profile.editor:
        if request.user.profile == post.designer:
            edit_authorized = True


    context = {
        "faqs": faqs,
        "post": post,
        'faq_editable': faq_editable,
        'lang_code': lang_code,
        'selected_language': language,
        'available_languages': available_languages,
        'unavailable_languages': unavailable_languages,
        'edit_authorized': edit_authorized,
        }

    if request.htmx:
        return render(request, "the_keep/faq/partials/faq_list.html", context)
    return render(request, "the_keep/faq/website_faq.html", context)


def faq_home(request, lang_code=None):
    query = request.GET.get("q", "")

    if not lang_code:
        lang_code = get_language()
    language = get_object_or_404(Language, code=lang_code)
    if not language:
        language = Language.objects.first()
    faqs = FAQ.objects.filter(post__isnull=False, language=language)



    faqs = faqs.annotate(
        post_title=Coalesce(
            Subquery(
                PostTranslation.objects.filter(
                    post=OuterRef('post_id'),  # reference the related post
                    language=language
                ).values('translated_title')[:1],
                output_field=CharField()
            ),
            F('post__title')  # fallback to the original post title
        )
    )



    if query:
        faqs = faqs.filter(Q(question__icontains=query)|Q(answer__icontains=query)|Q(post__title__icontains=query))

    # Determine other available languages for this context
    available_languages_qs = FAQ.objects.filter(
        Q(post__isnull=False)
    ).exclude(language=language)

    available_languages = Language.objects.filter(
            faq__in=available_languages_qs
        ).exclude(id=language.id).distinct()

    unavailable_languages = Language.objects.exclude(
        faq__in=available_languages_qs
    ).exclude(id=language.id).distinct()


    context = {
        "faqs": faqs,
        'lang_code': lang_code,
        'selected_language': language,
        'available_languages': available_languages,
        'unavailable_languages': unavailable_languages,
        }

    if request.htmx:
        return render(request, "the_keep/faq/partials/global_faq_list.html", context)
    return render(request, "the_keep/faq/global_faq.html", context)

@editor_required_class_based_view
class FAQCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = FAQ
    form_class = FAQForm
    template_name = 'the_keep/faq/faq_form.html'

    def get_post(self):
        if not hasattr(self, '_post'):
            slug = self.kwargs.get('slug')
            if slug:
                self._post = get_object_or_404(Post, slug=slug)
            else:
                self._post = None
        return self._post

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        post = self.get_post()
        lang_code = self.kwargs.get('lang_code')
        language = Language.objects.filter(code=lang_code).first() if lang_code else None
        laws_qs = Law.objects.filter(language=language)
        if post:
            laws_qs = laws_qs.filter(
                Q(group__post__designer=post.designer) |
                Q(group__post=None) |
                Q(group__post__official=True)
            ).distinct()        

        form.fields['reference_laws'].queryset = laws_qs.distinct()
        return form


    def test_func(self):
        post = self.get_post()
        if post:
            return self.request.user.profile.admin or post.designer == self.request.user.profile
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['post'] = self.get_post()
        context['lang_code'] = self.kwargs.get('lang_code')
        return context

    def form_valid(self, form):
        post = self.get_post()
        lang_code = self.kwargs.get('lang_code')

        if post:
            form.instance.post = post

        if lang_code:
            language = Language.objects.filter(code=lang_code).first()
            if language:
                form.instance.language = language

        return super().form_valid(form)

    def get_success_url(self):
        answer_preview = self.object.answer
        lang_code = self.kwargs.get('lang_code')
        if answer_preview and len(answer_preview) > 100:
            answer_preview = answer_preview[:100] + "..."

        fields = [
            {'name': 'Posted by:', 'value': self.request.user.profile.name},
            {'name': 'Question:', 'value': self.object.question},
            {'name': 'Answer:', 'value': answer_preview or ""}
        ]

        post = self.get_post()
        if post:
            send_rich_discord_message(f'FAQ Created for {post.title}', category='FAQ Law', title='New FAQ', fields=fields)
            return reverse('faq-view', kwargs={'slug': post.slug, 'lang_code': lang_code})
        return reverse('lang-faq', kwargs={'lang_code': lang_code})

@editor_required_class_based_view
class FAQUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = FAQ
    form_class = FAQForm
    template_name = 'the_keep/faq/faq_form.html'  # You can reuse the same form template

    def test_func(self):
        faq = self.get_object()
        post = faq.post
        user_profile = self.request.user.profile

        # Allow if user is admin or designer of the post (if post exists)
        if post and user_profile:
            return user_profile.admin or post.designer == user_profile
        # Or allow if user is staff (for FAQs without post)
        return self.request.user.is_staff
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        faq = self.get_object()
        context['post'] = faq.post
        context['lang_code'] = faq.language.code
        return context


    def get_success_url(self):
        faq = self.get_object()
        lang_code = faq.language.code
        if faq.post:
            return reverse('faq-view', kwargs={'slug': faq.post.slug, 'lang_code': lang_code})
        return reverse('lang-faq', kwargs={'lang_code': lang_code})

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        faq = self.get_object()
        post = faq.post
        language = faq.language

        laws_qs = Law.objects.filter(language=language)
        if post:
            laws_qs = laws_qs.filter(
                Q(group__post__designer=post.designer) |
                Q(group__post=None) |
                Q(group__post__official=True)
            ).distinct()        

        form.fields['reference_laws'].queryset = laws_qs.distinct()
        return form


@editor_required_class_based_view
class FAQDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = FAQ
    template_name = 'the_keep/faq/faq_confirm_delete.html'  # Create a simple confirmation template

    def test_func(self):
        faq = self.get_object()
        post = faq.post
        user_profile = self.request.user.profile

        if post:
            return user_profile.admin or post.designer == user_profile
        return self.request.user.is_staff

    def get_success_url(self):
        faq = self.get_object()
        if faq.post:
            return reverse('faq-view', kwargs={'slug': faq.post.slug, 'lang_code': faq.language.code})
        return reverse('lang-faq', kwargs={'lang_code': faq.language.code})



