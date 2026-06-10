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
COMMANDS = [
    {
        "name": "faction",
        "description": "Look up a Root faction by name",
        "options": [
            {
                "name": "name",
                "description": "Faction name to search",
                "type": 3,  # STRING
                "required": True,
            }
        ],
    },
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
