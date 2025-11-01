import json
import requests
from celery import shared_task
from django.utils import timezone

from .services.github_laws import sync_github_rules
from the_gatehouse.models import Language, Website
from the_gatehouse.services.discordservice import send_discord_message

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def sync_rules_task(self):
    site = Website.get_singular_instance()
    site.last_law_check = timezone.now()
    site.save(update_fields=["last_law_check"])
    try:
        token = config['GITHUB_KEY']
    except KeyError:
        message = "GitHub token missing from config file."
        return message

    updated_locales = []
    message = "No new rules available from Leder Rules Library."

    try:
        for language in Language.objects.all():
            rules_updated = sync_github_rules(language=language, token=token)
            if rules_updated:
                updated_locales.append(language.locale)

    except requests.exceptions.RequestException as e:
        # Network or request-level error
        message = f"Network error while syncing rules: {e}"
        return message

    except Exception as e:
        # Handle GitHub auth/rate limit feedback if your sync function raises specific errors
        err_str = str(e).lower()
        if "401" in err_str or "bad credentials" in err_str:
            message = "Invalid GitHub token — please check your configuration."
        elif "403" in err_str and "rate limit" in err_str:
            message = "GitHub API rate limit exceeded. Try again later."
        else:
            message = f"Unexpected error during sync: {e}"
        return message

    # Handle successful sync
    if updated_locales:
        locales_str = ", ".join(updated_locales)
        message = f"New Rules available for the following languages: {locales_str}"
        send_discord_message(message=message, category='automation')
    else:
        message = "No new rules available from Leder Rules Library."

    return message

