"""
Register the bot's slash commands with Discord.

Run after deploying (or changing) the command definitions:

    python manage.py register_discord_commands              # global (can take minutes to appear)
    python manage.py register_discord_commands --guild 123  # instant, for a test server

Global commands propagate slowly; register against a test guild while iterating.
"""
import requests

from django.core.management.base import BaseCommand

from the_gatehouse.services.discordservice import DISCORD_API, _bot_headers, config

# Slash-command definitions. Add new commands to this list.
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


COMMANDS = [
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


class Command(BaseCommand):
    help = "Register the bot's slash commands with Discord (global or per-guild)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--guild",
            type=str,
            default=None,
            help="Register to a specific guild (server) ID for instant availability.",
        )

    def handle(self, *args, **options):
        app_id = config["DISCORD_ID"]  # OAuth client ID doubles as the application ID
        guild_id = options["guild"]

        if guild_id:
            url = f"{DISCORD_API}/applications/{app_id}/guilds/{guild_id}/commands"
            scope = f"guild {guild_id}"
        else:
            url = f"{DISCORD_API}/applications/{app_id}/commands"
            scope = "global"

        # PUT bulk-overwrites the full command set for this scope.
        response = requests.put(url, headers=_bot_headers(), json=COMMANDS, timeout=10)

        if response.status_code in (200, 201):
            names = ", ".join(f"/{c['name']}" for c in COMMANDS)
            self.stdout.write(self.style.SUCCESS(
                f"Registered {len(COMMANDS)} command(s) [{scope}]: {names}"
            ))
        else:
            self.stderr.write(self.style.ERROR(
                f"Failed to register commands ({response.status_code}): {response.text}"
            ))
