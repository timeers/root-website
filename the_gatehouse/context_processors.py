# Context Processors
from django.db.models import Q
from the_keep.models import Post
from the_warroom.models import Game, ScoreCard
from .models import Website
from .services.context_service import get_theme

from django.core.exceptions import ObjectDoesNotExist

def active_user_data(request):
    try:
        config = Website.get_singular_instance()
        site_title = config.site_title
        global_message = config.global_message
        global_message_type = config.message_type
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
        scorecard_count = 0
        unassigned_scorecards = 0
        bookmarks = 0
        approved_invites = []
        user_notifications = []

        if hasattr(request, 'user') and request.user.is_authenticated:
            try:
                profile = request.user.profile
                post_count = Post.objects.filter(designer=profile).count()
                recent_posts = Post.objects.filter(
                    Q(designer=profile) | Q(co_designers=profile, co_designers_can_edit=True)
                    ).exclude(status='9').order_by('-date_updated')[:3]
                in_process_games = Game.objects.filter(final=False, recorder=profile).count()
                game_count = Game.objects.filter(efforts__player=profile).distinct().count()
                scorecard_count = ScoreCard.objects.filter(recorder=profile, final=True).count()
                unassigned_scorecards = ScoreCard.objects.filter(final=False, recorder=profile).count()
                bookmarked_games = profile.bookmarkedgames.count()
                bookmarked_posts = profile.bookmarkedposts.count()
                bookmarks = bookmarked_posts + bookmarked_games

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
            'user_active_scorecards_count': unassigned_scorecards,
            'user_active_count': unassigned_scorecards + in_process_games,
            'user_bookmarks_count': bookmarks,
            'user_scorecard_count': scorecard_count,
            'approved_invites': approved_invites,
            'user_notifications': user_notifications,
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
