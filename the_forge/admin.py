from django.contrib import admin

from .models import (
    ForgedFaction,
    FactionSheet,
    FactionAbility,
    ContentBox,
    PhaseStep,
    StepAction,
    DecreeSection,
    CardSlot,
    CardPile,
    BorderedBox,
    CardboardTrack,
    CardboardSlot,
    FactionBack,
    Legend,
    LegendRow,
    Piece,
    Scale,
    ScaleRow,
    SetupCard,
    SetupStep,
)


# ---------- Inlines ----------

class CardboardSlotInline(admin.TabularInline):
    model = CardboardSlot
    extra = 0
    fields = ('number', 'row', 'column', 'content', 'background_image')
    ordering = ('row', 'column')


class StepActionInline(admin.TabularInline):
    model = StepAction
    extra = 0
    fields = ('order', 'cost', 'text')


class BorderedBoxInline(admin.TabularInline):
    model = BorderedBox
    extra = 0
    fields = ('order', 'title', 'height', 'body')


class CardboardTrackInline(admin.TabularInline):
    model = CardboardTrack
    extra = 0
    fields = ('order', 'title', 'type', 'num_rows', 'num_columns')
    show_change_link = True


class LegendInline(admin.TabularInline):
    model = Legend
    extra = 0
    fields = ('order', 'title')
    show_change_link = True


class LegendRowInline(admin.TabularInline):
    model = LegendRow
    extra = 0
    fields = ('order', 'title', 'image', 'body')


class ScaleInline(admin.TabularInline):
    model = Scale
    extra = 0
    fields = ('order', 'title')
    show_change_link = True


class ScaleRowInline(admin.TabularInline):
    model = ScaleRow
    extra = 0
    fields = ('order', 'range', 'result')


class FactionAbilityInline(admin.TabularInline):
    model = FactionAbility
    extra = 0
    fields = ('order', 'title', 'body')


class ContentBoxInline(admin.TabularInline):
    model = ContentBox
    extra = 0
    fields = ('order', 'title', 'text')
    show_change_link = True


class PhaseStepInline(admin.TabularInline):
    model = PhaseStep
    extra = 0
    fields = ('phase', 'number', 'text', 'content_box')
    show_change_link = True


class CardSlotInline(admin.TabularInline):
    model = CardSlot
    extra = 0
    fields = ('number', 'title', 'body')


class CardPileInline(admin.TabularInline):
    model = CardPile
    extra = 0
    fields = ('number', 'title', 'body')


class PieceInline(admin.TabularInline):
    model = Piece
    fk_name = 'parent'
    extra = 0
    fields = ('name', 'quantity', 'type', 'small_icon')


class SetupStepBackInline(admin.TabularInline):
    model = SetupStep
    fk_name = 'faction_back'
    extra = 0
    fields = ('number', 'text')


class SetupStepCardInline(admin.TabularInline):
    model = SetupStep
    fk_name = 'card'
    extra = 0
    fields = ('number', 'text')


# ---------- Top-level registrations ----------

@admin.register(ForgedFaction)
class ForgedFactionAdmin(admin.ModelAdmin):
    list_display = ('faction_name', 'designer', 'color', 'background_preset')
    list_filter = ('background_preset',)
    search_fields = ('faction_name',)


@admin.register(FactionSheet)
class FactionSheetAdmin(admin.ModelAdmin):
    list_display = ('faction', 'layout_mode', 'include_crafted_items')
    list_filter = ('layout_mode',)
    inlines = [FactionAbilityInline, ContentBoxInline, PhaseStepInline, CardPileInline]


@admin.register(PhaseStep)
class PhaseStepAdmin(admin.ModelAdmin):
    list_display = ('sheet', 'phase', 'number', 'content_box', 'short_text')
    list_filter = ('phase',)
    inlines = [StepActionInline, BorderedBoxInline, CardboardTrackInline, LegendInline, ScaleInline]

    @admin.display(description='Text')
    def short_text(self, obj):
        return (obj.text or '')[:80]


@admin.register(Legend)
class LegendAdmin(admin.ModelAdmin):
    list_display = ('step', 'order', 'title')
    inlines = [LegendRowInline]


@admin.register(Scale)
class ScaleAdmin(admin.ModelAdmin):
    list_display = ('step', 'order', 'title')
    inlines = [ScaleRowInline]


@admin.register(ContentBox)
class ContentBoxAdmin(admin.ModelAdmin):
    list_display = ('sheet', 'order', 'title')
    list_filter = ('sheet',)


@admin.register(DecreeSection)
class DecreeSectionAdmin(admin.ModelAdmin):
    list_display = ('sheet', 'title')
    inlines = [CardSlotInline]


@admin.register(CardboardTrack)
class CardboardTrackAdmin(admin.ModelAdmin):
    list_display = ('title', 'step', 'type', 'num_rows', 'num_columns', 'order')
    list_filter = ('type',)
    inlines = [CardboardSlotInline]


@admin.register(CardboardSlot)
class CardboardSlotAdmin(admin.ModelAdmin):
    list_display = ('track', 'row', 'column', 'content', 'background_image')
    list_filter = ('track__type',)
    search_fields = ('content',)


@admin.register(FactionBack)
class FactionBackAdmin(admin.ModelAdmin):
    list_display = ('faction',)
    inlines = [PieceInline, SetupStepBackInline]


@admin.register(SetupCard)
class SetupCardAdmin(admin.ModelAdmin):
    list_display = ('faction', 'type', 'reach')
    inlines = [SetupStepCardInline]
