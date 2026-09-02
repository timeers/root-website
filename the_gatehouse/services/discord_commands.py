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
import copy
import logging

from the_keep.models import CardTag

logger = logging.getLogger(__name__)


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


CARD_COMMAND = {
    "name": "card",
    "description": "Look up an individual card by name, source, or suit",
    "options": [
        {"name": "name", "description": "Card name to search",
         "type": 3, "required": True, "autocomplete": True},
        {"name": "from", "description": "Post the card is from",
         "type": 3, "required": False, "autocomplete": True},
        {"name": "tag", "description": "Card suit / tag",
         "type": 3, "required": False,
         "choices": [{"name": label, "value": value} for value, label in CardTag.choices]},
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


SCHEDULE_COMMAND = {
    "name": "schedule",
    "description": "Suggest or clear the scheduled time for this thread's match",
    "options": [
        # Optional so that omitting it means "clear the current time" (the handler
        # asks for confirmation first, and errors when there's nothing to clear).
        {"name": "time",
         "description": 'e.g. "4pm", "tomorrow 4pm", "Mar 15 8pm", or a <t:...> paste — leave empty to clear',
         "type": 3, "required": False},
        # Rarely needed: the handler asks for a timezone with a region/city picker
        # when it doesn't have one. This option stays because that picker is a
        # curated ~76 zones, and it's the only way to reach any of the others.
        {"name": "timezone",
         "description": "Override your saved timezone (otherwise I'll just ask)",
         "type": 3, "required": False, "autocomplete": True},
    ],
}


RECORD_COMMAND = {
    "name": "record",
    "description": "Get a link to record this game's result",
    # No options: the mode is resolved from the channel the command is used in
    # (LFG thread -> lfg_mode, scheduled match thread -> match_mode, else
    # standalone), the same way /schedule finds its match.
    "options": [],
}


# /help categories. Values double as the dispatch key in the /help handler.
HELP_CATEGORY_COMMANDS = "commands"
HELP_CATEGORY_LFG = "lfg"

# Two /help variants, both registered as `/help`, but only one PUT per guild depending
# on whether /lfg is in its whitelist (see help_command_for_guild):
#   * BASE — no options, the long-standing behaviour.
#   * LFG  — an OPTIONAL `category` dropdown. Discord has no way to preselect a choice,
#            so "Commands" is listed FIRST to put it at the top of the dropdown, and the
#            option stays optional so a bare /help still returns the command list.
HELP_COMMAND_BASE = {
    "name": "help",
    "description": "List the bot's available commands",
    "options": [],
}

HELP_COMMAND_LFG = {
    "name": "help",
    "description": "List the bot's available commands or request more info",
    "options": [
        {"name": "category", "description": "What to show (defaults to the command list)",
         "type": 3, "required": False,
         "choices": [
             {"name": "Commands", "value": HELP_CATEGORY_COMMANDS},
             {"name": "LFG", "value": HELP_CATEGORY_LFG},
         ]},
    ],
}

# The base COMMANDS entry (registration template + /help listing) is the BASE variant;
# the per-guild LFG swap happens at registration time in register_guild_commands.
HELP_COMMAND = HELP_COMMAND_BASE


def help_command_for_guild(enabled_names):
    """The /help definition to register for a guild: the LFG variant (with the `category`
    dropdown) when /lfg is enabled there, otherwise the option-less base. Deep-copies the
    shared module dicts so the caller never mutates a singleton."""
    # "lfg" here is the COMMAND NAME, not HELP_CATEGORY_LFG. The two constants happen to
    # share a value but mean different things, and keying off the category constant would
    # be a latent bug the day either one changes.
    if "lfg" in set(enabled_names or ()):
        return copy.deepcopy(HELP_COMMAND_LFG)
    return copy.deepcopy(HELP_COMMAND_BASE)


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
    "description": "Build a faction draft for a game, banning any you want to omit",
    "options": [
        {"name": "players", "description": "Number of players (defaults to the game thread's players, else 4)",
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

# The seating half of /draft on its own, for groups who pick factions some other
# way. No options: the roster comes from the thread it's used in, the same way
# /record resolves its mode from the channel — an LFG game thread (saved) or a
# tournament player group's thread (displayed only).
SEATING_COMMAND = {
    "name": "seating",
    "description": "Randomly seat the players in this thread's game",
    "options": [],
}

# The step after /draft and /seating: who takes which faction. No options -- the
# seating, the roster and the faction pool all come from the thread it's used in.
PICK_COMMAND = {
    "name": "pick",
    "description": "Pick factions in seat order, or assign factions",
    "options": [],
}

# /seating + /draft + /pick as ONE message that is edited in place through every
# phase. No options at all: the roster comes from the thread, and the platform is
# always Tabletop Simulator (the ban/draft/pick flow has no Root Digital case).
#
# The description is deliberately broad about WHAT (it also gathers players and
# runs the picks, and naming every step would be long and still incomplete) but
# explicit about WHERE: Discord lists the command everywhere, while the handler
# refuses outside an LFG game thread or a player group's thread, so the picker is
# the only place to set that expectation before someone hits the refusal.
ADSET_COMMAND = {
    "name": "adset",
    "description": "Seat players and draft factions within a thread",
    "options": [],
}

# Free text, no autocomplete — the title is whatever the host wants to call the
# game. Only the host of the /lfg that made the thread may use it.
RENAME_COMMAND = {
    "name": "rename",
    "description": "Rename this game's thread",
    "options": [
        {"name": "title", "description": "The new thread title",
         "type": 3, "required": True},
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


# Discord caps a string option at 25 choices; the site enforces the same cap on the
# number of LFG tags a guild can create so the /lfg dropdown can list them all.
LFG_TAG_LIMIT = 25

# Two /lfg variants, both registered as `/lfg` but only one PUT per guild depending on
# its LFG-tag count (see lfg_command_for_roles):
#   * SINGLE — no `type` option; used for 0 or 1 tags (0 → plain post, 1 → sole tag used).
#   * MULTI  — a required `type` dropdown of the guild's tags; used for 2+ tags.
# `description` is optional in both.
LFG_COMMAND_SINGLE = {
    "name": "lfg",
    "description": "Post a Looking For Game message others can join",
    "options": [
        {"name": "description", "description": "What kind of game you're looking for",
         "type": 3, "required": False},
    ],
}

LFG_COMMAND_MULTI = {
    "name": "lfg",
    "description": "Post a Looking For Game message others can join",
    "options": [
        {"name": "type", "description": "Which LFG tag to ping",
         "type": 3, "required": True, "choices": []},
        {"name": "description", "description": "What kind of game you're looking for",
         "type": 3, "required": False},
    ],
}

# The base COMMANDS entry (registration template + /help listing) is the SINGLE variant;
# the per-guild MULTI swap happens at registration time in register_guild_commands.
LFG_COMMAND = LFG_COMMAND_SINGLE


def lfg_command_for_roles(roles):
    """The /lfg definition to register for a guild given its LFG roles: SINGLE (no
    `type` option) for 0–1 roles, MULTI (required `type` dropdown) for 2+. Deep-copies
    the shared module dict so the caller never mutates the singleton."""
    if len(roles) < 2:
        return copy.deepcopy(LFG_COMMAND_SINGLE)
    cmd = copy.deepcopy(LFG_COMMAND_MULTI)
    if len(roles) > LFG_TAG_LIMIT:
        logger.warning(
            "Guild has %d LFG roles; /lfg dropdown truncated to Discord's %d-choice limit.",
            len(roles), LFG_TAG_LIMIT,
        )
    type_opt = next(o for o in cmd["options"] if o["name"] == "type")
    type_opt["choices"] = [
        {"name": r.name[:100], "value": str(r.pk)} for r in roles[:LFG_TAG_LIMIT]
    ]
    return cmd


# The LFG walkthrough, rendered in two places: the Databot page's "How to Use LFG" card
# and /help category:LFG. Edit the copy here and both update.
#
# Bodies carry three bits of inline markup, all of them valid Discord markdown so the
# embed sends them as-is; the `lfg_body` template filter (the_gatehouse/templatetags/
# databot_filters.py) converts the same three to HTML:
#   `/cmd`              -> inline code chip
#   *text*              -> italics
#   [label](url-name)   -> link, addressed by Django URL NAME so neither renderer ever
#                          hardcodes a path (the embed reverses it against SITE_URL).
#
# Steps may also carry "requires": a list of command names the step is about. /help
# filters the walkthrough to what a guild has actually enabled (see
# lfg_help_steps_for_guild): a step is shown only when EVERY name in its "requires" is
# enabled, and a step's `commands` chips are filtered to enabled ones -- a step that had
# chips but has none left is dropped whole, since its body only introduces them. Steps
# with neither key are unconditional. The public Databot page passes no whitelist and so
# still renders every step.
#
# "setup_only" marks a step as server configuration done on the web (the Manage your
# Guilds page) rather than something you do from Discord. build_lfg_help_embed drops
# those, since someone running /help category:LFG is asking how to USE lfg and usually
# isn't the person who can configure it; the Databot page renders them, which is where
# the setup instructions belong.
LFG_HELP_INTRO = (
    "`/lfg` posts a looking-for-game message in your server, pings the players who want "
    "to play, and gives the game its own thread. Everything rolled or looked up in that "
    "thread is remembered, so recording the result afterwards is simplified."
)

LFG_HELP_STEPS = [
    {
        "title": "Set up your LFG Roles",
        "setup_only": True,
        "body": "Add one or more LFG roles for your server on the "
                "[Manage your Guilds](manage-guilds) page (for example *Root TTS LFG* "
                "and *Root Digital LFG*). Each role mentions a Discord role, so starting "
                "a game pings only the people who want to play. A role can also be tied "
                "to a series, which lets its games record into that series by default.",
    },
    {
        "title": "Choose where the Thread goes",
        "setup_only": True,
        "body": "By default the game thread appears under the LFG message itself. If "
                "you'd rather keep game threads in one place, give the role a forum "
                "channel and each new game is created as a post there instead (you can "
                "give the thread a tag as well).",
    },
    {
        "title": "Find Players for your Game",
        "body": "Use `/lfg` to ping the players who want to play Root. "
                "If your server has multiple LFG roles you can specify one in the command. "
                "Give your LFG a description to specify the type of game you want to play. "
                "Other players can click join to add themselves to the roster or click notify "
                "be alerted when another player joins. Only the host can cancel or start the game."
        ,
    },
    {
        "title": "Optional in-thread Commands",
        # The trailing colon introduces the `commands` chips below, so both renderers
        # emit them immediately after the body. Drop the colon if the chips ever go.
        "body": "Once the game is started a thread will automatically be created and the players will "
        "be notified. Within the thread the players can use certain commands to help set up the game. "
        "All of these commands are optional, but can be helpful when recording the game. The commands are as follows:",
        # LFG-specific blurbs: deliberately worded for what the command does *inside a
        # game thread*, which differs from its general registration description.
        "commands": [
            ("random",  "Roll a random map, deck, faction, etc."),
            ("map",     "Specify map you're playing on."),
            ("deck",    "Specify deck you're playing with."),
            ("faction", "Note a faction that's in the game."),
            ("seating", "Randomly seat the players without drafting factions."),
            ("draft",   "Draft the factions that can be selected in this game."),
            ("pick",    "Have each player pick factions from the draft or assign "
                        "factions to each player."),
        ],
    },
    {
        "title": "Record the Result",
        # Both closing steps are about /record, so a guild without it sees neither
        # rather than being told to run a command Discord won't offer them.
        "requires": ["record"],
        "body": "When the game is over, run `/record` in the thread. You'll get a link "
                "to the game form with the players, seating, map, deck, and series "
                "already filled in from everything the thread captured.",
    },
    {
        "title": "Results Post to the Thread",
        "requires": ["record"],
        "body": "Once the game is saved, a link to the finished game is posted back in "
                "the thread, so everyone who played can see the result without leaving "
                "Discord.",
    },
]


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
    CARD_COMMAND,
    STATS_COMMAND,
    UPCOMING_COMMAND,
    SCHEDULE_COMMAND,
    RECORD_COMMAND,
    LAW_COMMAND,
    DRAFT_COMMAND,
    SEATING_COMMAND,
    PICK_COMMAND,
    ADSET_COMMAND,
    RENAME_COMMAND,
    RANDOM_COMMAND,
    LFG_COMMAND,
]


# Ordered grouping for the /help listing. Each command name should appear in
# exactly one group; any command missing from here falls into a trailing "Other"
# group (see grouped_commands) so a new command is never silently dropped.
COMMAND_GROUPS = [
    ("General", ["help"]),
    ("Lookups", ["law", "faction", "clockwork", "map", "deck", "vagabond",
                 "captain", "landmark", "hireling", "houserule", "card"]),
    ("Stats", ["stats", "upcoming"]),
    ("Games", ["lfg", "adset", "seating", "pick", "schedule", "record", "rename"]),
    ("Random", ["draft", "random"]),
]


def all_command_definitions():
    """Every command definition."""
    return list(COMMANDS)


# /help is always available in every guild and is never a whitelist toggle, so the
# whitelistable set is every other command. Derived from COMMANDS so a new command
# becomes toggleable automatically.
WHITELISTABLE = [c["name"] for c in COMMANDS if c["name"] != "help"]


def whitelistable_commands():
    """(name, description) for every command a guild moderator can toggle (all but /help)."""
    return [(c["name"], c["description"]) for c in COMMANDS if c["name"] != "help"]


def commands_for_guild(enabled_names):
    """Definitions to register for a guild: always /help, plus each enabled, whitelistable
    command. Ignores unknown/removed names so a stale whitelist never breaks registration."""
    allowed = set(enabled_names) & set(WHITELISTABLE)
    return [c for c in COMMANDS if c["name"] == "help" or c["name"] in allowed]


def lfg_help_steps_for_guild(enabled_names=None):
    """LFG_HELP_STEPS reduced to what this guild can actually do.

    enabled_names=None (the default -- the public Databot page, or a DM where there's no
    whitelist to consult) returns every step unfiltered. Otherwise a step is kept only
    when every name in its "requires" is enabled, and its `commands` chips are filtered
    to enabled ones; a step that had chips but has none left is dropped, since its body
    only introduces them.

    Steps are copied before their chips are filtered, so the module-level LFG_HELP_STEPS
    is never mutated (the same singleton-safety rule help_command_for_guild follows).
    Renumbering is the caller's enumerate, so dropped steps close the gap automatically.
    """
    if enabled_names is None:
        return list(LFG_HELP_STEPS)

    enabled = set(enabled_names)
    kept = []
    for step in LFG_HELP_STEPS:
        if not all(name in enabled for name in step.get("requires", ())):
            continue
        chips = step.get("commands")
        if chips:
            chips = [(name, blurb) for name, blurb in chips if name in enabled]
            if not chips:
                continue
            step = {**step, "commands": chips}
        kept.append(step)
    return kept


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
