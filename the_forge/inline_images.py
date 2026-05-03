"""Forge-specific inline image keyword map and picker order.

This module is the single curation point for the rich-text image picker on
forge editor pages and for `format_forge_text` rendering on forge display
pages. To add, remove, or reorder a keyword:

  - Add the URL to `FORGE_INLINE_IMAGES` (the keyword -> static URL map).
  - Add the keyword to `FORGE_PICKER_ORDER` in the order it should appear in
    the toolbar's image picker. Keywords missing from this list are hidden
    from the picker even if they exist in the map (so we can still render
    legacy {{ keyword }} content without showing the keyword as a button).

PDF rendering also reads from this map via `pdf_engine._inline_image_path`,
which resolves the `static()` URL back to a filesystem path through Django's
staticfiles finders. This module is the single source of truth for both
editor and PDF — adding a keyword here makes it available in both.
"""

from django.templatetags.static import static


# Keyword -> static URL. Order here doesn't matter; picker order is set
# by FORGE_PICKER_ORDER below. Add aliases (e.g. `bunny`/`rabbit`) by
# pointing multiple keys at the same URL.
FORGE_INLINE_IMAGES = {
    # Items (law icon set)
    'law_torch': static('items/law/torch.png'),
    'law_tea': static('items/law/tea.png'),
    'law_sword': static('items/law/sword.png'),
    'law_bag': static('items/law/bag.png'),
    'law_hammer': static('items/law/hammer.png'),
    'law_crossbow': static('items/law/crossbow.png'),
    'law_coin': static('items/law/coin.png'),
    'law_boot': static('items/law/boot.png'),

    # Items (wo background)
    'item_torch': static('pdf/inline/item_torch.png'),
    'item_tea': static('pdf/inline/item_tea.png'),
    'item_sword': static('pdf/inline/item_sword.png'),
    'item_bag': static('pdf/inline/item_bag.png'),
    'item_hammer': static('pdf/inline/item_hammer.png'),
    'item_crossbow': static('pdf/inline/item_crossbow.png'),
    'item_coin': static('pdf/inline/item_coin.png'),
    'item_boot': static('pdf/inline/item_boot.png'),

    # Hirelings
    'hired': static('items/law/hired.png'),
    'ability': static('items/law/ability.png'),
    'daylight': static('items/law/daylight.png'),
    'birdsong': static('items/law/birdsong.png'),

    # Other
    'meeple': static('pdf/inline/meeple.png'),
    'fox_icon': static('pdf/inline/fox_icon.png'),
    'mouse_icon': static('pdf/inline/mouse_icon.png'),
    'rabbit_icon': static('pdf/inline/rabbit_icon.png'),
    'fox_outline': static('pdf/inline/fox_outline.png'),
    'mouse_outline': static('pdf/inline/mouse_outline.png'),
    'rabbit_outline': static('pdf/inline/rabbit_outline.png'),
    'fox_craft': static('pdf/inline/fox_craft.png'),
    'mouse_craft': static('pdf/inline/mouse_craft.png'),
    'rabbit_craft': static('pdf/inline/rabbit_craft.png'),
    # 'wild_craft': static('pdf/inline/wild_craft.png'),


    # Draw cards
    '+card': static('pdf/inline/+card.png'),
    '-card': static('pdf/inline/-card.png'),

    'card_vertical': static('pdf/inline/card_vertical.png'),
    'card': static('pdf/inline/card.png'),
    'card2': static('pdf/inline/card2.png'),
    'card3': static('pdf/inline/card3.png'),
    'card4': static('pdf/inline/card4.png'),

    # Animals (law icon set)
    'bunny': static('items/law/bunny.png'),
    'rat': static('items/law/rat.png'),
    'vb': static('items/law/raccoon.png'),
    'otter': static('items/law/otter.png'),
    'cat': static('items/law/cat.png'),
    'badger': static('items/law/badger.png'),
    'bird': static('items/law/bird.png'),
    'mole': static('items/law/mole.png'),
    'lizard': static('items/law/lizard.png'),
    'crow': static('items/law/crow.png'),
    'frog': static('items/law/frog.png'),
    'bat': static('items/law/bat.png'),
    'skunk': static('items/law/skunk.png'),

    # Cards (suits)
    'fox_card': static('pdf/inline/fox_card.png'),
    'mouse_card': static('pdf/inline/mouse_card.png'),
    'rabbit_card': static('pdf/inline/rabbit_card.png'),
    'bird_card': static('pdf/inline/bird_card.png'),
    'cards': static('pdf/inline/other_cards.png'),

    # Tilts
    'fox_tilt': static('pdf/inline/fox_tilt.png'),
    'mouse_tilt': static('pdf/inline/mouse_tilt.png'),
    'rabbit_tilt': static('pdf/inline/rabbit_tilt.png'),
    'bird_tilt': static('pdf/inline/bird_tilt.png'),

    # Victory points
    'VP': static('pdf/inline/VP.png'),
    '0VP': static('pdf/inline/0VP.png'),
    '1VP': static('pdf/inline/1VP.png'),
    '2VP': static('pdf/inline/2VP.png'),
    '3VP': static('pdf/inline/3VP.png'),
    '4VP': static('pdf/inline/4VP.png'),
    '5VP': static('pdf/inline/5VP.png'),
    '6VP': static('pdf/inline/6VP.png'),
    '7VP': static('pdf/inline/7VP.png'),
    '8VP': static('pdf/inline/8VP.png'),
    '9VP': static('pdf/inline/9VP.png'),
    '-1VP': static('pdf/inline/-1VP.png'),
    '-2VP': static('pdf/inline/-2VP.png'),
    '-3VP': static('pdf/inline/-3VP.png'),
    '-4VP': static('pdf/inline/-4VP.png'),
    '-5VP': static('pdf/inline/-5VP.png'),
    '-6VP': static('pdf/inline/-6VP.png'),
    '-7VP': static('pdf/inline/-7VP.png'),
    '-8VP': static('pdf/inline/-8VP.png'),
    '-9VP': static('pdf/inline/-9VP.png'),
}


# Ordered list controlling which keywords appear in the image picker, and
# in what order. Keys not listed here are still recognised by
# `format_forge_text` (so existing saved {{ keyword }} content keeps
# rendering) but won't show as a button in the picker.
FORGE_PICKER_ORDER = [


    # Cards
    '+card',
    '-card',

    # VP
    'VP',
    '0VP',
    '1VP',
    '2VP',
    '3VP',
    '4VP',
    '5VP',
    '-1VP',

    'cards',
    'bird_card',
    'bird_tilt',
    'fox_card',
    'fox_tilt',
    'mouse_card',
    'mouse_tilt',
    'rabbit_card',
    'rabbit_tilt',


    # More cards
    'card_vertical',
    'card',
    'card2',
    'card3',
    'card4',

    # Icons
    'meeple',
    'fox_icon',
    'fox_outline',
    'fox_craft',
    'mouse_icon',
    'mouse_outline',
    'mouse_craft',
    'rabbit_icon',
    'rabbit_outline',
    'rabbit_craft',
    # 'wild_craft',






    # # Animals
    # 'bunny',
    # 'bird',
    # 'cat',
    # 'vb',
    # 'lizard',
    # 'otter',
    # 'mole',
    # 'crow',

    # 'rat',
    # 'badger',

    # 'frog',
    # 'bat',
    # 'skunk',

    # Items
    'item_sword',
    'item_hammer',
    'item_crossbow',
    'item_bag',
    'item_boot',
    'item_tea',
    'item_coin',
    'item_torch',


    '9VP',
    '8VP',
    '7VP',
    '6VP',
    '-2VP',
    '-3VP',
    '-4VP',
    '-5VP',
    '-6VP',
    '-7VP',
    '-8VP',
    '-9VP',

    'law_sword',
    'law_hammer',
    'law_crossbow',
    'law_bag',
    'law_boot',
    'law_tea',
    'law_coin',
    'law_torch',

    # Hirelings
    'birdsong',
    'daylight',
    'hired',
    'ability',


]


def picker_keywords():
    """Return the ordered list of keywords to display in the picker.

    Filters out any entry that doesn't have a URL in FORGE_INLINE_IMAGES so
    a typo in FORGE_PICKER_ORDER doesn't render an empty button.
    """
    return [k for k in FORGE_PICKER_ORDER if k in FORGE_INLINE_IMAGES]


def picker_image_map():
    """Return the keyword -> URL map filtered to keys the picker will show
    plus any other keys still referenced by saved content. We pass the full
    map so the editor can hydrate existing {{ keyword }} markers even when
    the keyword has been removed from the picker order."""
    return dict(FORGE_INLINE_IMAGES)
