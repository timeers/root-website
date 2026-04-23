# test_faction_back.py  (run from project root)
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'django_project.settings'
django.setup()

from types import SimpleNamespace
from the_forge.pdf_engine import FactionBackLayoutEngine
from the_forge.models import ForgedFaction


def make_piece(name, quantity, type_code, icon_path=None):
    icon_ns = None
    if icon_path and os.path.exists(icon_path):
        icon_ns = SimpleNamespace(path=icon_path)
    return SimpleNamespace(
        name=name,
        quantity=quantity,
        type=type_code,
        small_icon=icon_ns,
    )


STATIC_DIR = os.path.join(os.path.dirname(__file__), 'the_keep', 'static')
ICONS = {
    'warrior': os.path.join(STATIC_DIR, 'pdf/inline/mouse_card.png'),
    'building': os.path.join(STATIC_DIR, 'pdf/images/BuildingSlot.png'),
    'token': os.path.join(STATIC_DIR, 'pdf/images/TokenSlot.png'),
    'card': os.path.join(STATIC_DIR, 'pdf/inline/bird_card.png'),
}


pieces = [
    make_piece('Warriors', 15, 'W', ICONS['warrior']),
    make_piece('Veterans', 4, 'W'),  # no icon -> should fall back to faction-colored meeple.svg
    make_piece('Waystations\n(double-sided)', 3, 'B', ICONS['building']),
    make_piece('Tablet Relics', 4, 'T', ICONS['token']),
    make_piece('Figure Relics', 4, 'T', ICONS['token']),
    make_piece('Jewelry Relics', 4, 'T', ICONS['token']),
    make_piece('Faithful Retainer Cards', 3, 'C', ICONS['card']),
]

setup_steps = [
    SimpleNamespace(number=1, text='Collect all **12 relic tokens** and shuffle them face down (no value showing). Place one face down randomly in each forest.'),
    SimpleNamespace(number=2, text='Place **4 warriors** in a corner clearing that another player has not chosen as their starting clearing, then place **4 warriors** in an adjacent clearing on the map edge.'),
    SimpleNamespace(number=3, text='Place all remaining **relics** face down, as evenly as able, among forests not adjacent to your starting clearings.'),
    SimpleNamespace(number=4, text='_Flip your board_ and tuck a **Faithful Retainer card** into each of your Retinue column slots. Place all **3 waystations** on the Waystations spaces.'),
]


faction = SimpleNamespace(
    faction_name='Keepers in Iron',
    color='#5BB8D4',
    repeat_background_image=False,
    background_preset='badgers',
    background_image=None,
    BACKGROUND_PRESET_FILES=ForgedFaction.BACKGROUND_PRESET_FILES,
)
faction.get_background_path = lambda: ForgedFaction.get_background_path(faction)


faction_back = SimpleNamespace(
    faction=faction,
    complexity='H',
    card_wealth='H',
    aggression='M',
    crafting_ability='M',
    setup_order='K',
    how_to_play_title='Playing the Keepers',
    how_to_play_text=(
        'As the Keepers in Iron, you score points by recovering **relics** lost in past '
        'conflicts. You will need to **delve** relics out of the forests, move them to '
        'a **waystation** of the same type, and then **recover** them. Whether these '
        'relics belong to you or the Woodland, though, is another question.\n\n'
        'As **Devout Knights** of an exiled order, you ignore the first hit you take in '
        'battle if you have both a warrior and a relic in it, whether attacking or '
        'defending. You can also move relics with your warriors.\n\n'
        'Your relics are **Prized Trophies**, so keep them safe. Whenever an enemy '
        'removes a relic in any way, they score two points instead of one, and put it '
        'back in any forest.\n\n'
        'Over time, you will grow your **Retinue**, three columns of cards that let you '
        'take actions. Delving and recovering relics will put your Retinue at risk, '
        'though, so you will need to plan ahead and take prudent risks in order to succeed.'
    ),
    pieces=SimpleNamespace(all=lambda: pieces),
    setup_steps=SimpleNamespace(
        order_by=lambda f: sorted(setup_steps, key=lambda s: s.number),
        all=lambda: setup_steps,
    ),
)

engine = FactionBackLayoutEngine(faction_back)
engine.build('test_faction_back.pdf')
print('Generated test_faction_back.pdf')
