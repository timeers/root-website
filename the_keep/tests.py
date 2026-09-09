import json

from django.test import TestCase

from the_keep.forms import discord_thread_guild_error

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


class DiscordThreadGuildErrorTests(TestCase):
    """The ww_link / wr_link / fr_link rule.

    This replaced a bare `f"discord.com/channels/{guild}" in url` substring
    test. The rejection cases below are the point of the change -- each one
    PASSED under the old rule.
    """

    GUILD = str(config['WW_GUILD_ID'])
    OTHER_GUILD = "111122223333444455"
    SERVER = "Woodland Warriors"

    def check(self, url):
        return discord_thread_guild_error(url, self.GUILD, self.SERVER)

    def assertAccepted(self, url):
        self.assertIsNone(self.check(url), f"should have been accepted: {url!r}")

    def assertRejected(self, url):
        self.assertIsNotNone(self.check(url), f"should have been rejected: {url!r}")

    # --- accepted -------------------------------------------------------
    def test_canonical_thread_link(self):
        self.assertAccepted(f"https://discord.com/channels/{self.GUILD}/123456789")

    def test_link_with_a_message_id(self):
        """What Discord's "Copy Message Link" produces."""
        self.assertAccepted(
            f"https://discord.com/channels/{self.GUILD}/123456789/987654321")

    def test_discordapp_com_is_accepted(self):
        """A legitimate Discord domain the old substring rule REJECTED."""
        self.assertAccepted(f"https://discordapp.com/channels/{self.GUILD}/123456789")

    def test_trailing_slash(self):
        self.assertAccepted(f"https://discord.com/channels/{self.GUILD}/123456789/")

    def test_surrounding_whitespace(self):
        self.assertAccepted(f"  https://discord.com/channels/{self.GUILD}/123456789 ")

    def test_empty_is_not_an_error(self):
        """Required-ness belongs to the field, not this check."""
        self.assertAccepted("")
        self.assertAccepted(None)

    # --- rejected (each of these passed the OLD substring rule) ---------
    def test_rejects_a_spoofed_host(self):
        """The old rule searched the whole string for the guild id, so any host
        carrying that path passed."""
        self.assertRejected(
            f"https://evil.example.com/discord.com/channels/{self.GUILD}/1")

    def test_rejects_a_link_with_no_thread_id(self):
        """The error says "not a valid thread", so a bare guild link is wrong
        even though the old rule allowed it."""
        self.assertRejected(f"https://discord.com/channels/{self.GUILD}")

    def test_rejects_the_guild_id_appearing_elsewhere(self):
        self.assertRejected(f"https://example.com/?ref=discord.com/channels/{self.GUILD}/1")

    # --- rejected (rejected before too) --------------------------------
    def test_rejects_another_guild(self):
        self.assertRejected(
            f"https://discord.com/channels/{self.OTHER_GUILD}/123456789")

    def test_rejects_an_invite_link(self):
        self.assertRejected("https://discord.com/invite/abcdef")

    def test_rejects_a_non_discord_url(self):
        self.assertRejected("https://ledergames.com/products/root")

    # --- the messages differ, so the user knows which thing is wrong ----
    def test_wrong_guild_and_wrong_shape_give_different_advice(self):
        wrong_shape = self.check("https://discord.com/invite/abcdef")
        wrong_guild = self.check(
            f"https://discord.com/channels/{self.OTHER_GUILD}/123456789")
        self.assertIn("Copy", wrong_shape)
        self.assertIn("correct Discord server", wrong_guild)
        self.assertIn(self.SERVER, wrong_shape)
        self.assertIn(self.SERVER, wrong_guild)
