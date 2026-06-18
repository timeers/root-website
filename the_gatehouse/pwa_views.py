from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def manifest(request):
    response = render(request, 'pwa/manifest.json')
    response['Content-Type'] = 'application/manifest+json'
    return response


@require_GET
def service_worker(request):
    response = render(
        request,
        'pwa/service-worker.js',
        {'version': settings.PWA_CACHE_VERSION},
    )
    response['Content-Type'] = 'application/javascript'
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


@require_GET
def offline(request):
    return render(request, 'pwa/offline.html')
