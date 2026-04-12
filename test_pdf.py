# test_pdf.py  (run from project root)
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'django_project.settings'
django.setup()

from types import SimpleNamespace
from the_forge.pdf_engine import SheetLayoutEngine
from the_forge.models import ForgedFaction

# Fake abilities
abilities = [
    SimpleNamespace(title="Governors", body="In battle, deal +1 hit. Then you ", order=1),
    SimpleNamespace(title="Nimble", body="May move before or after battle. Additionally, whenever you remove an enemy warrior in battle, you may move that warrior's matching piece to any adjacent clearing you rule.", order=2),
    SimpleNamespace(title="Battle Plans II", body="In battle, deal +1 hit. Then you ", order=3),
]

# Fake phase steps
steps = [
    SimpleNamespace(phase="birdsong", number=1, text="##Craft## with enclaves."),
    SimpleNamespace(phase="birdsong", number=2, text="##Protect the Weak## \n Once per acclaim, you may spend a card matching its clearing to place 1 Skunk at it."),
    SimpleNamespace(phase="birdsong", number=3, text="##Take it Easy## if all Captain cards are face down Flip all Captains and items face up. The enemy with the most Prisoners chooses a clearing and places their adjacent Prisoners into it. _(On a tie, choose a tied enemy.)_"),
    SimpleNamespace(phase="birdsong", number=4, text="##Draw## 1 card. ##Discard## down to 5 cards."),

    SimpleNamespace(phase="daylight", number=1, text="##March## Move up to 3 times."),
    SimpleNamespace(phase="daylight", number=2, text="##Battle## Initiate a battle in any clearing."),
    SimpleNamespace(phase="daylight", number=3, text="You must **Rally** or **Reconcile**. \n **Rally.**  Place 1 warrior. ##Build## Place a building in a clearing you rule, spending wood equal to the connected building cost."),
    SimpleNamespace(phase="daylight", number=4, text="Recruit. Place a warrior in each recruiter clearing."),
    SimpleNamespace(phase="daylight", number=5, text="##Settle## Choose a clearing. Spend a card to place one {{ 1VP }} at {{ 2VP }} {{ 3VP }} {{ 4VP }} {{ VP }} a _sawmill_ in the card's matching clearing."),
    
    SimpleNamespace(phase="evening", number=1, text="##Draw## Draw one card plus one per uncovered {{ draw }} {{ mouse }} {{ bird }} {{ rabbit tilt}}."),
    SimpleNamespace(phase="evening", number=5, text="##Settle## Choose a clearing. Spend a card to place one {{ 1VP }} at {{ 2VP }} {{ 3VP }} {{ 4VP }} {{ VP }} a _sawmill_ in the card's matching clearing."),

]

# Fake decree sections and card slots
decree_card_slots = [
    SimpleNamespace(number=1, title="Recruit", body=None),
    SimpleNamespace(number=2, title="Move", body=None),
    SimpleNamespace(number=3, title="Battle", body=None),
    # SimpleNamespace(number=4, title="Build", body=None),
    # SimpleNamespace(number=5, title="Empower", body=None),
]
decree_section = SimpleNamespace(
    type="decree",
    title="The Decree of Eyrie",
    body=None,
    card_slots=SimpleNamespace(all=lambda: decree_card_slots),
)
single_section = SimpleNamespace(
    type="single",
    title=None,
    body=None,
    card_slots=SimpleNamespace(all=lambda: []),
)
decree_sections = [single_section]  # decree_section removed temporarily

# Fake cardboard tracks
building_slots = [
    SimpleNamespace(title="1", number=1),
    SimpleNamespace(title="2", number=2),
    SimpleNamespace(title="3", number=3),
    SimpleNamespace(title="4", number=4),
    SimpleNamespace(title="5", number=5),
]
token_slots = [
    SimpleNamespace(title="1", number=1),
    SimpleNamespace(title="2", number=2),
    SimpleNamespace(title="3", number=3),
]
tracks = [
    SimpleNamespace(title="Sawmills", type="building", body=None,
                    slots=SimpleNamespace(all=lambda: building_slots)),
    SimpleNamespace(title="Keep Tokens", type="token", body=None,
                    slots=SimpleNamespace(all=lambda: token_slots)),
]

# Fake faction (ForgedFaction fields)
faction = SimpleNamespace(
    faction_name="Marquise de Cat",
    color="#D45B2C",
    repeat_background_image=True,
    background_preset="frogs",
    background_image=None,
    BACKGROUND_PRESET_FILES=ForgedFaction.BACKGROUND_PRESET_FILES,
)
faction.get_background_path = lambda: ForgedFaction.get_background_path(faction)

# Fake sheet (FactionSheet fields)
sheet = SimpleNamespace(
    faction=faction,
    flavor_text="You're the true voice of this woodland. Dissenters will burn. They will learn to fear your wrath. And bring you cheese.",
    include_crafted_items=True,
    layout_mode="horizontal",
    get_background_path=lambda: faction.get_background_path(),
    abilities=SimpleNamespace(order_by=lambda f: abilities),
    phase_steps=SimpleNamespace(all=lambda: steps),
    decrees=SimpleNamespace(prefetch_related=lambda *a: SimpleNamespace(all=lambda: decree_sections)),
    tracks=SimpleNamespace(prefetch_related=lambda *a: SimpleNamespace(all=lambda: tracks)),
)

engine = SheetLayoutEngine(sheet)
engine.build("test_output.pdf")
print("Generated test_output.pdf")
