# Context Processors
from the_keep.models import Post
from the_warroom.models import Game, ScoreCard
from .models import Website
from .utils import get_theme

def active_user_data(request):

    config = Website.get_singular_instance()
    site_title = config.site_title
    global_message = config.global_message
    global_message_type = config.message_type

    # You can add any data you want to be available in templates here
    if request.user.is_authenticated:
        
        profile = request.user.profile
        post_count = Post.objects.filter(designer=profile).count()
        recent_posts = Post.objects.filter(designer=profile).order_by('-date_updated')[:3]
        in_process_games = Game.objects.filter(final=False, recorder=profile).count()
        game_count = Game.objects.filter(efforts__player=profile).distinct().count()
        scorecard_count = ScoreCard.objects.filter(recorder=profile, final=True).count()
        unassigned_scorecards = ScoreCard.objects.filter(final=False, recorder=profile).count()
        bookmarked_games = profile.bookmarkedgames.count()
        bookmarked_posts = profile.bookmarkedposts.count()
        bookmarks = bookmarked_posts + bookmarked_games
        # theme = profile.theme

    else:
        post_count = 0
        recent_posts = 0
        in_process_games = 0
        game_count = 0
        scorecard_count = 0
        unassigned_scorecards = 0
        bookmarks = 0
        # theme = config.default_theme

    theme = get_theme(request)

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
        'theme': theme,
        'global_message': global_message,
        'global_message_type': global_message_type,

    }