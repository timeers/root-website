# Context Processors
from the_keep.models import Post
from the_warroom.models import Game, ScoreCard

def active_user_data(request):
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
        return {
            'user_posts_count': post_count,
            'user_recent_posts': recent_posts,
            'user_in_process_games': in_process_games,
            'user_games_count': game_count,
            'user_unassigned_scorecards_count': unassigned_scorecards,
            'user_bookmarks_count': bookmarks,
            'theme': theme,
        }
    return {
        'user_posts_count': 0,
        'user_recent_posts': 0,
        'user_in_process_games': 0,
        'user_games_count': 0,
        'user_unassigned_scorecards_count': 0,
        'user_bookmarks_count': 0,
        'theme': None,
    }