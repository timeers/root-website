# Context Processors
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from the_keep.models import Post, PNPAsset
from the_warroom.models import Game, ScoreCard, Match, Round, CompetitionStatus
from .models import Website
from .services.context_service import get_theme

from django.core.exceptions import ObjectDoesNotExist

def active_user_data(request):
    try:
        config = Website.get_singular_instance()
        site_title = config.site_title
        global_message = config.global_message
        global_message_type = config.message_type
        dismissed_key = request.session.get('dismissed_global_msg')
        if global_message and dismissed_key == config.date_modified.isoformat():
            global_message = None
        woodland_warriors_invite = config.woodland_warriors_invite
        french_root_invite = config.french_root_invite
        if config.primary_discord_guild:
            rdb_feedback_invite = config.primary_discord_guild.server_invite
        else:
            rdb_feedback_invite = None

        post_count = 0
        recent_posts = []
        in_process_games = 0
        game_count = 0
        recorded_game_count = 0
        scorecard_count = 0
        unassigned_scorecards = 0
        bookmarks = 0
        has_shared_assets = False
        approved_invites = []
        user_notifications = []
        next_scheduled_matches = []
        has_scheduled_matches = False

        if hasattr(request, 'user') and request.user.is_authenticated:
            try:
                profile = request.user.profile
                post_count = Post.objects.filter(designer=profile).count()
                recent_posts = Post.objects.filter(
                    Q(designer=profile) | Q(co_designers=profile, co_designers_can_edit=True)
                    ).exclude(status='9').order_by('-date_updated')[:3]
                in_process_games = Game.objects.filter(final=False, recorder=profile).count()
                game_count = Game.objects.filter(final=True, efforts__player=profile).distinct().count()
                recorded_game_count = Game.objects.filter(recorder=profile).distinct().count()
                scorecard_count = ScoreCard.objects.filter(recorder=profile, final=True).count()
                unassigned_scorecards = ScoreCard.objects.filter(final=False, recorder=profile).count()
                bookmarked_games = profile.bookmarkedgames.count()
                bookmarked_posts = profile.bookmarkedposts.count()
                bookmarks = bookmarked_posts + bookmarked_games

                # Whether the user has any shared workshop resources (drives the
                # "My Resources" header sub-item; mirrors `shared_assets` in the
                # workshop views but as a cheap global existence check).
                has_shared_assets = PNPAsset.objects.filter(shared_by=profile).exists()

                # Get approved invites for guilds the user is not yet a member of
                # Excludes completed invites (user already joined) and guilds already in
                approved_invites = profile.guild_join_requests.filter(
                    status="approved"
                ).exclude(
                    guild__in=profile.guilds.all()
                )

                # Get user notifications that haven't been dismissed
                from .models import UserNotification
                user_notifications = UserNotification.objects.filter(
                    profile=profile,
                    is_dismissed=False
                ).order_by('-created_at')

                # Scheduled matches the player is seated in. Mirrors
                # _upcoming_scheduled_matches() in the_warroom.views: finalized
                # bracket, a scheduled_time, and no recorded result yet. Scoped to
                # this player's seats across all tournaments.
                # distinct() guards against duplicate rows when the player holds
                # more than one seat in a series.
                scheduled_matches = (
                    Match.objects.filter(
                        series__matchseat__stage_participant__tournament_player__profile=profile,
                        round__bracket_status=Round.BracketStatusChoices.FINALIZED,
                        scheduled_time__isnull=False,
                    )
                    .exclude(status=CompetitionStatus.COMPLETED)
                    .exclude(game__final=True)
                    .distinct()
                )

                # The menu's "Next Scheduled Match" entry keeps a 6h leeway so it
                # points at the genuinely next game rather than the oldest overdue
                # one: a match that just kicked off still counts, one from last
                # month does not.
                schedule_cutoff = timezone.now() - timedelta(hours=6)
                next_scheduled_matches = list(
                    scheduled_matches
                    .filter(scheduled_time__gte=schedule_cutoff)
                    .select_related('round', 'round__stage', 'round__stage__tournament', 'series')
                    .order_by('scheduled_time')[:1]
                )

                # Gates the "View all scheduled matches" link independently, with no
                # cutoff, so a player whose matches are all overdue can still reach
                # the schedule page (which lists them).
                has_scheduled_matches = scheduled_matches.exists()

            except ObjectDoesNotExist:
                pass  # profile or related object not found
            except Exception:
                pass  # fail silently if something weird happens

        theme = get_theme(request)

        theme_artists = theme.get_artists()

        return {
            'site_title': site_title,
            'user_posts_count': post_count,
            'user_recent_posts': recent_posts,
            'user_active_games_count': in_process_games,
            'user_games_count': game_count,
            'user_recorded_game_count': recorded_game_count,
            'user_active_scorecards_count': unassigned_scorecards,
            'user_active_count': unassigned_scorecards + in_process_games,
            'user_bookmarks_count': bookmarks,
            'user_has_shared_assets': has_shared_assets,
            'user_scorecard_count': scorecard_count,
            'approved_invites': approved_invites,
            'user_notifications': user_notifications,
            'next_scheduled_matches': next_scheduled_matches,
            'has_scheduled_matches': has_scheduled_matches,
            'theme': theme,
            'theme_artists': theme_artists,
            'global_message': global_message,
            'global_message_type': global_message_type,
            'woodland_warriors_invite': woodland_warriors_invite,
            'french_root_invite': french_root_invite,
            'rdb_feedback_invite': rdb_feedback_invite,
        }

    except Exception:
        # If the whole thing fails, return minimal fallback context
        return {
            'site_title': 'Root Database',
            'theme': None,
        }
