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

]

# One action on the daylight step
sword_action = SimpleNamespace(
    cost='item_sword', cost_image=None, order=1,
    text="##Battle## in your clearing."
)

# Token track with enough columns to trigger overlap
sympathy_bg = SimpleNamespace(path="the_keep/static/pdf/inline/Sympathy Token.png")
roosts_slots = [
    SimpleNamespace(number=i+1, row=0, column=i, row_title="Roosts" if i == 0 else "", content=f"{i}VP" if i > 0 else "", background_image=sympathy_bg)
    for i in range(5)
]
roosts_track = SimpleNamespace(
    title="Roosts", type="token", body="_Only one per clearing_", order=0,
    num_columns=5, column_headers="{{ 1VP }}|{{ cards }}|{{ bird }}|{{ 4VP }}|{{ 1VP }}|{{ cards }}|{{ bird }}", column_cost_type="",
    column_dividers="4", background_image=None, header_position="above",
    header_title="", row_title_orientation="vertical",
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

# Building track with multiple rows, row titles (with markup), and header title
building_slots = [
    # Sawmills row
    SimpleNamespace(number=1, row=0, column=0, row_title="**Sawmills**", content="", background_image=None),
    SimpleNamespace(number=2, row=0, column=1, row_title="", content="1VP", background_image=None),
    SimpleNamespace(number=3, row=0, column=2, row_title="", content="2VP", background_image=None),
    SimpleNamespace(number=4, row=0, column=3, row_title="", content="3VP", background_image=None),
    # # Workshops row
    # SimpleNamespace(number=5, row=1, column=0, row_title="**Workshops**", content="", background_image=None),
    # SimpleNamespace(number=6, row=1, column=1, row_title="", content="1VP", background_image=None),
    # SimpleNamespace(number=7, row=1, column=2, row_title="", content="2VP", background_image=None),
    # SimpleNamespace(number=8, row=1, column=3, row_title="", content="3VP", background_image=None),
    # # Recruiters row
    # SimpleNamespace(number=9, row=2, column=0, row_title="_Recruiters_", content="", background_image=None),
    # SimpleNamespace(number=10, row=2, column=1, row_title="", content="1VP", background_image=None),
    # SimpleNamespace(number=11, row=2, column=2, row_title="", content="2VP", background_image=None),
    # SimpleNamespace(number=12, row=2, column=3, row_title="", content="3VP", background_image=None),
    # # Recruiters row
    # SimpleNamespace(number=13, row=3, column=0, row_title="_Special_", content="", background_image=None),
    # SimpleNamespace(number=14, row=3, column=1, row_title="", content="1VP", background_image=None),
    # SimpleNamespace(number=15, row=3, column=2, row_title="", content="2VP", background_image=None),
    # SimpleNamespace(number=16, row=3, column=3, row_title="", content="3VP", background_image=None),
]
building_track = SimpleNamespace(
    title="Buildings", type="building", body="", order=1,
    num_columns=5, column_headers="0|1|2|3",
    column_cost_type="", column_dividers="", background_image=None, header_position="above",
    header_title="~~Cost~~", row_title_orientation="vertical",
    slots=SimpleNamespace(all=lambda: building_slots),
)
steps[2].tracks = make_tracks_qs([roosts_track])

# --- Content Boxes ---

# Content Box 1: Two action-cost actions
cb1_action1 = SimpleNamespace(cost='action', cost_image=None, order=1, text="##Battle## in one clearing you rule.")
cb1_action2 = SimpleNamespace(cost='action', cost_image=None, order=2, text="##Recruit## Place a warrior in each recruiter.")
cb1_step = SimpleNamespace(
    phase="other", number=1,
    text="Take these actions in any order.",
    actions=make_actions_qs([cb1_action1, cb1_action2]),
    boxes=make_boxes_qs([]),
    tracks=make_tracks_qs([]),
)
content_box_1 = SimpleNamespace(
    title="Decree", text="", order=1,
    steps=SimpleNamespace(all=lambda: [cb1_step]),
)

# Content Box 2: Bordered box
cb2_bordered = SimpleNamespace(title="Martial Law", body="If you have 3+ warriors in a clearing, you rule it.", height="small", order=1)
cb2_step = SimpleNamespace(
    phase="other", number=1,
    text="",
    actions=make_actions_qs([]),
    boxes=make_boxes_qs([cb2_bordered]),
    tracks=make_tracks_qs([]),
)
content_box_2 = SimpleNamespace(
    title="", text="", order=2,
    steps=SimpleNamespace(all=lambda: [cb2_step]),
)


cb3_step = SimpleNamespace(
    phase="other", number=1,
    text="Place buildings in clearings you rule.",
    actions=make_actions_qs([]),
    boxes=make_boxes_qs([]),
    tracks=make_tracks_qs([building_track]),
)
content_box_3 = SimpleNamespace(
    title="Buildings", text="", order=3,
    steps=SimpleNamespace(all=lambda: [cb3_step]),
)

all_content_boxes = [content_box_1, content_box_2, content_box_3]

# Helper to fake the content_boxes queryset chain
def _make_content_boxes_qs(boxes):
    return SimpleNamespace(
        prefetch_related=lambda *a: SimpleNamespace(
            order_by=lambda f: boxes,
        ),
    )

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
    card_piles=SimpleNamespace(all=lambda: [
        SimpleNamespace(number=1, title="Character Card", body="Discard a card of matching suit to **recruit**. {{ fox }} {{ bird }}"),
        SimpleNamespace(number=2, title="Mood Card", body="_Set up with the Stubborn mood._"),
    ]),
    content_boxes=_make_content_boxes_qs(all_content_boxes),
)

engine = SheetLayoutEngine(sheet)
engine.build("test_output_simple.pdf")
print("Generated test_output_simple.pdf")
