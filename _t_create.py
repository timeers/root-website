import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','django_project.settings'); django.setup()
from django.test import Client
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]
from django.contrib.auth.models import User
from the_warroom.models import Tournament, Stage, Round

c = Client(); c.force_login(User.objects.filter(is_superuser=True).first())
NAME = 'ZZ Inactive Probe'
Tournament.objects.filter(name=NAME).delete()

data = {
    'name': NAME, 'classification': 'Game Group', 'description': 'probe',
    'start_date': '2026-01-01', 'end_date': '2026-12-31',
    'leaderboard_positions': 10, 'game_threshold': 1, 'asset_mode': 2,
    'coalition_type': 'One', 'platform': '', 'max_players': 4, 'min_players': 2,
    'recording_access': 'moderators', 
    'tab_order': 'games,overview,elo,details',
}
resp = c.post('/new/series/', data)
print('POST status:', resp.status_code, '| redirect:', resp.get('Location'))

t = Tournament.objects.filter(name=NAME).first()
if not t:
    print('NOT CREATED')
else:
    print('created OK  pk=%s slug=%s' % (t.pk, t.slug))
    print('  tab_order  :', t.tab_order)
    print('  hidden_tabs:', sorted(t.hidden_tabs))
    print('  landing url:', t.get_absolute_url())
    print('  stages=%d rounds=%d factions=%d' % (
        Stage.objects.filter(tournament=t).count(),
        Round.objects.filter(stage__tournament=t).count(),
        t.factions.count()))
    # the redirect target must actually render
    print('  follow redirect:', c.get(t.get_absolute_url()).status_code)
    t.delete(); print('  cleaned up')
