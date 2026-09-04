"""Outbound site notifications sent to Discord webhook URLs.

NOT bot code: these post to per-category webhook URLs from /etc/config.json and
use no token at all — neither the bot token nor a user's OAuth token. The bot's
own messaging (DMs, channel posts, interaction replies) lives in
the_databot.services.discordservice.

Deliberately free of model imports. Nine apps reach these senders through
the_gatehouse.tasks, and keeping this module ORM-free keeps that path cheap to
import and free of circular-import pressure.
"""
import json
import logging

import requests

logger = logging.getLogger(__name__)

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


def apply_discord_category(category):

    webhook_url = ''
    embed_title = ''
    embed_color = ''
        # Set the webhook URL based on the category
    if category == 'feedback':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Feedback Received"
        embed_color = 0x00FF00  # Green color for feedback
    elif category == 'bug':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Bug Reported"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'report':
        webhook_url = config['DISCORD_REPORTS_WEBHOOK_URL']
        embed_title = "Report Received"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'request':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Request Received"
        embed_color = 0x0000FF  # Blue color for request
    elif category == 'weird-root' or category == 'french-root':
        webhook_url = config['DISCORD_REPORTS_WEBHOOK_URL']
        embed_title = "Invite Requested"
        embed_color = 0x9746c7  # Purple color for invite
    elif category == 'user_updates':
        webhook_url = config['DISCORD_NEW_USER_WEBHOOK_URL']
        embed_title = 'New User Registered'
        embed_color = 0xed3eed # Pink for new users
    elif category == 'New Post':
        webhook_url = config['DISCORD_NEW_POST_WEBHOOK_URL']
        embed_title = "Report Received"
        embed_color = 0x00FF00  # Green color for new
    elif category == 'New Game':
        webhook_url = config['DISCORD_NEW_GAME_WEBHOOK_URL']
        embed_title = "New Game Recorded"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'FAQ Law':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_color = 800080  # Red color for report
    elif category == 'Post Created':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Post Created"
        embed_color = 0x00FF00  # Green color for new
    elif category == 'survey':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Created"
        embed_color = 0xCF9FFF  # Light violet color for surveys
    elif category == 'Post Edited':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Post Edited"
        embed_color = 0x00FF00  # Green color for new
    # Forge stuff
    elif category == 'forge-activity':
        webhook_url = config['DISCORD_FORGE_URL']
        embed_title = "Forged Faction"
        embed_color = 0xffa500  # Orange color for Forge
    elif category == 'forge-feedback':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Forge Feedback"
        embed_color = 0xffa500  # Orange, matches existing Forge category
    # Automations
    elif category == 'automation':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Automation"
        embed_color = 0x808080  # Grey color for unknown category
    elif category == 'rdl-import':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Import"
        embed_color = 0xc7ef8e # Green
    elif category == 'rdl-update':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Update"
        embed_color = 0xcbfbfd # Blue
    elif category == 'rdl-delete':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Delete"
        embed_color = 0xf95965 # Red
    elif category == 'user-summary':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Daily User Summary"
        embed_color = 0xc29ce4 # Purple
    elif category == 'inactive-cleanup':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Inactive Cleanup"
        embed_color = 0xfd9651 # Orange

    # Other
    else:
        webhook_url = config['DISCORD_USER_EVENTS_WEBHOOK_URL']
        embed_title = "Activity"
        embed_color = 0x808080  # Grey color for unknown category

    return webhook_url, embed_title, embed_color


def send_discord_message(message, category=None):
    # Check if DEBUG is False in the config
    if config["DEBUG_VALUE"] == "True":
        return  # Do nothing if DEBUG is True

    webhook_url, _, _ = apply_discord_category(category=category)
    
    # Define the payload (message) to be sent
    payload = {
        'content': message,  # Message to be sent
    }

    # Send POST request to Discord webhook URL
    response = requests.post(webhook_url, json=payload, timeout=5)
    
    if response.status_code != 204:
        logger.error(
            "Discord webhook failed: status=%s body=%s url=%s",
            response.status_code, response.text[:200], webhook_url,
        )

def send_rich_discord_message(message, category=None, author_name=None, author_icon_url=None, title=None, color=None, fields=None, url=None):
    # Check if DEBUG is False in the config (uncomment this to test it)
    if config["DEBUG_VALUE"] == "True":
        return  # Do nothing if DEBUG is True
    
    webhook_url, embed_title, embed_color = apply_discord_category(category=category)

    # Base embed structure
    embed = {
        'description': message,
        'author': {
            'name': author_name,
            'icon_url': author_icon_url,
        },
        'title': embed_title,  # Title based on category
        'color': embed_color,  # Color based on category
    }

    # Add the title if provided
    if title:
        embed['title'] = title

    # Add a URL to make the title a clickable link (Discord renders the title
    # as a hyperlink only when both title and url are present)
    if url:
        embed['url'] = url

    # Add the color if provided (to override the default category color)
    if color:
        embed['color'] = color

    # Add fields if provided
    if fields:
        embed['fields'] = []
        for field in fields:
            embed['fields'].append({
                'name': field.get('name', 'Field Name'),
                'value': field.get('value', 'Field Value'),
                'inline': field.get('inline', False),  # Whether to display inline or not
            })

    # Payload to send to Discord
    payload = {
        # 'content': message,  # Removed because content is already in embed
        'embeds': [embed],  # Only one embed in this case
    }

    # Send POST request to Discord webhook URL
    response = requests.post(webhook_url, json=payload, timeout=5)
    
    if response.status_code != 204:
        logger.error(
            "Discord webhook failed",
            extra={
                'status_code': response.status_code,
                'response': response.text,
            }
        )



def send_new_survey_notification(*, profile, survey, type):
    if not profile or not survey:
        logger.warning("Missing profile or survey for survey notification")
        return False

    fields = []

    try:
        # Core info
        if survey.pk:
            fields.append({'name': 'Questions:', 'value': survey.question_count()})

        if survey.post_id:
            fields.append({'name': 'Post:', 'value': survey.post.title})

        if survey.series_id:
            fields.append({'name': 'Series:', 'value': survey.series.name})

        if survey.stage_id:
            fields.append({'name': 'Stage:', 'value': survey.stage.name})

        if not survey.is_public:
            if survey.guild_id:
                fields.append({'name': 'Guild:', 'value': survey.guild.name})

            if survey.invited_players.exists():
                fields.append({
                    'name': 'Invited Players:',
                    'value': survey.invited_players.count()
                })

        author = profile.discord or profile.user.username if profile.user else "Unknown"

        # Lazy: the_gatehouse.tasks imports the_keep/the_warroom models, and this
        # module is deliberately model-free (see the module docstring).
        from the_gatehouse.tasks import send_rich_discord_message_task

        send_rich_discord_message_task.delay(
            message=survey.title,
            author_name=author,
            category='survey',
            title=f'{type} Survey',
            fields=fields,
        )

        return True

    except Exception:
        logger.exception(
            "Failed to queue survey notification",
            extra={
                'survey_id': survey.pk,
                'profile_id': profile.pk if profile else None,
            }
        )
        return False
