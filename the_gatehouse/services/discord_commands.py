"""
Shared slash-command definitions for the Discord bot.

This is the single source of truth for what commands exist and how they look in
Discord. Two places consume it:

  * the `register_discord_commands` management command, which PUTs these
    definitions to Discord, and
  * the `/help` handler (build_help_embed in discordservice), which lists them.

Add a new command by defining it here and adding it to COMMANDS (and, if it has
behaviour, a handler in discord_interactions.py). Keeping definitions here means
`/help` picks the command up automatically.
"""


def _lookup_command(name, label):
    """A /<name> command with a required, autocompleting 'name' option. Replies
    with one embed (info card + large image)."""
    return {
        "name": name,
        "description": f"Look up a Root {label}",
        "options": [
            {
                "name": "name",
                "description": f"{label.capitalize()} name to search",
                "type": 3,  # STRING
                "required": True,
                "autocomplete": True,
            },
        ],
    }


STATS_COMMAND = {
    "name": "stats",
    "description": "Win rate and leaderboard filtered by player, faction, series, and/or platform",
    "options": [
        {"name": "player", "description": "Player", "type": 3, "required": False, "autocomplete": True},
        {"name": "faction", "description": "Faction", "type": 3, "required": False, "autocomplete": True},
        {"name": "series", "description": "Series / tournament", "type": 3, "required": False, "autocomplete": True},
        {
            "name": "platform",
            "description": "Platform",
            "type": 3,  # STRING
            "required": False,
            "choices": [
                {"name": "Tabletop Simulator", "value": "Tabletop Simulator"},
                {"name": "Root Digital", "value": "Root Digital"},
                {"name": "In Person", "value": "In Person"},
            ],
        },
        # Optional Yes/No; when omitted it reads as unset and fan content stays
        # hidden, so leaving it out behaves like "No".
        {
            "name": "include_fan_content",
            "description": "Include fan-made factions (default: No)",
            "type": 5,  # BOOLEAN (Yes/No)
            "required": False,
        },
    ],
}


UPCOMING_COMMAND = {
    "name": "upcoming",
    "description": "Show the next scheduled match for a player or event",
    "options": [
        {"name": "series", "description": "Filter to a series / tournament", "type": 3, "required": False, "autocomplete": True},
        {"name": "player", "description": "Filter to a player", "type": 3, "required": False, "autocomplete": True},
    ],
}


HELP_COMMAND = {
    "name": "help",
    "description": "List the bot's available commands",
    "options": [],
}


LAW_COMMAND = {
    "name": "law",
    "description": "Look up a Root law by code/title, post, or text",
    "options": [
        {"name": "law", "description": "Law code or title", "type": 3, "required": False, "autocomplete": True},
        {"name": "text", "description": "Text to search within the law", "type": 3, "required": False},
        {"name": "post", "description": "Faction / component the law belongs to", "type": 3, "required": False, "autocomplete": True},
    ],
}


# Platform values for /draft, shared with the handlers in discord_interactions.py
# so the value strings (which also match the site's platform labels) have a single
# source of truth.
DRAFT_PLATFORM_TTS = "Tabletop Simulator"
DRAFT_PLATFORM_RD = "Root Digital"

DRAFT_COMMAND = {
    "name": "draft",
    "description": "Build a factions draft for a game, banning any you want to omit",
    "options": [
        {"name": "players", "description": "Number of players (default 4)",
         "type": 4, "required": False,
         "choices": [{"name": str(n), "value": n} for n in range(2, 7)]},
        {"name": "platform", "description": "Platform (default Tabletop Simulator)",
         "type": 3, "required": False,
         "choices": [
             {"name": DRAFT_PLATFORM_TTS, "value": DRAFT_PLATFORM_TTS},
             {"name": DRAFT_PLATFORM_RD, "value": DRAFT_PLATFORM_RD},
         ]},
    ],
}


# /random kinds. Value strings double as the dispatch key and the label shown in
# "Random <Kind>:". Keep in sync with the handler in discord_interactions.py.
RANDOM_KINDS = [
    "Map", "Faction", "Clockwork", "Deck", "Vagabond", "Captain", "Hireling", "Landmark",
    "Roll", "Suit", "Clearing",
]

RANDOM_COMMAND = {
    "name": "random",
    "description": "Roll for a random selection (component, dice or suit/clearing)",
    "options": [
        {"name": "kind", "description": "What to randomize", "type": 3, "required": True,
         "choices": [{"name": k, "value": k} for k in RANDOM_KINDS]},
    ],
}


# All command definitions registered with Discord.
COMMANDS = [
    HELP_COMMAND,
    _lookup_command("faction", "faction"),
    _lookup_command("clockwork", "clockwork faction"),
    _lookup_command("map", "map"),
    _lookup_command("deck", "deck"),
    _lookup_command("vagabond", "vagabond"),
    _lookup_command("captain", "knave captain"),
    _lookup_command("landmark", "landmark"),
    _lookup_command("hireling", "hireling"),
    _lookup_command("houserule", "house rule"),
    STATS_COMMAND,
    UPCOMING_COMMAND,
    LAW_COMMAND,
    DRAFT_COMMAND,
    RANDOM_COMMAND,
]


# Ordered grouping for the /help listing. Each command name should appear in
# exactly one group; any command missing from here falls into a trailing "Other"
# group (see grouped_commands) so a new command is never silently dropped.
COMMAND_GROUPS = [
    ("General", ["help"]),
    ("Lookups", ["law", "faction", "clockwork", "map", "deck", "vagabond",
                 "captain", "landmark", "hireling", "houserule"]),
    ("Stats", ["stats"]),
    ("Tournaments", ["upcoming"]),
    ("Random", ["draft", "random"]),
]


def all_command_definitions():
    """Every command definition."""
    return list(COMMANDS)


def grouped_commands():
    """Yield (group_name, [(command_name, description), ...]) in display order.

    Commands not listed in COMMAND_GROUPS are collected into a final "Other"
    group so /help always reflects the full registered command set.
    """
    definitions = all_command_definitions()
    descriptions = {c["name"]: c.get("description", "") for c in definitions}

    grouped_names = set()
    for group_name, names in COMMAND_GROUPS:
        rows = [(n, descriptions[n]) for n in names if n in descriptions]
        grouped_names.update(n for n, _ in rows)
        if rows:
            yield group_name, rows

    leftover = [(c["name"], descriptions[c["name"]])
                for c in definitions if c["name"] not in grouped_names]
    if leftover:
        yield "Other", leftover
