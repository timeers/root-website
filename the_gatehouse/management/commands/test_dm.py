"""
Send a test Discord DM to a user, to verify the bot token + DM delivery.

    python manage.py test_dm <username> [--message "..."]

Requires:
  - DISCORD_BOT_TOKEN in /etc/config.json
  - DEBUG_VALUE = "False" in /etc/config.json (send is skipped when "True")
  - The bot must share a Discord server with the target user.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from the_gatehouse.services.discordservice import (
    send_discord_dm, DM_OK, DM_BLOCKED, DM_ERROR,
)


class Command(BaseCommand):
    help = "Send a test Discord DM to a user (verifies bot token + delivery)."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Django username of the recipient.")
        parser.add_argument(
            "--message",
            default="✅ Test DM from the RDB bot. If you can read this, DMs work!",
            help="Message body to send.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass the DEBUG_VALUE guard so the DM sends even in a debug environment.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist:
            raise CommandError(f"No user with username '{options['username']}'.")

        result = send_discord_dm(user, content=options["message"], force=options["force"])

        if result == DM_OK:
            self.stdout.write(self.style.SUCCESS(f"DM delivered to {user.username}."))
        elif result == DM_BLOCKED:
            self.stdout.write(self.style.WARNING(
                "DM blocked (permanent): the bot shares no server with this user, "
                "the user has DMs disabled, the user has no linked Discord ID, or "
                "DEBUG_VALUE is \"True\". Not a transient error."
            ))
        elif result == DM_ERROR:
            self.stderr.write(self.style.ERROR(
                "DM failed (transient): network/5xx/rate-limit. Check logs and retry."
            ))
