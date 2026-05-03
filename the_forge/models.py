from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

from the_gatehouse.models import Profile
from the_keep.utils import validate_hex_color, delete_old_image

from .services.upload_paths import (
    sheet_preview_upload_path,
    back_preview_upload_path,
    card_preview_upload_path,
    faction_upload_path,
)

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
        FROGS = 'frogs', 'Lilipad Diaspora'
        KNAVES = 'knaves', 'Knaves of the Deepwood'
        BATS = 'bats', 'Twilight Council'

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
    slug = models.SlugField(unique=True, null=True, blank=True)
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
    background_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)
    repeat_background_image = models.BooleanField(default=False)

    last_updated = models.DateTimeField(auto_now=True)
    last_generated = models.DateTimeField(blank=True, null=True)

    def get_background_path(self):
        if self.background_preset:
            import os
            static_dir = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static')
            return os.path.join(static_dir, self.BACKGROUND_PRESET_FILES[self.background_preset])
        return self.background_image.path

    def save(self, *args, **kwargs):
        new = self.pk is None
        if self.pk:
            try:
                old = ForgedFaction.objects.get(pk=self.pk)
                if old.background_image and old.background_image != self.background_image:
                    delete_old_image(old.background_image)
            except ForgedFaction.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if new:
            from the_gatehouse.tasks import send_rich_discord_message_task
            from django.urls import reverse
            url = reverse('forge-faction-detail', kwargs={'pk': self.pk})
            fields = [{'name': 'By:', 'value': str(self.designer)}]
            send_rich_discord_message_task.delay(
                f'[{self.faction_name}](https://therootdatabase.com{url})',
                category='Forge', title='New Faction', fields=fields,
            )


class FactionSheet(models.Model):
    class LayoutChoices(models.TextChoices):
        LAYOUT_HORIZONTAL = 'horizontal'
        LAYOUT_VERTICAL = 'vertical'

    class TitleTextColor(models.TextChoices):
        AUTO = 'auto', 'Auto'
        BLACK = 'black', 'Black'
        WHITE = 'white', 'White'

    faction = models.OneToOneField(ForgedFaction, related_name='faction_sheet', on_delete=models.CASCADE)
    flavor_text = models.TextField(blank=True, null=True)
    action_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)
    include_crafted_items = models.BooleanField(default=True)
    include_decree = models.BooleanField(default=False)
    layout_mode = models.CharField(max_length=30, choices=LayoutChoices.choices, default=LayoutChoices.LAYOUT_VERTICAL)
    header_image = models.ImageField(upload_to=faction_upload_path, null=True, blank=True)
    title_text_color = models.CharField(
        max_length=10,
        choices=TitleTextColor.choices,
        default=TitleTextColor.AUTO,
    )


    # Layout overrides (inches). Null = auto. `_h` = horizontal layout, `_v` = vertical.
    # `phase_box_w_h` is stored for forward compat but not yet consumed by the engine.
    phase_box_x_h = models.FloatField(blank=True, null=True)
    phase_box_y_h = models.FloatField(blank=True, null=True)
    phase_box_w_h = models.FloatField(blank=True, null=True)
    phase_box_h_h = models.FloatField(blank=True, null=True)
    phase_box_x_v = models.FloatField(blank=True, null=True)
    phase_box_y_v = models.FloatField(blank=True, null=True)
    phase_box_w_v = models.FloatField(blank=True, null=True)
    phase_box_h_v = models.FloatField(blank=True, null=True)
    decree_y_h = models.FloatField(blank=True, null=True)
    decree_y_v = models.FloatField(blank=True, null=True)

    image_preview = models.ImageField(upload_to=sheet_preview_upload_path, blank=True, null=True)
    preview_fingerprint = models.CharField(max_length=32, blank=True, default='')

    last_updated = models.DateTimeField(auto_now=True)
    last_generated = models.DateTimeField(blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.faction.background_preset and not self.faction.background_image:
            raise ValidationError("Select a preset background or upload a custom image.")

    def get_background_path(self):
        return self.faction.get_background_path()

    def save(self, *args, **kwargs):
        new = self.pk is None
        if self.pk:
            try:
                old = FactionSheet.objects.get(pk=self.pk)
                for field_name in ('action_image', 'header_image', 'image_preview'):
                    old_img = getattr(old, field_name)
                    new_img = getattr(self, field_name)
                    if old_img and old_img != new_img:
                        delete_old_image(old_img)
            except FactionSheet.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if new:
            from the_gatehouse.tasks import send_rich_discord_message_task
            from django.urls import reverse
            url = reverse('forge-faction-detail', kwargs={'pk': self.faction.pk})
            fields = [{'name': 'By:', 'value': str(self.faction.designer)}]
            send_rich_discord_message_task.delay(
                f'[{self.faction.faction_name}](https://therootdatabase.com{url})',
                category='Forge', title='New Faction Board', fields=fields,
            )


class CharacterImage(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='character_images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to=faction_upload_path)
    order = models.PositiveIntegerField(default=0)
    in_front = models.BooleanField(default=False)

    x_h = models.FloatField(blank=True, null=True)
    y_h = models.FloatField(blank=True, null=True)
    width_h = models.FloatField(blank=True, null=True)
    x_v = models.FloatField(blank=True, null=True)
    y_v = models.FloatField(blank=True, null=True)
    width_v = models.FloatField(blank=True, null=True)

    class Meta:
        ordering = ['order']

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = CharacterImage.objects.get(pk=self.pk)
                if old.image and old.image != self.image:
                    delete_old_image(old.image)
            except CharacterImage.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class CustomInlineImage(models.Model):
    SLOT_MIN = 0
    SLOT_MAX = 9

    sheet = models.ForeignKey(FactionSheet, related_name='custom_inline_images', on_delete=models.CASCADE)
    slot = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(SLOT_MIN), MaxValueValidator(SLOT_MAX)],
    )
    name = models.CharField(max_length=40)
    image = models.ImageField(upload_to=faction_upload_path)

    class Meta:
        ordering = ['slot']
        constraints = [
            models.UniqueConstraint(fields=['sheet', 'slot'], name='unique_custom_inline_image_slot'),
        ]

    @property
    def keyword(self):
        return f'custom_image_{self.slot}'

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = CustomInlineImage.objects.get(pk=self.pk)
                if old.image and old.image != self.image:
                    delete_old_image(old.image)
            except CustomInlineImage.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        self._resize()

    def _resize(self):
        import os
        from PIL import Image
        if not (self.image and os.path.exists(self.image.path)):
            return
        try:
            img = Image.open(self.image.path)
            if img.width <= 256 and img.height <= 256:
                return
            if img.width >= img.height:
                new_size = (256, int(img.height * 256 / img.width))
            else:
                new_size = (int(img.width * 256 / img.height), 256)
            img.resize(new_size, Image.LANCZOS).save(self.image.path)
        except Exception as e:
            print(f"Error resizing custom inline image: {e}")


class FactionAbility(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='abilities', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=100)
    body = models.TextField()

class ContentBox(models.Model):
    class KindChoices(models.TextChoices):
        SECTION = 'section', 'Section'
        BOX = 'box', 'Bordered Box'
        TRACK = 'track', 'Cardboard Track'
        LEGEND = 'legend', 'Legend'
        SCALE = 'scale', 'Scale'
        ACTIONS = 'actions', 'Actions'

    sheet = models.ForeignKey(FactionSheet, related_name='content_boxes', on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=KindChoices.choices, default=KindChoices.SECTION)
    title = models.CharField(max_length=200, blank=True, default='')
    text = models.TextField(blank=True, default='')
    order = models.PositiveIntegerField()

    # Per-layout placement overrides (inches). Null = auto.
    x_h = models.FloatField(blank=True, null=True)
    y_h = models.FloatField(blank=True, null=True)
    w_h = models.FloatField(blank=True, null=True)
    h_h = models.FloatField(blank=True, null=True)
    x_v = models.FloatField(blank=True, null=True)
    y_v = models.FloatField(blank=True, null=True)
    w_v = models.FloatField(blank=True, null=True)
    h_v = models.FloatField(blank=True, null=True)

    class Meta:
        ordering = ['order']

class PhaseStep(models.Model):
    class PhaseChoices(models.TextChoices):
        BIRDSONG = 'birdsong'
        DAYLIGHT = 'daylight'
        EVENING = 'evening'
        OTHER = 'other'

    class ActionType(models.TextChoices):
        ACTION = 'action', 'Action'
        ITEM = 'item', 'Item'
        CARD = 'card', 'Card'
        OTHER = 'other', 'Other'

    sheet = models.ForeignKey(FactionSheet, related_name='phase_steps', on_delete=models.CASCADE)
    content_box = models.ForeignKey(ContentBox, related_name='steps', on_delete=models.CASCADE, blank=True, null=True)
    phase = models.CharField(max_length=30, choices=PhaseChoices.choices)
    number = models.PositiveIntegerField()
    text = models.TextField(blank=True, default='')
    action_type = models.CharField(max_length=10, choices=ActionType.choices, default=ActionType.ACTION)
    step_cost_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = PhaseStep.objects.get(pk=self.pk)
                if old.step_cost_image and old.step_cost_image != self.step_cost_image:
                    delete_old_image(old.step_cost_image)
            except PhaseStep.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def allowed_cost_choices(self):
        """Subset of StepAction.CostChoices valid for this step's action_type.
        Returns a list of (value, label) tuples. OTHER returns just OTHER."""
        if self.action_type == self.ActionType.ACTION:
            return [(StepAction.CostChoices.ACTION.value, StepAction.CostChoices.ACTION.label)]
        if self.action_type == self.ActionType.ITEM:
            return [(c.value, c.label) for c in StepAction.CostChoices if c.value.startswith('item_')]
        if self.action_type == self.ActionType.CARD:
            return [(c.value, c.label) for c in StepAction.CostChoices if c.value.startswith('card_')]
        return [(StepAction.CostChoices.OTHER.value, StepAction.CostChoices.OTHER.label)]

    def cost_choices_with(self, current_value):
        """Like `allowed_cost_choices` but also includes `current_value` if it
        falls outside the allowed set. Lets a row dropdown preserve a saved
        cost that doesn't match the step's current action_type."""
        choices = list(self.allowed_cost_choices())
        if current_value and not any(v == current_value for v, _ in choices):
            label = dict(StepAction.CostChoices.choices).get(current_value, current_value)
            choices.append((current_value, label))
        return choices

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.phase == self.PhaseChoices.OTHER and not self.content_box:
            raise ValidationError({'content_box': 'A ContentBox is required when phase is "Other".'})
        if self.phase != self.PhaseChoices.OTHER and self.content_box:
            raise ValidationError({'content_box': 'ContentBox should only be set when phase is "Other".'})

    class Meta:
        ordering = ['phase', 'number']

    @property
    def ordered_children(self):
        """Boxes, tracks, legends, scales merged into one sequence ordered by `order`.
        Yields dicts with kind/obj/order so templates can render the mixed list.
        Tiebreak by kind to keep the order stable when several share an `order` value."""
        items = []
        for b in self.boxes.all():
            items.append({'kind': 'box', 'obj': b, 'order': b.order})
        for t in self.tracks.all():
            items.append({'kind': 'track', 'obj': t, 'order': t.order})
        for L in self.legends.all():
            items.append({'kind': 'legend', 'obj': L, 'order': L.order})
        for s in self.scales.all():
            items.append({'kind': 'scale', 'obj': s, 'order': s.order})
        items.sort(key=lambda i: (i['order'], i['kind']))
        return items

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
        CARD = 'card_vertical', 'Card'
        # Custom
        OTHER = 'other', 'Other (Custom Image)'

    step = models.ForeignKey(PhaseStep, related_name='actions', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    text = models.TextField()
    cost = models.CharField(max_length=20, choices=CostChoices.choices, default=CostChoices.ACTION)
    cost_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.cost == self.CostChoices.OTHER and not self.cost_image:
            raise ValidationError({'cost_image': 'A custom image is required when cost is "Other".'})

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = StepAction.objects.get(pk=self.pk)
                if old.cost_image and old.cost_image != self.cost_image:
                    delete_old_image(old.cost_image)
            except StepAction.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class DecreeSection(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='decrees', on_delete=models.CASCADE)
    title = models.CharField(max_length=15, blank=True, null=True)
    body = models.CharField(max_length=52, blank=True, null=True)


class CardSlot(models.Model):
    decree = models.ForeignKey(DecreeSection, related_name='card_slots', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=20, blank=True, null=True)
    body = models.CharField(max_length=80, blank=True, null=True)

    class Meta:
        ordering = ['number']


class CardPile(models.Model):
    class Orientation(models.TextChoices):
        BOTTOM = 'bottom', 'Bottom'
        LEFT = 'left', 'Left'
        RIGHT = 'right', 'Right'

    sheet = models.ForeignKey(FactionSheet, related_name='card_piles', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, null=True)
    body = models.TextField(blank=True, null=True)

    # Per-layout coordinate overrides (inches). Size is fixed; only x/y can be overridden.
    x_h = models.FloatField(blank=True, null=True)
    y_h = models.FloatField(blank=True, null=True)
    x_v = models.FloatField(blank=True, null=True)
    y_v = models.FloatField(blank=True, null=True)

    orientation_h = models.CharField(max_length=10, choices=Orientation.choices,
                                     default=Orientation.BOTTOM)
    orientation_v = models.CharField(max_length=10, choices=Orientation.choices,
                                     default=Orientation.BOTTOM)

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
    num_rows = models.PositiveIntegerField(default=1)

    def grid(self):
        """Return a 2D list `grid[row][col]` of CardboardSlot or None.
        Used by the editor template to render the live grid preview."""
        cells = [[None] * self.num_columns for _ in range(self.num_rows)]
        for slot in self.slots.all():
            if slot.row < self.num_rows and slot.column < self.num_columns:
                cells[slot.row][slot.column] = slot
        return cells
    column_headers = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Pipe-delimited column labels, e.g. "0|1|2|3|4"'
    )
    row_titles = models.CharField(
        max_length=2000, blank=True, default='',
        help_text='Pipe-delimited per-row titles, e.g. "Phase 1|Phase 2|Phase 3"'
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
    background_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.column_headers:
            headers = self.column_headers.split('|')
            if len(headers) != self.num_columns:
                raise ValidationError({
                    'column_headers': f'Expected {self.num_columns} headers, got {len(headers)}.'
                })
        if self.row_titles:
            titles = self.row_titles.split('|')
            if len(titles) != self.num_rows:
                raise ValidationError({
                    'row_titles': f'Expected {self.num_rows} row titles, got {len(titles)}.'
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

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = CardboardTrack.objects.get(pk=self.pk)
                if old.background_image and old.background_image != self.background_image:
                    delete_old_image(old.background_image)
            except CardboardTrack.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class CardboardSlot(models.Model):
    track = models.ForeignKey(CardboardTrack, related_name='slots', on_delete=models.CASCADE)
    number = models.PositiveIntegerField(help_text='For admin ordering')
    row = models.PositiveIntegerField(default=0)
    column = models.PositiveIntegerField(default=0)
    content = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Pipe-delimited image keywords, e.g. "1VP" or "fox|1VP"'
    )
    background_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)

    class Meta:
        ordering = ['row', 'column']
        unique_together = [('track', 'row', 'column')]

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = CardboardSlot.objects.get(pk=self.pk)
                if old.background_image and old.background_image != self.background_image:
                    delete_old_image(old.background_image)
            except CardboardSlot.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    @property
    def content_keywords(self):
        return [k.strip() for k in (self.content or '').split('|') if k.strip()]


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

    how_to_play_title = models.TextField(default='Faction')
    how_to_play_text = models.TextField(blank=True, null=True)
    back_image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)

    image_preview = models.ImageField(upload_to=back_preview_upload_path, blank=True, null=True)
    preview_fingerprint = models.CharField(max_length=32, blank=True, default='')

    last_updated = models.DateTimeField(auto_now=True)
    last_generated = models.DateTimeField(blank=True, null=True)

    def save(self, *args, **kwargs):
        new = self.pk is None
        if self.pk:
            try:
                old = FactionBack.objects.get(pk=self.pk)
                for field_name in ('back_image', 'image_preview'):
                    old_img = getattr(old, field_name)
                    new_img = getattr(self, field_name)
                    if old_img and old_img != new_img:
                        delete_old_image(old_img)
            except FactionBack.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if new:
            from the_gatehouse.tasks import send_rich_discord_message_task
            from django.urls import reverse
            url = reverse('forge-faction-detail', kwargs={'pk': self.faction.pk})
            fields = [{'name': 'By:', 'value': str(self.faction.designer)}]
            send_rich_discord_message_task.delay(
                f'[{self.faction.faction_name}](https://therootdatabase.com{url})',
                category='Forge', title='New Faction Back', fields=fields,
            )


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
    small_icon = models.ImageField(upload_to=faction_upload_path, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = Piece.objects.get(pk=self.pk)
                if old.small_icon and old.small_icon != self.small_icon:
                    delete_old_image(old.small_icon)
            except Piece.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class SetupCard(models.Model):
    class TypeChoices(models.TextChoices):
        MILITANT = 'M', 'Militant'
        INSURGENT = 'I', 'Insurgent'
    faction = models.OneToOneField(ForgedFaction, related_name='setup_card', on_delete=models.CASCADE)
    type = models.CharField(max_length=10, choices=TypeChoices.choices, default=TypeChoices.INSURGENT)
    reach = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], default=4)
    header_image = models.ImageField(upload_to=faction_upload_path, null=True, blank=True)

    image_preview = models.ImageField(upload_to=card_preview_upload_path, blank=True, null=True)
    preview_fingerprint = models.CharField(max_length=32, blank=True, default='')

    last_updated = models.DateTimeField(auto_now=True)
    last_generated = models.DateTimeField(blank=True, null=True)

    def save(self, *args, **kwargs):
        new = self.pk is None
        if self.pk:
            try:
                old = SetupCard.objects.get(pk=self.pk)
                for field_name in ('header_image', 'image_preview'):
                    old_img = getattr(old, field_name)
                    new_img = getattr(self, field_name)
                    if old_img and old_img != new_img:
                        delete_old_image(old_img)
            except SetupCard.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if new:
            from the_gatehouse.tasks import send_rich_discord_message_task
            from django.urls import reverse
            url = reverse('forge-faction-detail', kwargs={'pk': self.faction.pk})
            fields = [
                {'name': 'By:', 'value': str(self.faction.designer)},
                {'name': 'Type:', 'value': self.get_type_display()},
                {'name': 'Reach:', 'value': str(self.reach)},
            ]
            send_rich_discord_message_task.delay(
                f'[{self.faction.faction_name}](https://therootdatabase.com{url})',
                category='Forge', title='New Adset Card', fields=fields,
            )


class SetupStep(models.Model):
    faction_back = models.ForeignKey(FactionBack, related_name='setup_steps', on_delete=models.CASCADE, blank=True, null=True)
    card = models.ForeignKey(SetupCard, related_name='setup_steps', on_delete=models.CASCADE, blank=True, null=True)
    number = models.PositiveIntegerField()
    text = models.TextField()


class Legend(models.Model):
    step = models.ForeignKey(PhaseStep, related_name='legends', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['order']


class LegendRow(models.Model):
    legend = models.ForeignKey(Legend, related_name='rows', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=100)
    image = models.ImageField(upload_to=faction_upload_path, blank=True, null=True)
    body = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['order']

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = LegendRow.objects.get(pk=self.pk)
                if old.image and old.image != self.image:
                    delete_old_image(old.image)
            except LegendRow.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class Scale(models.Model):
    step = models.ForeignKey(PhaseStep, related_name='scales', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['order']


class ScaleRow(models.Model):
    scale = models.ForeignKey(Scale, related_name='rows', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    range = models.CharField(max_length=20)
    result = models.CharField(max_length=200)

    class Meta:
        ordering = ['order']