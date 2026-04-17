# test_pdf_simple.py  (run from project root)
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

# Helper to create a fake queryset-like boxes attribute
def make_boxes_qs(boxes_list):
    return SimpleNamespace(
        order_by=lambda f: boxes_list,
        all=lambda: boxes_list,
    )

# Helper for fake track querysets
def make_tracks_qs(tracks_list):
    return SimpleNamespace(
        order_by=lambda f: tracks_list,
        all=lambda: tracks_list,
    )

# Fake abilities
abilities = [
    SimpleNamespace(title="Nimble", body="May move before or after battle.", order=1),
]

# Fake phase steps (one per phase)
steps = [
    SimpleNamespace(phase="birdsong", number=1, text="**Draw** one card plus one per uncovered {{ draw }}."),
    SimpleNamespace(phase="birdsong", number=2, text="##Craft## with workshops."),
    SimpleNamespace(phase="daylight", number=1, text="**Draw** one card plus one per uncovered {{ draw }}."),
    SimpleNamespace(phase="evening", number=1, text="**Draw** one card plus one per uncovered {{ draw }}."),
    SimpleNamespace(phase="evening", number=2, text="##March## Move up to 3 times."),
    SimpleNamespace(phase="evening", number=3, text="##March## Move up to 4 times."),

]

# One action on the daylight step
sword_action = SimpleNamespace(
    cost='item_sword', cost_image=None, order=1,
    text="##Battle## in your clearing."
)

# Token track with enough columns to trigger overlap
sympathy_bg = SimpleNamespace(path="the_keep/static/pdf/inline/Sympathy Token.png")
roosts_slots = [
    SimpleNamespace(number=i+1, row=0, column=i, row_title="", content=f"{i}VP" if i > 0 else "", background_image=sympathy_bg)
    for i in range(4)
]
roosts_track = SimpleNamespace(
    title="Roosts", type="token", body="_Only one per clearing_", order=0,
    num_columns=4, column_headers="{{ 1VP }}|{{ cards }}|{{ bird }}|{{ 4VP }}|{{ 1VP }}|{{ cards }}|{{ bird }}", column_cost_type="",
    column_dividers="3", background_image=None, header_position="above",
    slots=SimpleNamespace(all=lambda: roosts_slots),
)

# Assign actions, boxes, tracks to steps
steps[0].actions = make_actions_qs([])
steps[1].actions = make_actions_qs([sword_action])
steps[2].actions = make_actions_qs([sword_action])
steps[3].actions = make_actions_qs([sword_action])
steps[4].actions = make_actions_qs([sword_action])

for s in steps:
    s.boxes = make_boxes_qs([])
    s.tracks = make_tracks_qs([])

steps[2].tracks = make_tracks_qs([roosts_track])

# Fake faction
faction = SimpleNamespace(
    faction_name="Marquise de Cat",
    color="#FF6800",
    repeat_background_image=True,
    background_preset="frogs",
    background_image=None,
    BACKGROUND_PRESET_FILES=ForgedFaction.BACKGROUND_PRESET_FILES,
)
faction.get_background_path = lambda: ForgedFaction.get_background_path(faction)

# Fake sheet
sheet = SimpleNamespace(
    faction=faction,
    flavor_text="You rule this woodland.",
    include_crafted_items=True,
    layout_mode="vertical",
    get_background_path=lambda: faction.get_background_path(),
    abilities=SimpleNamespace(order_by=lambda f: abilities),
    phase_steps=SimpleNamespace(all=lambda: steps),
    decrees=SimpleNamespace(prefetch_related=lambda *a: SimpleNamespace(all=lambda: [])),
)

engine = SheetLayoutEngine(sheet)
engine.build("test_output_simple.pdf")
print("Generated test_output_simple.pdf")
