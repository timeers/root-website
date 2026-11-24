"""Test runner that refuses real HTTP to Discord.

Views like Edit Guild and the LFG / tournament-channel modals read live Discord
data (roles, channels, forum info) that most of their tests never mock. Unmocked,
those reads went out to discord.com for real: 404s for the fake guild ids, 429s
once the run tripped the rate limit, and a hang on a slow network -- so a test's
path depended on Discord's reply. Here every such request fails fast with a
ConnectionError, which the service functions already treat as "Discord
unreachable" (they return None), the same branch the 404s took.

The block sits at HTTPAdapter.send, below requests.get/post, so tests that mock
those (or the service functions) keep working unchanged. It's installed in the
main process and, via process_setup, in each --parallel worker: macOS spawns
workers fresh rather than forking, so they don't inherit the patch.
"""
from urllib.parse import urlparse

import requests
from django.test.runner import DiscoverRunner, ParallelTestSuite

_DISCORD_HOSTS = ("discord.com", "discordapp.com")


class DiscordNetworkBlocked(requests.exceptions.ConnectionError):
    """A test tried to reach Discord for real; mock the call instead."""


_real_send = requests.adapters.HTTPAdapter.send


def _guarded_send(self, request, *args, **kwargs):
    host = urlparse(request.url).hostname or ""
    if any(host == h or host.endswith("." + h) for h in _DISCORD_HOSTS):
        raise DiscordNetworkBlocked(
            f"real Discord HTTP is blocked under test: {request.method} {request.url}",
            request=request)
    return _real_send(self, request, *args, **kwargs)


def block_discord_network():
    requests.adapters.HTTPAdapter.send = _guarded_send


def _worker_setup(*args):
    # Django calls this as a plain function (process_setup.__func__, no self).
    block_discord_network()


class _GuardedParallelTestSuite(ParallelTestSuite):
    process_setup = _worker_setup


class NoDiscordNetworkRunner(DiscoverRunner):
    parallel_test_suite = _GuardedParallelTestSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        block_discord_network()
