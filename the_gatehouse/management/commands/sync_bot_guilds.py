"""
Sync which Discord guilds the bot is a member of.

Updates DiscordGuild.bot_member so the site knows which users are
DM-reachable (the bot can only DM users who share a guild with it).
Run after inviting the bot to a new server; also runnable on a schedule
via the sync_bot_guilds_task Celery task.

    python manage.py sync_bot_guilds
"""
from django.core.management.base import BaseCommand

from the_gatehouse.services.discordservice import sync_bot_guilds


class Command(BaseCommand):
    help = "Refresh DiscordGuild.bot_member from the bot's actual guild membership."

    def handle(self, *args, **options):
        count = sync_bot_guilds()
        if count is None:
            self.stderr.write(self.style.ERROR(
                "Failed to fetch bot guilds from Discord (see logs)."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Bot is a member of {count} guild(s); bot_member flags updated."
            ))
