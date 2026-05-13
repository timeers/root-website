"""Caps on per-faction child collections and free-text fields.

Every limit is enforced in views.py (count caps) or forms.py (length caps).
Existing rows over-cap continue to render — caps only block new submissions.
"""

# Per-FactionSheet collections
MAX_FACTION_ABILITIES = 5
MAX_CONTENT_SECTIONS = 15
MAX_CHARACTER_IMAGES = 5
MAX_CUSTOM_INLINE_IMAGES = 10
MAX_CARD_PILES = 5
MAX_CARD_SLOTS = 5

# PhaseStep collections
MAX_PHASE_STEPS_PER_PHASE = 10
MAX_PHASE_STEPS_PER_BOX = 10
MAX_STEP_ACTIONS = 10
MAX_STEP_CHILDREN = 6

# Legend / Scale rows
MAX_LEGEND_ROWS = 12
MAX_SCALE_ROWS = 10

# CardboardTrack grid
MAX_TRACK_ROWS = 12
MAX_TRACK_COLS = 12

# FactionBack / SetupCard
MAX_BACK_SETUP_STEPS = 10
MAX_CARD_SETUP_STEPS = 10
MAX_PIECES_PER_TYPE = 10
MAX_PIECE_QUANTITY = 30

# Free-text caps (raw character count, includes HTML markup from rich-text editor)
MAX_FLAVOR_TEXT = 500
MAX_ABILITY_TITLE = 100
MAX_ABILITY_BODY = 1600
MAX_CONTENT_TEXT = 1600
MAX_PHASE_STEP_TEXT = 1000
MAX_STEP_ACTION_TEXT = 1000
MAX_CARD_PILE_BODY = 1000
MAX_BORDERED_BOX_BODY = 1600
MAX_TRACK_BODY = 1000
MAX_LEGEND_ROW_BODY = 1000
MAX_LEGEND_BODY = 1000
MAX_SETUP_STEP_TEXT = 500
MAX_HOW_TO_PLAY_TITLE = 50
MAX_HOW_TO_PLAY_TEXT = 2000
