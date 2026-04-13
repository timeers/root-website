# test_pdf.py  (run from project root)
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'django_project.settings'
django.setup()

from types import SimpleNamespace
from the_forge.pdf_engine import SheetLayoutEngine
from the_forge.models import ForgedFaction

# Helper to create a fake queryset-like actions attribute
def make_actions_qs(actions_list):
    return SimpleNamespace(
        order_by=lambda f: actions_list,
        all=lambda: actions_list,
    )

# Fake abilities
abilities = [
    SimpleNamespace(title="Governors", body="In battle, deal +1 hit. Then you ", order=1),
    SimpleNamespace(title="Nimble", body="May move before or after battle. Additionally, whenever you remove an enemy warrior in battle, you may move that warrior's matching piece to any adjacent clearing you rule.", order=2),
    SimpleNamespace(title="Battle Plans II", body="In battle, deal +1 hit. Then you ", order=3),
]

# Fake phase steps
steps = [
    SimpleNamespace(phase="birdsong", number=1, text="##Craft## with enclaves."),
    SimpleNamespace(phase="birdsong", number=2, text="##Settle## Choose a clearing. Spend a card to place one {{ 1VP }} at {{ 2VP }} {{ 3VP }} {{ 4VP }} {{ VP }} a _sawmill_ in the card's matching clearing."),
    SimpleNamespace(phase="birdsong", number=3, text="##Take it Easy## if all Captain cards are face down Flip all Captains and items face up. The enemy with the most Prisoners chooses a clearing and places their adjacent Prisoners into it. _(On a tie, choose a tied enemy.)_"),
    SimpleNamespace(phase="birdsong", number=4, text="##Draw## 1 card. ##Discard## down to 5 cards."),

    SimpleNamespace(phase="daylight", number=1, text="##March## Move up to 3 times."),
    SimpleNamespace(phase="daylight", number=2, text="##Battle## Initiate a battle in any clearing."),
    SimpleNamespace(phase="daylight", number=3, text="You must **Rally** or **Reconcile**. \n **Rally.**  Place 1 warrior. ##Build## Place a building in a clearing you rule, spending wood equal to the connected building cost."),
    SimpleNamespace(phase="daylight", number=4, text="Recruit. Place a warrior in each recruiter clearing."),
    SimpleNamespace(phase="daylight", number=5, text="##Settle## Choose a clearing. Spend a card to place one {{ 1VP }} at {{ 2VP }} {{ 3VP }} {{ 4VP }} {{ VP }} a _sawmill_ in the card's matching clearing."),
    
    SimpleNamespace(phase="evening", number=1, text="**Draw** Draw one card plus one per uncovered {{ draw }} {{ mouse }} {{ bird }} {{ rabbit tilt}}."),

]

# Fake StepActions
sword_action = SimpleNamespace(
    cost='item_sword', cost_image=None, order=1,
    text="##Battle## in your clearing. Max hits equals undamaged swords."
)
boots_action = SimpleNamespace(
    cost='item_boots', cost_image=None, order=2,
    text="**Move** _not into forest_"
)
torch_action = SimpleNamespace(
    cost='item_torch', cost_image=None, order=1,
    text="##Explore## Take item from ruin in your clearing. Score {{ 1VP }}."
)
any_action = SimpleNamespace(
    cost='item_any', cost_image=None, order=2,
    text="##Aid## Give a card matching your clearing to a player there. ##And## then increase your relationship with that faction if you have met the ##requirements##."
)
fox_action = SimpleNamespace(
    cost='card_nonbird', cost_image=None, order=1,
    text="##Build## Place a garden in a clearing you rule."
)
fox_action2 = SimpleNamespace(
    cost='card_nonbird', cost_image=None, order=2,
    text="**Recruit** Place a warrior in a {{fox}} fox clearing."
)
fox_action3 = SimpleNamespace(
    cost='card_nonbird', cost_image=None, order=3,
    text="##Score## _once per suit_ Discard an unrevealed {{ VP }} of rightmost empty Gardens space."
)
rabbit_action = SimpleNamespace(
    cost='card_bird', cost_image=None, order=4,
    text="##Sacrifice## Place one warrior in the Acolytes box."
)
rabbit_action2 = SimpleNamespace(
    cost='card_bird', cost_image=None, order=5,
    text="##Convert## Replace one enemy warrior in a rabbit clearing with one of yours."
)
nonbird_action = SimpleNamespace(
    cost='card_nonbird', cost_image=None, order=1,
    text="**Score** Score 2 points."
)
action_action = SimpleNamespace(
    cost='action', cost_image=None, order=1,
    text="**Score** 1 {{ VP }}"
)
action_action_2 = SimpleNamespace(
    cost='action', cost_image=None, order=2,
    text="**Refresh** 1 item"
)


# Assign actions to steps
steps[0].actions = make_actions_qs([])
steps[1].actions = make_actions_qs([])
steps[2].actions = make_actions_qs([sword_action, boots_action])
steps[3].actions = make_actions_qs([])
steps[4].actions = make_actions_qs([torch_action, any_action])
# 3 fox actions (odd group) + 2 rabbit actions (even group)
steps[5].actions = make_actions_qs([fox_action, fox_action2, fox_action3, rabbit_action, rabbit_action2])
steps[6].actions = make_actions_qs([action_action, action_action_2])
steps[7].actions = make_actions_qs([])
steps[8].actions = make_actions_qs([])
# Single non-bird action (wider icon, shorter arrow)
steps[9].actions = make_actions_qs([nonbird_action])


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
)

engine = SheetLayoutEngine(sheet)
engine.tracks = tracks  # Override since test uses fakes, not real DB objects
engine.build("test_output.pdf")
print("Generated test_output.pdf")
