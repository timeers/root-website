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


def _link_embed(title, relative_url):
    """
    Build a minimal Discord embed whose title is a clickable link.

    Masked links ([text](url)) don't render in a plain DM body, so the link is
    carried here instead: an embed with a title + url shows the title as a
    clickable link below the plain-text message content.
    """
    return {"title": title, "url": _link(relative_url)}


def _queue_dm(profile, content, embed=None):
    """Queue a DM to a profile's user, if it has a linked user account."""
    user_id = getattr(profile, "user_id", None)
    if not user_id:
        return
    send_discord_dm_task.delay(user_id, content=content, embed=embed)


def _join_names(names):
    """Join component names as 'A', 'A and B', or 'A, B and C'."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _component_designers(game):
    """
    Map each designer Profile to the list of component names they designed in
    the game, preserving discovery order and de-duplicating per designer (a
    designer credited on two components gets both names, once each).
    """
    designers = {}

    def add(component):
        if component and component.designer:
            names = designers.setdefault(component.designer, [])
            if component.title not in names:
                names.append(component.title)

    add(game.deck)
    add(game.map)

    for effort in game.efforts.all():
        add(effort.faction)
        add(effort.vagabond)

    for landmark in game.landmarks.all():
        add(landmark)
    for hireling in game.hirelings.all():
        add(hireling)
    for tweak in game.tweaks.all():
        add(tweak)

    return designers


def notify_game_recorded(game):
    """
    DM opted-in users when a game is recorded (finalized). A recipient gets a
    separate message for each reason that applies to them (deduped per
    profile+reason so the same reason is never sent twice):
      - notify_game_recorded: they played in the game (recorder excluded)
      - notify_post_game_recorded: a component they designed was used
      - notify_tournament_game_recorded: they host the tournament (organizer/mod)
    """
    game_title = game.nickname if game.nickname else f"{game.platform} Game"
    embed = _link_embed(game_title, game.get_absolute_url())

    # (profile, pref_attr) pairs already queued, so a recipient gets at most one
    # DM per reason (e.g. a designer credited on two components in the game).
    sent = set()

    def notify(profile, pref_attr, content):
        if profile is None:
            return
        if not getattr(profile, pref_attr, False):
            return
        key = (profile.pk, pref_attr)
        if key in sent:
            return
        sent.add(key)
        _queue_dm(profile, content, embed=embed)

    # Players (exclude the recorder — they know, they recorded it)
    recorder = game.recorder
    for effort in game.efforts.all():
        player = effort.player
        if player and player != recorder:
            notify(player, "notify_game_recorded", "A game you played in was recorded:")

    # Component designers
    for designer, names in _component_designers(game).items():
        notify(designer, "notify_post_game_recorded",
               f"A game using {_join_names(names)} was recorded:")

    # Tournament host(s): organizer + moderators
    if game.round_id:
        tournament = game.round.get_tournament()
        if tournament:
            hosts = set()
            if tournament.designer:
                hosts.add(tournament.designer)
            hosts.update(tournament.moderators.all())
            for host in hosts:
                notify(host, "notify_tournament_game_recorded",
                       f"A {tournament.name} game was recorded:")


def notify_post_approved(post):
    """
    DM a post's designer when their submission is moved out of Submitted
    status (i.e. approved into Development), if they opted in.
    """
    designer = post.designer
    if designer is None or not getattr(designer, "notify_post_approved", False):
        return

    content = f"Your submitted {post.get_component_display()} was approved:"
    embed = _link_embed(post.title, post.get_absolute_url())
    _queue_dm(designer, content, embed=embed)


def notify_survey_response(survey_response):
    """DM the survey owner when someone submits a response, if they opted in."""
    survey = survey_response.survey
    owner = survey.created_by
    respondent = survey_response.profile

    if owner is None or owner == respondent:
        return
    if not getattr(owner, "notify_survey_response", False):
        return

    content = f"A new response was submitted to {survey.title}:"
    respondent_name = respondent.name if respondent else "Anonymous"
    embed = _link_embed(respondent_name, survey_response.get_absolute_url())
    _queue_dm(owner, content, embed=embed)
