from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from the_gatehouse.models import Profile
from the_keep.utils import validate_hex_color

class ForgedFaction(models.Model):
    class BackgroundPreset(models.TextChoices):
        NONE = '', '---'
        CATS = 'cats', 'Marquise de Cat'
        BIRDS = 'birds', 'Eyrie Dynasties'
        WA = 'wa', 'Woodland Alliance'
        VB = 'vb', 'Vagabond'
        LIZARDS = 'lizards', 'Lizard Cult'
        OTTERS = 'otters', 'Riverfolk Company'
        MOLES = 'moles', 'Underground Duchy'
        CROWS = 'crows', 'Corvid Conspiracy'
        RATS = 'rats', 'Lord of the Hundreds'
        BADGERS = 'badgers', 'Keepers in Iron'

    BACKGROUND_PRESET_FILES = {
        'cats': 'pdf/backgrounds/RootBG_Cats.png',
        'birds': 'pdf/backgrounds/RootBG_Birds.png',
        'wa': 'pdf/backgrounds/RootBG_WA.png',
        'vb': 'pdf/backgrounds/RootBG_VB.png',
        'lizards': 'pdf/backgrounds/RootBG_Lizards.png',
        'otters': 'pdf/backgrounds/RootBG_Otters.png',
        'moles': 'pdf/backgrounds/RootBG_Moles.png',
        'crows': 'pdf/backgrounds/RootBG_Crows.png',
        'rats': 'pdf/backgrounds/RootBG_Rats.png',
        'badgers': 'pdf/backgrounds/RootBG_Badgers.png',
        'frogs': 'pdf/backgrounds/RootBG_Frogs.png',
        'knaves': 'pdf/backgrounds/RootBG_Knaves.png',
        'bats': 'pdf/backgrounds/RootBG_Bats.png',
    }

    designer = models.ForeignKey(Profile, related_name='faction_sheets', on_delete=models.CASCADE)
    faction_name = models.CharField(max_length=100)
    color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    background_preset = models.CharField(
        max_length=20,
        choices=BackgroundPreset.choices,
        blank=True,
        default='',
        help_text="Select a preset background, or upload your own below."
    )
    background_image = models.ImageField(upload_to='forge/sheet_backgrounds/', blank=True, null=True)
    repeat_background_image = models.BooleanField(default=False)

    def get_background_path(self):
        if self.background_preset:
            import os
            static_dir = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static')
            return os.path.join(static_dir, self.BACKGROUND_PRESET_FILES[self.background_preset])
        return self.background_image.path


class FactionSheet(models.Model):
    class LayoutChoices(models.TextChoices):
        LAYOUT_HORIZONTAL = 'horizontal'
        LAYOUT_VERTICAL = 'vertical'
    faction = models.OneToOneField(ForgedFaction, related_name='faction_sheet', on_delete=models.CASCADE)
    flavor_text = models.TextField(blank=True, null=True)
    action_image = models.ImageField(upload_to='forge/action_icons/', blank=True, null=True)
    include_crafted_items = models.BooleanField(default=True)
    layout_mode = models.CharField(max_length=30, choices=LayoutChoices.choices, default=LayoutChoices.LAYOUT_VERTICAL)

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.faction.background_preset and not self.faction.background_image:
            raise ValidationError("Select a preset background or upload a custom image.")

    def get_background_path(self):
        return self.faction.get_background_path()




class FactionAbility(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='abilities', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=100)
    body = models.TextField()

class ContentBox(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='content_boxes', on_delete=models.CASCADE)
    title = models.CharField(max_length=200, blank=True, default='')
    text = models.TextField(blank=True, default='')
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ['order']

class PhaseStep(models.Model):
    class PhaseChoices(models.TextChoices):
        BIRDSONG = 'birdsong'
        DAYLIGHT = 'daylight'
        EVENING = 'evening'
        OTHER = 'other'
    sheet = models.ForeignKey(FactionSheet, related_name='phase_steps', on_delete=models.CASCADE)
    content_box = models.ForeignKey(ContentBox, related_name='steps', on_delete=models.CASCADE, blank=True, null=True)
    phase = models.CharField(max_length=30, choices=PhaseChoices.choices)
    number = models.PositiveIntegerField()
    text = models.TextField()

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.phase == self.PhaseChoices.OTHER and not self.content_box:
            raise ValidationError({'content_box': 'A ContentBox is required when phase is "Other".'})
        if self.phase != self.PhaseChoices.OTHER and self.content_box:
            raise ValidationError({'content_box': 'ContentBox should only be set when phase is "Other".'})

    class Meta:
        ordering = ['phase', 'number']

class StepAction(models.Model):
    class CostChoices(models.TextChoices):
        ACTION = 'action', 'Action'
        # Items
        SWORD = 'item_sword', 'Sword'
        HAMMER = 'item_hammer', 'Hammer'
        CROSSBOW = 'item_crossbow', 'Crossbow'
        COINS = 'item_coins', 'Coins'
        BOOTS = 'item_boots', 'Boots'
        TEA = 'item_tea', 'Tea'
        BAG = 'item_bag', 'Bag'
        TORCH = 'item_torch', 'Torch'
        ANY_ITEM = 'item_any', 'Any Item'
        # Cards
        FOX = 'card_fox', 'Fox'
        MOUSE = 'card_mouse', 'Mouse'
        RABBIT = 'card_rabbit', 'Rabbit'
        BIRD = 'card_bird', 'Bird'
        NON_BIRD = 'card_nonbird', 'Non-Bird'
        # Custom
        OTHER = 'other', 'Other (Custom Image)'

    step = models.ForeignKey(PhaseStep, related_name='actions', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    text = models.TextField()
    cost = models.CharField(max_length=20, choices=CostChoices.choices, default=CostChoices.ACTION)
    cost_image = models.ImageField(upload_to='forge/cost_icons/', blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.cost == self.CostChoices.OTHER and not self.cost_image:
            raise ValidationError({'cost_image': 'A custom image is required when cost is "Other".'})

class DecreeSection(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='decrees', on_delete=models.CASCADE)
    title = models.CharField(max_length=15, blank=True, null=True)
    body = models.CharField(max_length=20, blank=True, null=True)


class CardSlot(models.Model):
    decree = models.ForeignKey(DecreeSection, related_name='card_slots', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, null=True)
    body = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['number']


class CardPile(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='card_piles', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, null=True)
    body = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['number']

class BorderedBox(models.Model):
    class BoxSize(models.TextChoices):
        SMALL = 'small'
        MEDIUM = 'medium'
        LARGE = 'large'
    step = models.ForeignKey(PhaseStep, related_name='boxes', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, null=True)
    height = models.CharField(max_length=15, choices=BoxSize.choices)

class CardboardTrack(models.Model):
    class TrackChoices(models.TextChoices):
        TOKEN = 'token'
        BUILDING = 'building'
    step = models.ForeignKey(PhaseStep, related_name='tracks', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, choices=TrackChoices.choices)
    num_columns = models.PositiveIntegerField(default=1)
    column_headers = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Pipe-delimited column labels, e.g. "0|1|2|3|4"'
    )
    column_cost_type = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Inline image keyword for column header icons, e.g. "VP"'
    )
    column_dividers = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Comma-separated column indices for dividers, e.g. "2,5,7"'
    )
    class HeaderPosition(models.TextChoices):
        ABOVE = 'above'
        BELOW = 'below'
    header_position = models.CharField(
        max_length=10, choices=HeaderPosition.choices, default=HeaderPosition.ABOVE,
        help_text='Display column headers above or below the slots'
    )
    header_title = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Title displayed in the row-title area of the header row'
    )
    class RowTitleOrientation(models.TextChoices):
        HORIZONTAL = 'horizontal'
        VERTICAL = 'vertical'
    row_title_orientation = models.CharField(
        max_length=12, choices=RowTitleOrientation.choices, default=RowTitleOrientation.HORIZONTAL,
        help_text='Display row titles horizontally (default) or vertically (rotated 90°)'
    )
    background_image = models.ImageField(upload_to='forge/track_backgrounds/', blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.column_headers:
            headers = self.column_headers.split('|')
            if len(headers) != self.num_columns:
                raise ValidationError({
                    'column_headers': f'Expected {self.num_columns} headers, got {len(headers)}.'
                })
        if self.column_dividers:
            for col_str in self.column_dividers.split(','):
                col_str = col_str.strip()
                try:
                    col = int(col_str)
                except ValueError:
                    raise ValidationError({
                        'column_dividers': f'"{col_str}" is not a number.'
                    })
                if col >= self.num_columns:
                    raise ValidationError({
                        'column_dividers': f'Column index {col} exceeds num_columns ({self.num_columns}).'
                    })

    class Meta:
        ordering = ['order']

class CardboardSlot(models.Model):
    track = models.ForeignKey(CardboardTrack, related_name='slots', on_delete=models.CASCADE)
    number = models.PositiveIntegerField(help_text='For admin ordering')
    row = models.PositiveIntegerField(default=0)
    column = models.PositiveIntegerField(default=0)
    row_title = models.CharField(max_length=200, blank=True, default='')
    content = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Pipe-delimited image keywords, e.g. "1VP" or "fox|1VP"'
    )
    background_image = models.ImageField(upload_to='forge/slot_backgrounds/', blank=True, null=True)

    class Meta:
        ordering = ['row', 'column']
        unique_together = [('track', 'row', 'column')]


class FactionBack(models.Model):
    class AttributeChoices(models.TextChoices):
        NONE = 'N', 'None'
        LOW = 'L', 'Low'
        MODERATE = 'M', 'Moderate'
        HIGH = 'H', 'High'
    faction = models.OneToOneField(ForgedFaction, related_name='faction_back', on_delete=models.CASCADE)
    complexity = models.CharField(max_length=1, choices=AttributeChoices.choices, default=AttributeChoices.NONE)
    card_wealth = models.CharField(max_length=1, choices=AttributeChoices.choices, default=AttributeChoices.NONE)
    aggression = models.CharField(max_length=1, choices=AttributeChoices.choices, default=AttributeChoices.NONE)
    crafting_ability = models.CharField(max_length=1, choices=AttributeChoices.choices, default=AttributeChoices.NONE)

    setup_order = models.CharField(max_length=1, default="X")

    how_to_play_title = models.TextField(default='Playing the Faction')
    how_to_play_text = models.TextField(blank=True, null=True)
    back_image = models.ImageField(upload_to='forge/faction_back/', blank=True, null=True)

class Piece(models.Model):
    class TypeChoices(models.TextChoices):
        WARRIOR = 'W'
        BUILDING = 'B'
        TOKEN = 'T'
        CARD = 'C'
        OTHER = 'O'

    parent = models.ForeignKey(FactionBack, on_delete=models.CASCADE, related_name='pieces')    
    name = models.CharField(max_length=30)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(99)])
    type = models.CharField(max_length=1, choices=TypeChoices.choices)
    small_icon = models.ImageField(upload_to='forge/sheet_backgrounds/', null=True, blank=True)


class SetupCard(models.Model):
    class TypeChoices(models.TextChoices):
        MILITANT = 'M', 'Militant'
        INSURGENT = 'I', 'Insurgent'
    faction = models.OneToOneField(ForgedFaction, related_name='setup_card', on_delete=models.CASCADE)
    type = models.CharField(max_length=10, choices=TypeChoices.choices, default=TypeChoices.INSURGENT)
    reach = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], default=4)

class SetupStep(models.Model):
    faction_back = models.ForeignKey(FactionBack, related_name='setup_steps', on_delete=models.CASCADE, blank=True, null=True)
    card = models.ForeignKey(SetupCard, related_name='setup_steps', on_delete=models.CASCADE, blank=True, null=True)
    number = models.PositiveIntegerField()
    text = models.TextField()