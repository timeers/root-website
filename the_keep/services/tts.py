# tts.py
import os
import hashlib
import random
import math

from PIL import Image

from django.conf import settings

from the_keep.models import CardDeck

FACTION_BOARD_TRANSFORM = {
    "posX": 0.0,
    "posY": -0.113604546,
    "posZ": 0.0,
    "rotX": 0.0,
    "rotY": 180.0,
    "rotZ": 0.0,
    "scaleX": 9.035468,
    "scaleY": 1.0,
    "scaleZ": 9.035468,
}

DEFAULT_CARD_TRANSFORM = {
    "posX": 0.0,
    "posY": 2.0,
    "posZ": 0.0,
    "rotX": 0.0,
    "rotY": 180.0,
    "rotZ": 0.0,
    "scaleX": 2.3,
    "scaleY": 1.0,
    "scaleZ": 2.3,
}
# 0.158 x between snap points
CRAFTED_ITEMS_SNAP_POINTS = [
    {"Position": {"x": -0.64,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
    {"Position": {"x": -0.798,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
    {"Position": {"x": -0.956, "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
    {"Position": {"x": -1.097,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
]
## Old values
# CRAFTED_ITEMS_SNAP_POINTS = [
#     {"Position": {"x": -0.64,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
#     {"Position": {"x": -0.79,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
#     {"Position": {"x": -0.939, "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
#     {"Position": {"x": -1.09,  "y": 0.1, "z": -0.65}, "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0}},
# ]

def generate_tts_guid():
    return "".join(random.choices("0123456789abcdef", k=6))

def tts_image_url(image, request=None):
    """
    Return the URL for TTS, prepended with {verifycache}.
    If `request` is given, returns an absolute URL.
    """
    if not image:
        return ""
    
    url = image.url if hasattr(image, "url") else image
    if request:
        url = request.build_absolute_uri(url)
    return f"{{verifycache}}{url}"


class TTSBoard:
    def __init__(self, post, request=None):
        self.post = post
        self.request = request
    
    def get_color_diffuse(self):
        # Default to white if any component is missing
        r = self.post.color_r if self.post.color_r is not None else 255
        g = self.post.color_g if self.post.color_g is not None else 255
        b = self.post.color_b if self.post.color_b is not None else 255

        return {
            "r": r / 255,
            "g": g / 255,
            "b": b / 255
        }

    def to_dict(self):
        faction_board_snap_points = [
            sp.to_tts_dict()
            for sp in self.post.snap_points.all()
        ]
        all_snap_points = faction_board_snap_points + list(CRAFTED_ITEMS_SNAP_POINTS)

        front_image = tts_image_url(self.post.board_image, request=self.request)
        back_image = tts_image_url(self.post.board_2_image, request=self.request)

        return {
            "GUID": generate_tts_guid(),
            "Name": "Custom_Tile",
            "Transform": FACTION_BOARD_TRANSFORM,
            "Nickname": self.post.title or self.post.slug,
            "Description": f"{self.post.title} Faction Board",
            "GMNotes": "",
            "AltLookAngle": {"x": 0, "y": 0, "z": 0},
            "ColorDiffuse": self.get_color_diffuse(),
            "LayoutGroupSortIndex": 0,
            "Value": 0,
            "Locked": False,
            "Grid": True,
            "Snap": True,
            "IgnoreFoW": False,
            "MeasureMovement": False,
            "DragSelectable": True,
            "Autoraise": True,
            "Sticky": True,
            "Tooltip": True,
            "GridProjection": False,
            "HideWhenFaceDown": False,
            "Hands": False,
            "CustomImage": {
                "ImageURL": front_image,
                "ImageSecondaryURL": back_image if back_image else front_image,
                "ImageScalar": 1.0,
                "WidthScale": 0.0,
                "CustomTile": {
                    "Type": 0,
                    "Thickness": 0.2,
                    "Stackable": False,
                    "Stretch": True,
                },
            },
            "LuaScript": "",
            "LuaScriptState": "",
            "XmlUI": "",
            "AttachedSnapPoints": all_snap_points,
        }

LOCK_ON_REST_LUA = (
    "function onLoad()\n"
    "    Wait.time(function() self.setLock(true) end, 1.5)\n"
    "end\n"
)


class TTSBoardBase:
    NAME = "Custom_Tile"
    DEFAULT_TRANSFORM = FACTION_BOARD_TRANSFORM
    LUA_SCRIPT = ""

    def __init__(self, post, request=None):
        self.post = post
        self.request = request

    def get_color_diffuse(self):
        r = self.post.color_r if self.post.color_r is not None else 255
        g = self.post.color_g if self.post.color_g is not None else 255
        b = self.post.color_b if self.post.color_b is not None else 255

        return {
            "r": r / 255,
            "g": g / 255,
            "b": b / 255,
        }

    def get_front_image(self):
        return tts_image_url(self.post.board_image, request=self.request)

    def get_back_image(self):
        back = tts_image_url(self.post.board_2_image, request=self.request)
        return back or self.get_front_image()

    def get_snap_points(self):
        model_snap_points = [
            sp.to_tts_dict()
            for sp in self.post.snap_points.all()
        ]
        return model_snap_points + self.get_default_snap_points()

    def get_default_snap_points(self):
        """Override per board type"""
        return []

    def get_transform(self):
        """Override per board type"""
        return self.DEFAULT_TRANSFORM

    def get_description(self):
        return self.post.title or self.post.slug

    def get_nickname(self):
        return self.post.title or self.post.slug

    def to_dict(self):
        return {
            "GUID": generate_tts_guid(),
            "Name": self.NAME,
            "Transform": self.get_transform(),
            "Nickname": self.get_nickname(),
            "Description": self.get_description(),
            "GMNotes": "",
            "AltLookAngle": {"x": 0, "y": 0, "z": 0},
            "ColorDiffuse": self.get_color_diffuse(),
            "LayoutGroupSortIndex": 0,
            "Value": 0,
            "Locked": False,
            "Grid": True,
            "Snap": True,
            "IgnoreFoW": False,
            "MeasureMovement": False,
            "DragSelectable": True,
            "Autoraise": True,
            "Sticky": True,
            "Tooltip": True,
            "GridProjection": False,
            "HideWhenFaceDown": False,
            "Hands": False,
            "CustomImage": {
                "ImageURL": self.get_front_image(),
                "ImageSecondaryURL": self.get_back_image(),
                "ImageScalar": 1.0,
                "WidthScale": 0.0,
                "CustomTile": {
                    "Type": 0,
                    "Thickness": 0.2,
                    "Stackable": False,
                    "Stretch": True,
                },
            },
            "LuaScript": self.LUA_SCRIPT,
            "LuaScriptState": "",
            "XmlUI": "",
            "AttachedSnapPoints": self.get_snap_points(),
        }

class TTSFactionBoard(TTSBoardBase):
    DEFAULT_TRANSFORM = FACTION_BOARD_TRANSFORM

    def get_default_snap_points(self):
        return list(CRAFTED_ITEMS_SNAP_POINTS)

    def get_description(self):
        return f"{self.post.title} Faction Board"



class TTSDeckBase:
    DEFAULT_TRANSFORM = DEFAULT_CARD_TRANSFORM

    def __init__(self, deck_id, request=None):
        self.deck_id = deck_id
        self.request = request

    def card_id(self, index):
        return self.deck_id * 100 + index

    def get_transform(self):
        return dict(self.DEFAULT_TRANSFORM)

class TTSSpriteDeck(TTSDeckBase):
    def __init__(self, carddeck, deck_id, request=None):
        super().__init__(deck_id, request)
        self.carddeck = carddeck
        self.carddeck_name = carddeck.group.name

    def custom_deck(self):
        num_width = 6
        num_height = math.ceil(self.carddeck.card_count / num_width)
        return {
            str(self.deck_id): {
                "FaceURL": tts_image_url(generate_sprite_sheet(self.carddeck), request=self.request),
                "BackURL": tts_image_url(self.carddeck.group.back_image, request=self.request),
                "NumWidth": num_width,
                "NumHeight": num_height,
                "BackIsHidden": True,
                "UniqueBack": False,
                "Type": 0,
            }
        }

    def to_object(self):
        transform = self.get_transform()
        cards = []
        for i, card in enumerate(self.carddeck.cards_in_deck):
            card_name = getattr(card, "name", None) or ""
            card_tags = card.tag_string
            # Dominance is an identity tag — promote it into the Nickname
            # (so TTS shows "Bird Dominance", etc.) and drop the bare tag
            # token from the Description.
            raw_tags = getattr(card, "tags", None) or []
            if "Dominance" in raw_tags:
                if "dominance" not in card_name.lower():
                    card_name = f"{card_name} Dominance".strip()
                # tag_string puts tags as whitespace-separated tokens; drop
                # the "Dominance" token without touching surrounding text.
                card_tags = " ".join(
                    tok for tok in card_tags.split(" ") if tok != "Dominance"
                ).strip()
            cards.append({
                "GUID": generate_tts_guid(),
                "Name": "Card",
                "Transform": dict(transform),
                "Nickname": card_name,
                "Description": card_tags,
                "CardID": self.card_id(i),
                "CustomDeck": self.custom_deck(),
                "HideWhenFaceDown": True,
                "Hands": True,
            })

        # TTS requires a deck to contain at least 2 cards. A single-card
        # "deck" must be exported as a bare Card object instead, or the
        # prefab fails to load.
        if len(cards) == 1:
            only = dict(cards[0])
            # Inherit the deck-group's display name as the card's Nickname
            # if the card itself doesn't have one.
            if not only.get("Nickname") and self.carddeck_name:
                only["Nickname"] = self.carddeck_name
            return only

        return {
            "GUID": generate_tts_guid(),
            # "DeckCustom" is the TTS prefab type for image-backed decks.
            # The deck-group's display name belongs in Nickname; using it
            # as Name made TTS look up a prefab by that name and fail.
            "Name": "DeckCustom",
            "Nickname": self.carddeck_name or "",
            "Transform": transform,
            "DeckIDs": [self.card_id(i) for i in range(len(cards))],
            "ContainedObjects": cards,
            "CustomDeck": self.custom_deck(),
        }

class TTSSingleCardDeck(TTSDeckBase):
    def __init__(self, face_image, back_image, deck_id, request=None, card_name="Card", transform=None):
        super().__init__(deck_id, request)
        self.face_image = face_image
        self.back_image = back_image
        self.card_name = card_name
        self.transform_override = transform

    def get_transform(self):
        if self.transform_override is not None:
            return dict(self.transform_override)
        return dict(self.DEFAULT_TRANSFORM)

    def custom_deck(self):
        return {
            str(self.deck_id): {
                "FaceURL": tts_image_url(self.face_image, request=self.request),
                "BackURL": tts_image_url(self.back_image, request=self.request),
                "NumWidth": 1,
                "NumHeight": 1,
                "BackIsHidden": True,
                "UniqueBack": True,
                "Type": 0,
            }
        }

    def to_object(self):
        transform = self.get_transform()
        return {
            "GUID": generate_tts_guid(),
            "Name": "Card",
            "Transform": transform,
            "Nickname": self.card_name,
            "CardID": self.card_id(0),
            "CustomDeck": self.custom_deck(),
            "HideWhenFaceDown": True,
            "Hands": True,
        }
    

class TTSDeckGroup:
    """
    Builds one or more TTSSpriteDeck objects from a DeckGroup.
    """

    def __init__(self, group, request=None):
        self.group = group
        self.request = request

    def build(self, starting_deck_id):
        """
        Returns a list of TTSSpriteDecks.
        Deck IDs start at starting_deck_id and increment per deck.
        """
        # Ensure CardDeck objects exist (1 per 99 cards)
        card_count = len(self.group.ordered_cards)
        if card_count > 0 and not self.group.decks.exists():
            num_decks = (card_count - 1) // 99 + 1
            for i in range(num_decks):
                self.group.get_or_create_deck_for_card_index(i * 99)

        decks = []
        deck_id = starting_deck_id

        for carddeck in self.group.decks.all():
            decks.append(
                TTSSpriteDeck(
                    carddeck=carddeck,
                    deck_id=deck_id,
                    request=self.request,
                )
            )
            deck_id += 1

        return decks



# class TTSDeckGroup:
#     def __init__(self, deck_group, request=None, starting_deck_id=1):
#         self.deck_group = deck_group
#         self.request = request
#         self.starting_deck_id = starting_deck_id

#     def to_object_list(self):
#         objects = []
#         deck_id = self.starting_deck_id

#         for carddeck in self.deck_group.decks.all():
#             tts_deck = TTSCardDeck(
#                 carddeck=carddeck,
#                 deck_id=deck_id,
#                 request=self.request,
#             )
#             objects.append(tts_deck.to_dict())
#             deck_id += 1

#         return objects, deck_id






def wrap_tts_save(objects, save_name=""):
    """
    Wrap a list of TTS object dicts into a minimal TTS save file JSON.
    
    objects: list of dicts, each a TTS object (like your player board)
    save_name: string, optional name for the save
    """
    save_file = {
        "SaveName": save_name,     
        "Date": "",                
        "VersionNumber": "",     
        "GameMode": "",
        "GameType": "",
        "GameComplexity": "",
        "Tags": [],
        "Gravity": 0.5,           
        "PlayArea": 0.5,          
        "Table": "",
        "Sky": "",
        "Note": "",
        "TabStates": {},
        "LuaScript": "",
        "LuaScriptState": "",
        "XmlUI": "",
        "ObjectStates": objects 
    }
    return save_file



# ----------------------
# Sprite sheet generation
# ----------------------

def carddeck_hash(deck: CardDeck):
    """
    Generate a hash representing the current state of a CardDeck.
    Includes card IDs and front image file modification timestamps.
    """
    cards = deck.cards_in_deck
    hash_input = []

    for card in cards:
        image_path = card.front_image.path
        timestamp = os.path.getmtime(image_path) if os.path.exists(image_path) else 0
        hash_input.append(f"{card.id}-{timestamp}")

    combined = "|".join(hash_input)
    return hashlib.md5(combined.encode("utf-8")).hexdigest()

# def generate_sprite_sheet(deck: CardDeck):
#     """
#     Combine up to 99 card front images into a single TTS-compatible sprite sheet.
#     Only regenerates if the deck has changed.
#     """
#     cards = deck.cards_in_deck
#     if not cards:
#         return None

#     # Check if sprite sheet is up-to-date
#     current_hash = carddeck_hash(deck)
#     if deck.sprite_hash == current_hash and deck.sprite_sheet:
#         return deck.sprite_sheet.url  # up-to-date

#     num_cards = len(cards)
#     num_width = 6  # TTS standard
#     num_height = math.ceil(num_cards / num_width)

#     # Use first card to determine size
#     first_img = Image.open(cards[0].front_image.path)
#     card_w, card_h = first_img.size

#     sheet_w = card_w * num_width
#     sheet_h = card_h * num_height

#     sprite = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

#     for idx, card in enumerate(cards):
#         img = Image.open(card.front_image.path).convert("RGBA")
#         img = img.resize((card_w, card_h), Image.LANCZOS)
#         x = (idx % num_width) * card_w
#         y = (idx // num_width) * card_h
#         sprite.paste(img, (x, y))

#     # Use the custom upload path function
#     relative_path = deck_sheet_upload_path(deck, "sheet.webp")
#     full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
#     os.makedirs(os.path.dirname(full_path), exist_ok=True)
#     sprite.save(full_path, format="PNG")

#     # Update deck
#     deck.sprite_sheet.name = relative_path
#     deck.sprite_hash = current_hash
#     deck.save(update_fields=["sprite_sheet", "sprite_hash"])

#     return deck.sprite_sheet.url

CARD_MAX_DIM = 800
MAX_SHEET_PIXELS = 100_000_000


def generate_sprite_sheet(deck: CardDeck, quality=90):
    """
    Combine up to 99 card front images into a single TTS-compatible sprite sheet.
    Only regenerates if the deck has changed.

    Args:
        deck: CardDeck instance
        quality: WebP quality (0-100, default 90 for high quality sprite sheets)
    """
    cards = deck.cards_in_deck
    if not cards:
        return None

    # Check if sprite sheet is up-to-date
    current_hash = carddeck_hash(deck)
    if deck.sprite_hash == current_hash and deck.sprite_sheet:
        return deck.sprite_sheet.url  # up-to-date

    num_cards = len(cards)
    num_width = 6  # TTS standard
    num_height = math.ceil(num_cards / num_width)

    # Drop quality for big decks — TTS scales them down on-screen anyway.
    if num_cards > 30:
        quality = min(quality, 75)

    # Use first card to determine size, then clamp to a safe ceiling so a card
    # that slipped through the per-field resize doesn't blow up the sheet.
    with Image.open(cards[0].front_image.path) as first_img:
        card_w, card_h = first_img.size
    if card_w > CARD_MAX_DIM or card_h > CARD_MAX_DIM:
        ratio = CARD_MAX_DIM / max(card_w, card_h)
        card_w, card_h = int(card_w * ratio), int(card_h * ratio)

    sheet_w = card_w * num_width
    sheet_h = card_h * num_height

    # Hard bail-out before allocating the canvas. Without this an oversized
    # deck can OOM the worker via Image.new.
    if sheet_w * sheet_h > MAX_SHEET_PIXELS:
        raise ValueError(
            f"Sprite sheet would be {sheet_w}x{sheet_h} px "
            f"({sheet_w * sheet_h:,} pixels) — exceeds {MAX_SHEET_PIXELS:,} pixel limit"
        )

    sprite = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

    for idx, card in enumerate(cards):
        with Image.open(card.front_image.path) as img:
            img = img.convert("RGBA")
            img = img.resize((card_w, card_h), Image.LANCZOS)
            x = (idx % num_width) * card_w
            y = (idx // num_width) * card_h
            sprite.paste(img, (x, y))
    
    # Delegate the path to the field's upload_to so subclasses/parallel models
    # (e.g. the_forge's ForgedCardDeck) can route to their own location.
    relative_path = deck.sprite_sheet.field.generate_filename(deck, "sheet.webp")
    full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    # Delete old sprite sheet if it exists
    if deck.sprite_sheet:
        old_path = deck.sprite_sheet.path
        if os.path.exists(old_path):
            os.remove(old_path)
    
    # Save as WebP with high quality
    sprite.save(full_path, format="WEBP", quality=quality, method=6)
    
    # Update deck
    deck.sprite_sheet.name = relative_path
    deck.sprite_hash = current_hash
    deck.save(update_fields=["sprite_sheet", "sprite_hash"])
    
    return deck.sprite_sheet.url