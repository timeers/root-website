# Context Processors
from the_keep.models import Post
from the_warroom.models import Game, ScoreCard
from .models import Website

def active_user_data(request):

    config = Website.get_singular_instance()
    site_title = config.site_title
    global_message = config.global_message
    global_message_type = config.message_type
    post_count = 0
    recent_posts = 0
    in_process_games = 0
    game_count = 0
    unassigned_scorecards = 0
    bookmarks = 0

    # You can add any data you want to be available in templates here
    if request.user.is_authenticated:
        
        profile = request.user.profile
        post_count = Post.objects.filter(designer=profile).count()
        recent_posts = Post.objects.filter(designer=profile).order_by('-date_updated')[:3]
        in_process_games = Game.objects.filter(final=False, recorder=profile)
        game_count = Game.objects.filter(efforts__player=profile).distinct().count()
        unassigned_scorecards = ScoreCard.objects.filter(effort=None, recorder=profile)
        bookmarked_games = profile.bookmarkedgames.count()
        bookmarked_posts = profile.bookmarkedposts.count()
        bookmarks = bookmarked_posts + bookmarked_games
        theme = profile.theme

    else:

        theme = config.default_theme

    return {
        'site_title': site_title,
        'user_posts_count': post_count,
        'user_recent_posts': recent_posts,
        'user_in_process_games': in_process_games,
        'user_games_count': game_count,
        'user_unassigned_scorecards_count': unassigned_scorecards,
        'user_bookmarks_count': bookmarks,
        'theme': theme,
        'global_message': global_message,
        'global_message_type': global_message_type,
    }