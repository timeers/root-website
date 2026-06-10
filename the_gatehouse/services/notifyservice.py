"""
Discord DM notifications for game/survey events.

Gathers the recipients for an event, filters to those who opted in via their
Profile.notify_* preferences, dedupes so each user gets at most one DM per
event, and queues a plain-text DM (with a link) per recipient.

Reachability is NOT pre-checked here — send_discord_dm decides at send time
and treats a 403 as terminal, so a stale DiscordGuild.bot_member flag never
suppresses a deliverable DM.
"""
import json
import logging

from the_gatehouse.tasks import send_discord_dm_task

logger = logging.getLogger(__name__)

with open('/etc/config.json') as config_file:
    config = json.load(config_file)

SITE_URL = config.get("SITE_URL", "").rstrip("/")


def _link(relative_url):
    return f"{SITE_URL}{relative_url}" if SITE_URL else relative_url


def _queue_dm(profile, content):
    """Queue a DM to a profile's user, if it has a linked user account."""
    user_id = getattr(profile, "user_id", None)
    if not user_id:
        return
    send_discord_dm_task.delay(user_id, content=content)


def _component_designers(game):
    """Set of Profiles who designed any component used in the game."""
    designers = set()

    if game.deck and game.deck.designer:
        designers.add(game.deck.designer)
    if game.map and game.map.designer:
        designers.add(game.map.designer)

    for effort in game.efforts.all():
        if effort.faction and effort.faction.designer:
            designers.add(effort.faction.designer)
        if effort.vagabond and effort.vagabond.designer:
            designers.add(effort.vagabond.designer)

    for landmark in game.landmarks.all():
        if landmark.designer:
            designers.add(landmark.designer)
    for hireling in game.hirelings.all():
        if hireling.designer:
            designers.add(hireling.designer)
    for tweak in game.tweaks.all():
        if tweak.designer:
            designers.add(tweak.designer)

    return designers


def notify_game_recorded(game):
    """
    DM opted-in users when a game is recorded (finalized). Each recipient gets
    one combined message listing every reason that applies to them:
      - notify_game_recorded: they played in the game (recorder excluded)
      - notify_post_game_recorded: a component they designed was used
      - notify_tournament_game_recorded: they host the tournament (organizer/mod)
    """
    # profile -> list of reason strings (only kept if the matching pref is on)
    reasons = {}

    def add_reason(profile, pref_attr, text):
        if profile is None:
            return
        if not getattr(profile, pref_attr, False):
            return
        reasons.setdefault(profile, [])
        if text not in reasons[profile]:
            reasons[profile].append(text)

    # Players (exclude the recorder — they know, they recorded it)
    recorder = game.recorder
    for effort in game.efforts.all():
        player = effort.player
        if player and player != recorder:
            add_reason(player, "notify_game_recorded", "you played in it")

    # Component designers
    for designer in _component_designers(game):
        add_reason(designer, "notify_post_game_recorded", "it used a component you designed")

    # Tournament host(s): organizer + moderators
    if game.round_id:
        tournament = game.round.get_tournament()
        if tournament:
            hosts = set()
            if tournament.designer:
                hosts.add(tournament.designer)
            hosts.update(tournament.moderators.all())
            for host in hosts:
                add_reason(host, "notify_tournament_game_recorded",
                           "it's part of a tournament you host")

    if not reasons:
        return

    game_title = game.nickname if game.nickname else f"{game.platform} Game"
    link = _link(game.get_absolute_url())

    for profile, why in reasons.items():
        content = (
            f"A game was recorded ({' and '.join(why)}): "
            f"[{game_title}]({link})"
        )
        _queue_dm(profile, content)


def notify_post_approved(post):
    """
    DM a post's designer when their submission is moved out of Submitted
    status (i.e. approved into Development), if they opted in.
    """
    designer = post.designer
    if designer is None or not getattr(designer, "notify_post_approved", False):
        return

    link = _link(post.get_absolute_url())
    content = (
        f"Your submitted {post.get_component_display()} was approved: "
        f"[{post.title}]({link})"
    )
    _queue_dm(designer, content)


def notify_survey_response(survey_response):
    """DM the survey owner when someone submits a response, if they opted in."""
    survey = survey_response.survey
    owner = survey.created_by
    respondent = survey_response.profile

    if owner is None or owner == respondent:
        return
    if not getattr(owner, "notify_survey_response", False):
        return

    link = _link(survey.get_absolute_url())
    content = f"A new response was submitted to your survey: [{survey.title}]({link})"
    _queue_dm(owner, content)
