import time
import logging

from django.utils.timezone import localdate
from django.utils.translation import activate
from .models import DailyUserVisit

logger = logging.getLogger(__name__)

class SetLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = None

        if request.user.is_authenticated:
            # If the user is authenticated, use their language preference
            if request.user.profile.language:
                language = request.user.profile.language.code if hasattr(request.user.profile, 'language') else None

        # This Part will see if there is already a session language set and if not will use the browser's language
        if not language and 'language' in request.session:
            # If no language set from the user, fall back to the session language
            language = request.session['language']

        # if not language:
        #     # If no language preference is set, use the browser's language
        #     language = get_language_from_request(request)

        if not language:
            language = 'en'

        # Set the language in the current session (useful for later requests)
        activate(language)
        request.session['language'] = language

        response = self.get_response(request)
        return response

class DailyUserVisitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_authenticated:
            today = str(localdate())
            cache_key = f"daily_visit:{request.user.id}:{today}"

            if not request.session.get(cache_key):
                DailyUserVisit.objects.get_or_create(profile=request.user.profile, date=today)
                request.session[cache_key] = True

        return response
    


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.time()
        response = self.get_response(request)
        duration = time.time() - start

        if duration > 4:  # Only log if slower than 4 seconds
            logger.warning(f"Slow request: {request.path} took {duration:.2f}s")
        return response
