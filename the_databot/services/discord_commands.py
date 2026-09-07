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

The nine component lookups are SUBCOMMANDS of one /lookup command (see
LOOKUP_SUBCOMMANDS) rather than nine top-level commands, so they take one slot in
Discord's command picker. Each subcommand is still whitelisted individually --
Discord only registers commands, so a guild's choices are honoured by varying
/lookup's options (lookup_command_for_guild).
"""
import copy
import logging

from the_keep.models import CardTag

logger = logging.getLogger(__name__)


LOOKUP_COMMAND_NAME = "lookup"


def _lookup_subcommand(name, label):
    """A `/lookup <name>` SUB_COMMAND with a required, autocompleting 'name' option.
    Replies with one embed (info card + large image)."""
    return {
        "name": name,
        "description": f"Look up a Root {label}",
        "type": 1,  # SUB_COMMAND
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


# The nine lookups, collapsed under one /lookup command so they occupy a single slot
# in Discord's command picker instead of nine. Order drives the /lookup options list,
# the guild edit page's checkbox order and the "Lookups" /help group.
#
# These names are the WHITELIST KEYS, deliberately unchanged from the old top-level
# command names (/faction, /map, ...), so an existing guild's enabled_commands stays
# valid verbatim and needs no data migration.
LOOKUP_SUBCOMMANDS = [_lookup_subcommand(n, l) for n, l in (
    ("faction", "faction"),
    ("clockwork", "clockwork faction"),
    ("map", "map"),
    ("deck", "deck"),
    ("vagabond", "vagabond"),
    ("captain", "knave captain"),
    ("landmark", "landmark"),
    ("hireling", "hireling"),
    ("houserule", "house rule"),
)]

LOOKUP_SUBCOMMAND_NAMES = [s["name"] for s in LOOKUP_SUBCOMMANDS]

# The base COMMANDS entry (registration template + the full /help listing) carries every
# subcommand; the per-guild subset is built by lookup_command_for_guild.
LOOKUP_COMMAND = {
    "name": LOOKUP_COMMAND_NAME,
    "description": "Look up a Root component by name",
    "options": LOOKUP_SUBCOMMANDS,
}


def parent_command_for_guild(parent_name, enabled_names):
    """The definition of a parent command to register for a guild: one SUB_COMMAND per
    enabled subcommand. Returns None when the guild has none enabled, so the parent
    isn't registered there at all.

    Discord registers COMMANDS, not subcommands, so a guild's per-subcommand whitelist
    can only be honoured by varying the parent's options -- the same trick
    lfg_command_for_roles uses to bake per-guild tag choices. Deep-copies the shared
    module dicts so the caller never mutates a singleton. Both parents are well under
    Discord's 25-option cap, so no truncation is needed."""
    parent, subcommands = PARENT_COMMANDS[parent_name]
    enabled = set(enabled_names or ())
    subs = [copy.deepcopy(s) for s in subcommands if s["name"] in enabled]
    if not subs:
        return None
    cmd = copy.deepcopy(parent)
    cmd["options"] = subs
    return cmd


def lookup_command_for_guild(enabled_names):
    """The /lookup definition for a guild. Thin alias kept for existing callers."""
    return parent_command_for_guild(LOOKUP_COMMAND_NAME, enabled_names)


LINK_COMMAND_NAME = "link"

# Account linking, built as a parent + subcommands like /lookup so future providers
# (/link dwd, ...) cost a subcommand rather than another top-level slot.
LINK_SUBCOMMANDS = [
    {
        "name": "steam",
        "description": "Link your Steam account to your Root Database profile",
        "type": 1,  # SUB_COMMAND
    },
]

LINK_SUBCOMMAND_NAMES = [s["name"] for s in LINK_SUBCOMMANDS]

LINK_COMMAND = {
    "name": LINK_COMMAND_NAME,
    "description": "Link an external account to your profile",
    "options": LINK_SUBCOMMANDS,
}


# Every parent command whose SUBCOMMANDS -- not the parent itself -- are the whitelist
# toggles. Registering this here means the five whitelist functions below stay generic:
# adding a third parent is one entry, not five new branches.
PARENT_COMMANDS = {
    LOOKUP_COMMAND_NAME: (LOOKUP_COMMAND, LOOKUP_SUBCOMMANDS),
    LINK_COMMAND_NAME: (LINK_COMMAND, LINK_SUBCOMMANDS),
}

# Flat list of every subcommand name across all parents -- these are the whitelist keys.
PARENT_SUBCOMMAND_NAMES = [s["name"]
                           for _parent, subs in PARENT_COMMANDS.values()
                           for s in subs]

# subcommand name -> its parent, so a display that collapses parents can map a
# COMMAND_GROUPS entry ("faction") onto the row it should render ("lookup").
_PARENT_OF = {s["name"]: parent_name
              for parent_name, (_parent, subs) in PARENT_COMMANDS.items()
              for s in subs}


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


# /schedule took subcommands so that clearing a time is something you can FIND.
# It used to be spelled "run /schedule with no time", which nothing advertised.
# Discord does not let one command be both a plain command and a parent, so the
# old bare `/schedule <time>` is now `/schedule set <time>` -- worth a changelog
# line, since regulars have muscle memory for the old form.
SCHEDULE_SUBCOMMANDS = [
    {
        "name": "set",
        "description": "Set the scheduled time for this thread's match",
        "type": 1,  # SUB_COMMAND
        "options": [
            # Required now. It was optional only to carry the "no time means
            # clear" trick, which `clear` replaces.
            {"name": "time",
             "description": 'e.g. "4pm", "tomorrow 4pm", "Mar 15 8pm", or a <t:...> paste',
             "type": 3, "required": True},
            # Rarely needed: the handler asks for a timezone with a region/city
            # picker when it doesn't have one. This option stays because that
            # picker is a curated ~76 zones, and it's the only way to reach any
            # of the others.
            #
            # NOTE its autocomplete is keyed ("schedule set", "timezone") -- the
            # dispatcher builds that key as "<parent> <sub>", so the bare
            # "schedule" key would silently return no choices.
            {"name": "timezone",
             "description": "Override your saved timezone (otherwise I'll just ask)",
             "type": 3, "required": False, "autocomplete": True},
        ],
    },
    {
        "name": "clear",
        "description": "Remove the scheduled time for this thread's match",
        "type": 1,  # SUB_COMMAND
    },
]

SCHEDULE_SUBCOMMAND_NAMES = [s["name"] for s in SCHEDULE_SUBCOMMANDS]

SCHEDULE_COMMAND = {
    "name": "schedule",
    "description": "Set or clear the scheduled time for this thread's match",
    "options": SCHEDULE_SUBCOMMANDS,
}


# Read-only and match-free, unlike /schedule: it reports when the thread's PLAYERS
# are free, which works just as well in a plain /lfg thread that has no Match at
# all. That is why it is its own command rather than a /schedule subcommand.
AVAILABILITY_COMMAND = {
    "name": "availability",
    "description": "Compare when this game's players are free",
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

# The only command that takes a FILE. Discord option type 11 (ATTACHMENT) is used
# nowhere else in this project, so the handler needs its own resolver to read the
# upload -- the option's value is the attachment's ID, and the metadata lives in
# data["resolved"]["attachments"] (see _get_attachment).
#
# Like /seating and /pick it has no thread option: the game comes from the thread
# it's run in. The description names the file type because Discord's picker will
# happily attach anything and the refusal would otherwise be the first hint.
BOXSCORE_COMMAND_NAME = "boxscore"

# /boxscore gained subcommands when the Tabletop Simulator uploader arrived: the
# object needs a token, and Discord does not let one command be both a plain
# command and a parent. So the old bare `/boxscore <file>` is now
# `/boxscore upload <file>` -- worth a changelog line, since regulars have muscle
# memory for the old form.
BOXSCORE_SUBCOMMANDS = [
    {
        "name": "upload",
        "description": "Add a box score to this game from a JSON file",
        "type": 1,  # SUB_COMMAND
        "options": [
            {
                "name": "file",
                "description": "The game's box score, as a .json file",
                "type": 11,
                "required": True,
            }
        ],
    },
    {
        "name": "token",
        "description": "Get a one-time token so the TTS object can upload this game",
        "type": 1,  # SUB_COMMAND
    },
]

BOXSCORE_SUBCOMMAND_NAMES = [s["name"] for s in BOXSCORE_SUBCOMMANDS]

BOXSCORE_COMMAND = {
    "name": BOXSCORE_COMMAND_NAME,
    "description": "Add a box score to this game",
    "options": BOXSCORE_SUBCOMMANDS,
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
        # (name, label, blurb). `name` is the whitelist key that lfg_help_steps_for_guild
        # filters on; `label` is what to render after the slash, which for a lookup is
        # "lookup map". Blurbs are deliberately worded for what the command does *inside a
        # game thread*, which differs from its general registration description.
        "commands": [
            ("random",  "random",          "Roll a random map, deck, faction, etc."),
            ("map",     "lookup map",      "Specify map you're playing on."),
            ("deck",    "lookup deck",     "Specify deck you're playing with."),
            ("faction", "lookup faction",  "Note a faction that's in the game."),
            ("seating", "seating",         "Randomly seat the players without drafting factions."),
            ("draft",   "draft",           "Draft the factions that can be selected in this game."),
            ("pick",    "pick",            "Have each player pick factions from the draft or assign "
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
    LOOKUP_COMMAND,
    CARD_COMMAND,
    STATS_COMMAND,
    UPCOMING_COMMAND,
    SCHEDULE_COMMAND,
    AVAILABILITY_COMMAND,
    RECORD_COMMAND,
    LAW_COMMAND,
    DRAFT_COMMAND,
    SEATING_COMMAND,
    PICK_COMMAND,
    ADSET_COMMAND,
    BOXSCORE_COMMAND,
    RENAME_COMMAND,
    RANDOM_COMMAND,
    LFG_COMMAND,
    LINK_COMMAND,
]


# Ordered grouping for the /help listing. Each command name should appear in
# exactly one group; any command missing from here falls into a trailing "Other"
# group (see grouped_commands) so a new command is never silently dropped.
COMMAND_GROUPS = [
    ("General", ["help"]),
    ("Lookups", ["law", "faction", "clockwork", "map", "deck", "vagabond",
                 "captain", "landmark", "hireling", "houserule", "card", "stats"]),
    ("Organization", ["availability", "schedule", "upcoming"]),
    ("Games", ["lfg", "adset", "seating", "pick",
               "boxscore", "record", "rename"]),
    ("Randomize", ["draft", "random"]),
    ("Account", ["steam"]),
]


def all_command_definitions():
    """Every command definition."""
    return list(COMMANDS)


# None of these is a whitelist toggle: /help is always available everywhere, and a
# PARENT command (/lookup, /link) has no meaning of its own -- its SUBCOMMANDS are the
# toggles, keyed for the lookups by the same names the old top-level commands used.
_NON_WHITELISTABLE = {"help", *PARENT_COMMANDS}

# Everything a guild moderator can switch on. Derived from COMMANDS (so a new command
# becomes toggleable automatically) plus every parent's subcommands.
WHITELISTABLE = ([c["name"] for c in COMMANDS if c["name"] not in _NON_WHITELISTABLE]
                 + PARENT_SUBCOMMAND_NAMES)


def whitelistable_commands():
    """(name, label, description) for every toggle a guild moderator can flip.

    `name` is the stored whitelist key -- unchanged for the lookups, which is what lets
    an existing enabled_commands list keep working. `label` is what to render after the
    slash: identical to `name` for a top-level command, "lookup faction" / "link steam"
    for a subcommand, since that's what a user actually types.

    Ordered by COMMAND_GROUPS, so the guild settings page lists commands the same way
    /help does. It used to be declaration order in COMMANDS with every subcommand
    appended after, which put /lookup faction nowhere near the other lookups and moved
    rows around whenever a definition was inserted.

    Flattened rather than grouped: the caller renders one checkbox list, and
    grouped_commands() is already the single place the ordering lives -- including the
    "Other" catch-all that keeps a command missing from COMMAND_GROUPS visible instead
    of silently unlistable.
    """
    return [row for _group, rows in grouped_commands()
            for row in rows
            if row[0] not in _NON_WHITELISTABLE]


def commands_for_guild(enabled_names):
    """Definitions to register for a guild: always /help, plus each enabled, whitelistable
    command, plus each PARENT command carrying only this guild's enabled subcommands.
    Ignores unknown/removed names so a stale whitelist never breaks registration.

    Unlike the /help and /lfg per-guild variants (substituted in register_guild_commands),
    the parents are resolved HERE because they can be dropped entirely -- a guild with no
    lookups enabled gets no /lookup at all, which substitute-in-place can't express."""
    allowed = set(enabled_names or ()) & set(WHITELISTABLE)
    out = [c for c in COMMANDS
           if c["name"] == "help"
           or (c["name"] not in PARENT_COMMANDS and c["name"] in allowed)]
    for parent_name in PARENT_COMMANDS:
        parent = parent_command_for_guild(parent_name, allowed)
        if parent:
            out.append(parent)
    return out


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
            chips = [(name, label, blurb) for name, label, blurb in chips if name in enabled]
            if not chips:
                continue
            step = {**step, "commands": chips}
        kept.append(step)
    return kept


def grouped_commands(collapse_parents=False):
    """Yield (group_name, [(name, label, description), ...]) in display order.

    `name` is the whitelist key -- what enabled_commands stores and what build_help_embed
    filters on. `label` is what to render after the slash: identical to `name` for a
    top-level command, "lookup faction" / "link steam" for a subcommand.

    By DEFAULT a parent command is skipped in favour of a row per subcommand, because
    /help filters every row against the guild's whitelist: lookups are enabled
    individually, so a bare `/lookup` row could not say which ones this server actually
    has. That is the one deliberate exception to the "Other" catch-all below -- a new
    SUBCOMMAND missing from COMMAND_GROUPS still lands in "Other", so the safety net
    keeps working for the case that matters.

    `collapse_parents=True` yields ONE row per parent instead ("/lookup", "/link"),
    which is what the public Databot page wants: it lists every command with no guild
    context to filter against, so nine near-identical lookup rows are just noise. Never
    pass it for /help, which would then be unable to show a server what it has.
    """
    if collapse_parents:
        rows_by_name = {}
        for c in all_command_definitions():
            rows_by_name[c["name"]] = (c["name"], c["name"], c.get("description", ""))
        # A parent's row is keyed by the parent name, which is NOT a whitelist key --
        # fine here because this variant is never filtered, and it keeps the parent out
        # of the "Other" catch-all below via COMMAND_GROUPS.
        grouped_names = set()
        for group_name, names in COMMAND_GROUPS:
            rows, seen = [], set()
            for name in names:
                # Map a subcommand's group entry ("faction") onto its parent, so the
                # existing COMMAND_GROUPS ordering needs no duplicate bookkeeping.
                parent = _PARENT_OF.get(name, name)
                if parent in seen or parent not in rows_by_name:
                    continue
                seen.add(parent)
                rows.append(rows_by_name[parent])
            grouped_names.update(r[0] for r in rows)
            if rows:
                yield group_name, rows
        leftover = [row for name, row in rows_by_name.items()
                    if name not in grouped_names]
        if leftover:
            yield "Other", leftover
        return

    rows_by_name = {c["name"]: (c["name"], c["name"], c.get("description", ""))
                    for c in all_command_definitions()
                    if c["name"] not in PARENT_COMMANDS}
    rows_by_name.update({
        s["name"]: (s["name"], f"{parent_name} {s['name']}", s.get("description", ""))
        for parent_name, (_parent, subs) in PARENT_COMMANDS.items()
        for s in subs
    })

    grouped_names = set()
    for group_name, names in COMMAND_GROUPS:
        rows = [rows_by_name[n] for n in names if n in rows_by_name]
        grouped_names.update(r[0] for r in rows)
        if rows:
            yield group_name, rows

    leftover = [row for name, row in rows_by_name.items() if name not in grouped_names]
    if leftover:
        yield "Other", leftover
