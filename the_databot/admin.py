from datetime import timedelta

from django.contrib import admin

from .models import (GuildLFGRole, BotUsage, BotBlacklist, LFGThread,
                     ScheduleProposal, LFGRoll, LFGDraft, LFGDraftPick, LFGSeat,
                     BoxScoreUploadToken)


class GuildLFGRoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'guild', 'tournament', 'role_id', 'forum_channel_id', 'forum_tag_id']
    list_select_related = ['guild', 'tournament']
    search_fields = ['name', 'guild__name']

class BotBlacklistAdmin(admin.ModelAdmin):
    list_display = ['kind', 'discord_id', 'active', 'reason', 'created_at']
    list_filter = ['kind', 'active']
    search_fields = ['discord_id', 'reason']
    actions = ['activate', 'deactivate']

    @admin.action(description="Activate (block) selected entries")
    def activate(self, request, queryset):
        queryset.update(active=True)

    @admin.action(description="Deactivate (unblock) selected entries")
    def deactivate(self, request, queryset):
        queryset.update(active=False)

class BotUsageAdmin(admin.ModelAdmin):
    list_display = ['command', 'user_id', 'guild_id', 'count', 'last_used']
    list_filter = ['command']
    search_fields = ['user_id', 'guild_id', 'command']
    ordering = ['-count']

class LFGSeatInline(admin.TabularInline):
    """The thread's current seating. Read-only: written by /draft's seat button
    and by /pick, which also fills vagabond and captains."""
    model = LFGSeat
    extra = 0
    can_delete = False
    # captains via a display method, not the field: filter_horizontal doesn't
    # apply to a readonly M2M, and these rows are bot-written.
    readonly_fields = ['seat_number', 'profile', 'faction', 'vagabond',
                       'captain_list']

    @admin.display(description="Captains")
    def captain_list(self, obj):
        return ", ".join(c.title for c in obj.captains.all()) or "—"

class LFGRollInline(admin.TabularInline):
    """Append-only capture log. Read-only: written by record_lfg_components_task."""
    model = LFGRoll
    extra = 0
    can_delete = False
    readonly_fields = ['kind', 'post', 'slug', 'source', 'created_at']

class LFGDraftInline(admin.StackedInline):
    """StackedInline + max_num=1 because LFGDraft is a OneToOne. Its picks are one
    FK further out, so they live on LFGDraftAdmin below rather than here."""
    model = LFGDraft
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ['players', 'platform', 'drafted_by', 'created_at']

class LFGDraftPickInline(admin.TabularInline):
    model = LFGDraftPick
    extra = 0
    can_delete = False
    readonly_fields = ['order', 'faction', 'vagabond']
    filter_horizontal = ['captains']

class LFGDraftAdmin(admin.ModelAdmin):
    list_display = ['thread', 'players', 'platform', 'drafted_by', 'created_at']
    search_fields = ['thread__thread_id']
    readonly_fields = ['thread', 'created_at']
    inlines = [LFGDraftPickInline]

class LFGThreadAdmin(admin.ModelAdmin):
    list_display = ['thread_id', 'guild', 'lfg_role', 'map', 'deck', 'status',
                    'created_at', 'last_activity']
    list_filter = ['status']
    search_fields = ['thread_id', 'description', 'guild__name']
    readonly_fields = ['thread_id', 'created_at', 'last_activity']
    filter_horizontal = ['players']
    inlines = [LFGSeatInline, LFGDraftInline, LFGRollInline]
    actions = ['issue_test_upload_token']

    @admin.action(description="Issue a reusable box score test token (30 days)")
    def issue_test_upload_token(self, request, queryset):
        # issued_by=None deliberately: this token belongs to no Discord user,
        # so _boxscore_click_owner's "issuer_pk is None" branch lets ANY
        # roster player (or host/staff) answer a prompt it stages, rather
        # than restricting Confirm/Cancel to one specific profile.
        if queryset.count() != 1:
            self.message_user(
                request, "Select exactly one thread to issue a test token for.",
                level="error")
            return
        thread = queryset.first()
        _token, raw = BoxScoreUploadToken.issue(
            thread, profile=None, test_mode=True, ttl=timedelta(days=30))
        # The raw value is never recoverable after this -- only its hash is
        # stored, same as the /boxscore token Discord flow.
        self.message_user(
            request,
            f"Test upload token for {thread}: {BoxScoreUploadToken.group(raw)} "
            "-- copy it now, it can't be shown again.")

class BoxScoreUploadTokenAdmin(admin.ModelAdmin):
    """Read-only: the raw token is never stored, only its hash, and every
    other field is written by the upload flow itself."""
    list_display = ['thread', 'status', 'test_mode', 'created_at', 'expires_at',
                    'issued_by']
    list_filter = ['status', 'test_mode']
    search_fields = ['thread__thread_id']
    readonly_fields = ['token_hash', 'thread', 'issued_by', 'status', 'test_mode',
                       'created_at', 'expires_at', 'used_at', 'payload',
                       'channel_id', 'message_id', 'prompt_expires_at',
                       'reminded_at']

    def has_add_permission(self, request):
        return False

class ScheduleProposalAdmin(admin.ModelAdmin):
    list_display = ['id', 'match', 'proposed_time', 'status', 'proposed_by', 'created_at']
    list_filter = ['status']
    search_fields = ['match__name', 'proposed_by__discord', 'channel_id', 'message_id']
    readonly_fields = ['created_at', 'resolved_at', 'channel_id', 'message_id', 'guild_id']
    filter_horizontal = ['roster', 'confirmed_by']


admin.site.register(GuildLFGRole, GuildLFGRoleAdmin)
admin.site.register(BotBlacklist, BotBlacklistAdmin)
admin.site.register(BotUsage, BotUsageAdmin)
admin.site.register(LFGThread, LFGThreadAdmin)
admin.site.register(LFGDraft, LFGDraftAdmin)
admin.site.register(ScheduleProposal, ScheduleProposalAdmin)
admin.site.register(BoxScoreUploadToken, BoxScoreUploadTokenAdmin)
