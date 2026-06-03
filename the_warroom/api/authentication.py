from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from the_gatehouse.models import Profile

KEYWORD = 'Api-Key'


class ProfileApiKeyAuthentication(BaseAuthentication):
    """Authenticate a request using a per-user API key stored on Profile.

    The key may be supplied either as an Authorization header:
        Authorization: Api-Key <key>
    or as an ``api_key`` query parameter (convenient for browser testing).
    """

    def authenticate(self, request):
        key = self._extract_key(request)
        if not key:
            # No key provided — let other authenticators (e.g. session) try.
            return None

        try:
            profile = Profile.objects.select_related('user').get(api_key=key)
        except Profile.DoesNotExist:
            raise AuthenticationFailed('Invalid API key.')

        if profile.user is None:
            raise AuthenticationFailed('API key is not linked to an active account.')

        return (profile.user, key)

    def authenticate_header(self, request):
        # Drives the WWW-Authenticate header so failures return 401 (not 403).
        return KEYWORD

    def _extract_key(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith(f'{KEYWORD} '):
            return auth_header[len(KEYWORD) + 1:].strip()
        return request.query_params.get('api_key')
