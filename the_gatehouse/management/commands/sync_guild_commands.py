"""
Push each bot guild's per-guild slash command set (always /help + its whitelist) to
Discord.

Run once after switching from global to per-guild command registration (i.e. after
`register_discord_commands --clear`), and any time you want to re-sync every guild's
commands to match its DiscordGuild.enabled_commands.

    python manage.py sync_guild_commands

Guild-scoped command registration is rate-limited, so this sleeps briefly between
guilds and respects a 429's Retry-After. Per-guild failures are logged and skipped by
register_guild_commands, so one bad guild won't abort the sweep.
"""
import time

from django.core.management.base import BaseCommand

from the_gatehouse.models import DiscordGuild
from the_gatehouse.services.discordservice import register_guild_commands


class Command(BaseCommand):
    help = "Register per-guild slash commands for every guild the bot is in."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delay",
            type=float,
            default=1.0,
            help="Seconds to pause between guilds to stay under Discord's rate limit.",
        )

    def handle(self, *args, **options):
        delay = options["delay"]
        guilds = DiscordGuild.objects.filter(bot_member=True)
        total = guilds.count()
        ok = 0
        for guild in guilds:
            if register_guild_commands(guild):
                ok += 1
            else:
                self.stderr.write(self.style.WARNING(
                    f"Failed to register commands for {guild.guild_id} ({guild.name})."
                ))
            time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"Registered commands for {ok}/{total} bot guild(s)."
        ))
