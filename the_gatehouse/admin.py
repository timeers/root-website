from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.urls import path, reverse
from django.shortcuts import render
from .models import (Profile, PlayerBookmark,
                     Theme, BackgroundImage, ForegroundImage,
                     Website, Language, Holiday, DailyUserVisit, DiscordGuild,
                     Changelog, ChangelogEntry, DiscordGuildJoinRequest, UserNotification,
                     GuildLFGRole,
                     )
from django import forms
from django.http import HttpResponseRedirect 
from django.db import transaction
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _


class BackgroundInline(admin.StackedInline):
    model = BackgroundImage
    extra = 0
class ForegroundInline(admin.StackedInline):
    model = ForegroundImage
    extra = 0
class ChangelogEntryInline(admin.StackedInline):
    model = ChangelogEntry
    extra = 0

class DiscordGuildJoinRequestAdmin(admin.ModelAdmin):
    list_display = ['profile__discord', 'guild__name', 'status']
    search_fields = ['profile__discord', 'guild__name', 'status']

class GuildLFGRoleInline(admin.TabularInline):
    model = GuildLFGRole
    extra = 0

class GuildLFGRoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'guild', 'role_id']
    search_fields = ['name', 'guild__name']

class DiscordGuildAdmin(admin.ModelAdmin):
    list_display = ['name', 'guild_id']
    search_fields = ['name']
    inlines = [GuildLFGRoleInline]
    filter_horizontal = ['guild_moderators']

class WebsiteAdmin(admin.ModelAdmin):
    list_display = ['site_title', 'default_theme', 'player_threshold', 'game_threshold']

class LanguangeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code']

class HolidayAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_date', 'end_date']

class ThemeAdmin(admin.ModelAdmin):
    list_display = ['name', 'holiday', 'public', 'active', 'backup_theme']
    inlines = [BackgroundInline, ForegroundInline]

class ChangelogAdmin(admin.ModelAdmin):
    list_display = ['version', 'title', 'date']
    inlines = [ChangelogEntryInline]

class BackgroundImageAdmin(admin.ModelAdmin):
    list_display = ['name', 'page', 'theme__name', 'image']
    search_fields = ('name', 'page', 'theme__name')

class ForegroundImageAdmin(admin.ModelAdmin):
    list_display = ['name', 'page', 'theme__name', 'location', 'image']
    search_fields = ('name', 'page', 'theme__name')

class DailyUserVisitAdmin(admin.ModelAdmin):
    list_display = ['date', 'profile__discord']
    search_fields = ('date', 'profile__discord')


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

    # ------------------------------------------------------------------
    # Dynamic merge internals
    #
    # Instead of hardcoding each relationship, we walk Profile._meta at
    # runtime so that every inbound ForeignKey / ManyToMany (including ones
    # added in the future) is reassigned from the merged-in profile to the
    # survivor automatically. See the plan/docstrings below for the handling
    # of unique constraints, self-references and CASCADE children.
    # ------------------------------------------------------------------

    def _inbound_relations(self):
        """Classify every relation pointing at Profile into reverse FKs and
        reverse (auto-through) M2Ms, discovered from the model meta API.

        Forward fields on Profile (user, theme, language, guilds, bookmarks,
        admin_nominated, admin_dismiss) have auto_created=False and are skipped
        - they live on the survivor/loser row itself and don't need moving.

        Explicit-through M2Ms (PlayerBookmark/PostBookmark/GameBookmark) are
        skipped here because Django forbids .add()/.remove() on them; they are
        instead reassigned through their reverse FK (which is also discovered).
        """
        reverse_fks = []
        reverse_m2ms = []
        for f in Profile._meta.get_fields():
            if not f.is_relation or not f.auto_created or f.concrete:
                continue
            if f.one_to_many:
                reverse_fks.append(f)
            elif f.many_to_many and f.field.remote_field.through._meta.auto_created:
                reverse_m2ms.append(f)
        return reverse_fks, reverse_m2ms

    def _profile_unique_groups(self, model, fk_field_name):
        """Return, for every uniqueness rule on `model` that includes the
        profile FK, the tuple of OTHER fields in that rule.

        e.g. TournamentPlayer unique_together=('tournament', 'profile') with
        fk_field_name='profile' yields [('tournament',)]. These are the fields
        that, together with the survivor, decide whether a loser's row would
        collide with an existing survivor row.
        """
        groups = []
        for ut in (model._meta.unique_together or ()):
            if fk_field_name in ut:
                groups.append(tuple(c for c in ut if c != fk_field_name))
        for con in model._meta.constraints:
            if isinstance(con, UniqueConstraint) and fk_field_name in con.fields:
                groups.append(tuple(c for c in con.fields if c != fk_field_name))
        return groups

    def _other_profile_fk_names(self, model, fk_field_name):
        """Names of OTHER FK fields on `model` that also point at Profile.

        Used to detect rows that would become self-references after the merge
        (e.g. PlayerBookmark.player reassigned to survivor while friend is
        already the survivor, or Profile.admin_nominated self-loops).
        """
        names = []
        for field in model._meta.get_fields():
            # Only single-valued concrete relations have an `_id` attname to
            # compare; skip M2M (e.g. Profile.bookmarks) which would raise.
            if (getattr(field, 'concrete', False) and field.is_relation
                    and not field.many_to_many
                    and field.related_model is Profile
                    and field.name != fk_field_name):
                names.append(field.name)
        return names

    def _merge_reverse_fk(self, request, rel, loser, survivor):
        """Reassign all rows of one reverse FK from loser -> survivor.

        Fast path (no profile-FK uniqueness and no other Profile FK on the
        model): a single bulk UPDATE. This also covers Effort.player (PROTECT)
        - PROTECT only blocks deletes, and we reassign before deleting the loser.

        Careful path (uniqueness rule involving the FK, or the model has another
        FK pointing at Profile - e.g. PlayerBookmark.player/friend, or Profile's
        own admin_nominated/admin_dismiss self-FKs): walk row by row and discard
        the loser's row when moving it would create a self-reference or duplicate
        an existing survivor row.
        """
        model = rel.related_model
        field_name = rel.field.name
        qs = model.objects.filter(**{field_name: loser})

        unique_groups = self._profile_unique_groups(model, field_name)
        other_fk_names = self._other_profile_fk_names(model, field_name)

        if not unique_groups and not other_fk_names:
            moved = qs.update(**{field_name: survivor})
            return moved, 0

        moved = 0
        discarded = 0
        for obj in qs:
            # Would this row become a self-reference once reassigned? (covers
            # PlayerBookmark player==friend and admin_nominated==self loops)
            if any(getattr(obj, name + '_id') == survivor.pk for name in other_fk_names):
                self._report_discard(request, obj, reason="self-reference")
                obj.delete()
                discarded += 1
                continue

            # Would this row collide with an existing survivor row? Check both
            # declared unique groups (e.g. TournamentPlayer (tournament,)) and,
            # for models with other Profile FKs, the full set of Profile FKs so
            # duplicate relationships (e.g. a repeated follow) are deduped even
            # without a DB constraint. Use attname so FK/plain fields look up alike.
            collides = False
            collision_groups = list(unique_groups)
            if other_fk_names:
                collision_groups.append(tuple(other_fk_names))
            for group in collision_groups:
                lookup = {field_name: survivor}
                for other in group:
                    attname = model._meta.get_field(other).attname
                    lookup[attname] = getattr(obj, attname)
                if model.objects.filter(**lookup).exclude(pk=obj.pk).exists():
                    collides = True
                    break

            if collides:
                self._handle_collision(request, model, obj, survivor, field_name)
                obj.delete()
                discarded += 1
                continue

            setattr(obj, field_name, survivor)
            obj.save(update_fields=[field_name])
            moved += 1
        return moved, discarded

    def _handle_collision(self, request, model, obj, survivor, field_name):
        """Hook for collision rows that carry children worth preserving.

        TournamentPlayer is the notable case: deleting it would CASCADE-delete
        its StageParticipant rows (and their MatchSeat rows). Re-point those
        children onto the survivor's TournamentPlayer for the same tournament
        before the loser's row is discarded.
        """
        if model.__name__ == 'TournamentPlayer':
            survivor_tp = model.objects.filter(
                tournament=obj.tournament, **{field_name: survivor}
            ).first()
            if survivor_tp is not None:
                reparented, deduped = self._reparent_stage_participations(
                    obj, survivor_tp
                )
                if reparented or deduped:
                    detail = f"Re-pointed {reparented} stage participation(s)"
                    if deduped:
                        detail += (
                            f" and merged {deduped} duplicate(s) into the "
                            f"survivor's existing stage entries"
                        )
                    messages.warning(
                        request,
                        f"{detail} from the discarded {model.__name__} "
                        f"(pk={obj.pk}) to the survivor's entry in tournament "
                        f"{obj.tournament}."
                    )
        self._report_discard(request, obj, reason="duplicate")

    def _reparent_stage_participations(self, loser_tp, survivor_tp):
        """Move a losing TournamentPlayer's StageParticipant rows onto the
        survivor's TournamentPlayer, treating (stage, tournament_player) as
        unique (there is no DB constraint, but the app assumes it everywhere).

        For each losing StageParticipant:
        - If the survivor's TournamentPlayer has no participant in that stage,
          simply re-point it (fast, preserves its MatchSeat children).
        - If the survivor already participates in that stage, re-point the
          loser's MatchSeat rows onto the survivor's StageParticipant, then
          delete the now-duplicate loser row. This is what prevents the same
          player appearing twice in a single stage after a merge.
        """
        from the_warroom.models import StageParticipant, MatchSeat

        reparented = 0
        deduped = 0
        for sp in StageParticipant.objects.filter(tournament_player=loser_tp):
            survivor_sp = StageParticipant.objects.filter(
                stage_id=sp.stage_id, tournament_player=survivor_tp
            ).first()
            if survivor_sp is None:
                sp.tournament_player = survivor_tp
                sp.save(update_fields=['tournament_player'])
                reparented += 1
                continue

            # Survivor is already in this stage: fold the loser's seats in,
            # skipping any that would duplicate a seat the survivor already
            # holds in the same series, then drop the duplicate participant.
            for seat in MatchSeat.objects.filter(stage_participant=sp):
                if MatchSeat.objects.filter(
                    series_id=seat.series_id, stage_participant=survivor_sp
                ).exists():
                    seat.delete()
                else:
                    seat.stage_participant = survivor_sp
                    seat.save(update_fields=['stage_participant'])
            # Transfer any "series winner" records off the loser before it is
            # deleted, otherwise the through-row CASCADEs away and the win is lost.
            for series in sp.won_series.all():
                series.winners.remove(sp)
                series.winners.add(survivor_sp)
            sp.delete()
            deduped += 1
        return reparented, deduped

    def _report_discard(self, request, obj, reason):
        messages.warning(
            request,
            f"Discarded {obj._meta.label} (pk={obj.pk}) during merge "
            f"[{reason}]: {obj}."
        )

    def _merge_reverse_m2m(self, rel, loser, survivor):
        """Move membership in an auto-through M2M from loser -> survivor."""
        field_name = rel.field.name
        moved = 0
        for obj in rel.related_model.objects.filter(**{field_name: loser}):
            manager = getattr(obj, field_name)
            manager.add(survivor)
            manager.remove(loser)
            moved += 1
        return moved

    # This should makes it so that if the full merge is not successful it will undo the changes.
    @transaction.atomic
    def merge_user_with_players(self, request, user, players):
        # `user` is the surviving Profile (named for historical reasons).
        survivor = user
        no_dwd_username = survivor.dwd is None

        reverse_fks, reverse_m2ms = self._inbound_relations()

        for player in players:
            summary = {}

            # Reassign every inbound FK before deleting, so CASCADE children
            # (comments, bookmarks, notifications, ...) are moved, not destroyed.
            for rel in reverse_fks:
                moved, discarded = self._merge_reverse_fk(request, rel, player, survivor)
                if moved or discarded:
                    summary[rel.get_accessor_name()] = moved

            for rel in reverse_m2ms:
                moved = self._merge_reverse_m2m(rel, player, survivor)
                if moved:
                    summary[rel.get_accessor_name()] = moved

            # Preserve the DWD username on the survivor if it has none. Null it
            # on the loser first to free the unique column before reassigning.
            if no_dwd_username and player.dwd:
                dwd_name = player.dwd
                player.dwd = None
                player.save(update_fields=['dwd'])
                survivor.dwd = dwd_name
                survivor.save(update_fields=['dwd'])
                no_dwd_username = False
                self.message_user(request, f"{player}'s DWD Username ({dwd_name}) added to {survivor}.")

            # Delete the merged-in profile.
            player.delete()

            if summary:
                detail = ", ".join(f"{name}: {count}" for name, count in summary.items() if count)
                self.message_user(request, f"Merged {player} into {survivor} ({detail}).")
            else:
                self.message_user(request, f"Merged {player} into {survivor}.")


    def get_urls(self):
        urls = super().get_urls()
        new_urls = [path('upload-profile-csv/', self.upload_csv)]
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

class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ('profile', 'message_type', 'message_preview', 'created_at', 'is_dismissed')
    list_filter = ('message_type', 'is_dismissed', 'created_at')
    search_fields = ('profile__display_name', 'profile__discord', 'message')
    readonly_fields = ('created_at', 'dismissed_at')

    def message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_preview.short_description = 'Message'

admin.site.register(PlayerBookmark, PlayerBookmarkAdmin)
admin.site.register(UserNotification, UserNotificationAdmin)


# Unregister the default User admin
admin.site.unregister(User)
# Register the customized User admin
admin.site.register(User, CustomUserAdmin)





admin.site.register(Profile, ProfileAdmin)
admin.site.register(Theme, ThemeAdmin)
admin.site.register(BackgroundImage, BackgroundImageAdmin)
admin.site.register(ForegroundImage, ForegroundImageAdmin)
admin.site.register(Website, WebsiteAdmin)
admin.site.register(Language, LanguangeAdmin)
admin.site.register(Holiday, HolidayAdmin)
admin.site.register(DailyUserVisit, DailyUserVisitAdmin)
admin.site.register(DiscordGuild, DiscordGuildAdmin)
admin.site.register(Changelog, ChangelogAdmin)
admin.site.register(DiscordGuildJoinRequest, DiscordGuildJoinRequestAdmin)
admin.site.register(GuildLFGRole, GuildLFGRoleAdmin)


