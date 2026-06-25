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
from the_gatehouse.models import Language


def _lookup_command(name, label):
    """A /<name> command with a required, autocompleting 'name' option."""
    return {
        "name": name,
        "description": f"Look up a Root {label} by name",
        "options": [
            {
                "name": "name",
                "description": f"{label.capitalize()} name to search",
                "type": 3,  # STRING
                "required": True,
                "autocomplete": True,
            }
        ],
    }


STATS_COMMAND = {
    "name": "stats",
    "description": "Win rate filtered by player, faction, series, and/or platform",
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
    ],
}


HELP_COMMAND = {
    "name": "help",
    "description": "List the bot's available commands",
    "options": [],
}


def _law_command():
    """The /law command. Its `language` choices are read from the DB (only
    languages that actually have public laws), so this is built at run time."""
    languages = (
        Language.objects.filter(law__group__public=True)
        .distinct().order_by("id").values_list("name", "code")
    )
    lang_choices = [{"name": name, "value": code} for name, code in languages]
    return {
        "name": "law",
        "description": "Look up a Root law by code, title, post, or text",
        "options": [
            {"name": "code", "description": "Law code (e.g. MdC.1.a)", "type": 3, "required": False, "autocomplete": True},
            {"name": "title", "description": "Law title", "type": 3, "required": False, "autocomplete": True},
            {"name": "post", "description": "Faction / component the law belongs to", "type": 3, "required": False, "autocomplete": True},
            {"name": "text", "description": "Text to search within the law", "type": 3, "required": False},
            {"name": "language", "description": "Language (defaults to English)", "type": 3, "required": False, "choices": lang_choices},
        ],
    }


# Static command definitions. The /law command is DB-dependent and appended at
# run time by all_command_definitions().
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
]


# Ordered grouping for the /help listing. Each command name should appear in
# exactly one group; any command missing from here falls into a trailing "Other"
# group (see grouped_commands) so a new command is never silently dropped.
COMMAND_GROUPS = [
    ("General", ["help"]),
    ("Lookups", ["faction", "clockwork", "map", "deck", "vagabond",
                 "captain", "landmark", "hireling", "houserule"]),
    ("Stats", ["stats"]),
    ("Law", ["law"]),
]


def all_command_definitions():
    """Every command definition, including the run-time /law command."""
    return COMMANDS + [_law_command()]


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
