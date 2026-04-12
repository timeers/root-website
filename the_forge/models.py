from django.db import models

from the_gatehouse.models import Profile
from the_keep.utils import validate_hex_color

class FactionSheet(models.Model):
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
    }

    designer = models.ForeignKey(Profile, related_name='faction_sheets', on_delete=models.CASCADE)
    faction_name = models.CharField(max_length=100)
    flavor_text = models.TextField(blank=True, null=True)
    background_preset = models.CharField(
        max_length=20,
        choices=BackgroundPreset.choices,
        blank=True,
        default='',
        help_text="Select a preset background, or upload your own below."
    )
    background_image = models.ImageField(upload_to='sheet_backgrounds/', blank=True, null=True)
    color = models.CharField(
        max_length=7,
        blank=True,
        null=True,
        validators=[validate_hex_color],
        help_text="Enter a hex color code (e.g., #RRGGBB)."
    )
    include_crafted_items = models.BooleanField(default=True)
    repeat_background_image = models.BooleanField(default=False)


    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.background_preset and not self.background_image:
            raise ValidationError("Select a preset background or upload a custom image.")

    def get_background_path(self):
        if self.background_preset:
            import os
            static_dir = os.path.join(os.path.dirname(__file__), '..', 'the_keep', 'static')
            return os.path.join(static_dir, self.BACKGROUND_PRESET_FILES[self.background_preset])
        return self.background_image.path

    class LayoutChoices(models.TextChoices):
        LAYOUT_AUTO = 'auto'
        LAYOUT_HORIZONTAL = 'horizontal'
        LAYOUT_VERTICAL = 'vertical'

    layout_mode = models.CharField(max_length=30, choices=LayoutChoices.choices, default=LayoutChoices.LAYOUT_AUTO)

class SheetAbility(models.Model):
    sheet = models.ForeignKey(FactionSheet, related_name='abilities', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=100)
    body = models.TextField()

class PhaseStep(models.Model):
    class PhaseChoices(models.TextChoices):
        BIRDSONG = 'birdsong'
        DAYLIGHT = 'daylight'
        EVENING = 'evening'
    sheet = models.ForeignKey(FactionSheet, related_name='phase_steps', on_delete=models.CASCADE)
    phase = models.CharField(max_length=30, choices=PhaseChoices.choices)
    number = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ['phase', 'number']

class StepSubItem(models.Model):
    step = models.ForeignKey(PhaseStep, related_name='sub_items', on_delete=models.CASCADE)
    order = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, null=True)

class DecreeSection(models.Model):
    class CardTypes(models.TextChoices):
        SINGLE = 'single'
        DECREE = 'decree'
    sheet = models.ForeignKey(FactionSheet, related_name='decrees', on_delete=models.CASCADE)
    title = models.CharField(max_length=15, blank=True, null=True)
    body = models.CharField(max_length=20, blank=True, null=True)
    type = models.CharField(max_length=20, choices=CardTypes.choices)


class CardSlot(models.Model):
    decree = models.ForeignKey(DecreeSection, related_name='card_slots', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, null=True)
    body = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['number']

class CardboardTrack(models.Model):
    class TrackChoices(models.TextChoices):
        TOKEN = 'token'
        BUILDING = 'building'
    sheet = models.ForeignKey(FactionSheet, related_name='tracks', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, null=True)
    type = models.CharField(max_length=20, choices=TrackChoices.choices)

class CardboardSlot(models.Model):
    track = models.ForeignKey(CardboardTrack, related_name='slots', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    number = models.PositiveIntegerField()

    class Meta:
        ordering = ['number']
