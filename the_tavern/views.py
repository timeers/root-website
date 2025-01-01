from django.shortcuts import render, redirect, get_object_or_404
from .forms import GameCommentCreateForm, PostCommentCreateForm
from django.contrib.auth.decorators import login_required
from the_warroom.models import Game
from the_keep.models import Post
from .models import GameComment, PostComment
from django.contrib import messages
from django.views.decorators.http import require_http_methods

from django.http import HttpResponse



@login_required
def game_comment_sent(request, pk):
    game = get_object_or_404(Game, id=pk)

    if request.method == 'POST':
        form = GameCommentCreateForm(request.POST)
        if form.is_valid:
            comment = form.save(commit=False)
            comment.player = request.user.profile
            comment.game = game
            comment.save()
    return render(request, 'snippets/add_comment.html', {'comment': comment, 'object': game})

@login_required
@require_http_methods(['DELETE'])
def game_comment_delete(request, pk):
    comment = get_object_or_404(GameComment, id=pk, player=request.user.profile)
    comment.delete()

    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'delete-comment'
    return response


@login_required
def post_comment_sent(request, pk):
    post = get_object_or_404(Post, id=pk)

    if request.method == 'POST':
        form = PostCommentCreateForm(request.POST)
        if form.is_valid:
            comment = form.save(commit=False)
            comment.player = request.user.profile
            comment.post = post
            comment.save()
    return render(request, 'snippets/add_comment.html', {'comment': comment, 'object': post})

# @login_required
# def post_comment_delete(request, pk):
#     comment = get_object_or_404(PostComment, id=pk, player=request.user.profile)
#     component = comment.post.component

#     if request.method == 'POST':
#         comment.delete()
#         messages.success(request, 'Comment deleted')
#         return redirect(f'{component.lower()}-detail', comment.post.slug)
#     return render(request, 'the_tavern/post_comment_delete.html', {'comment': comment})


@login_required
@require_http_methods(['DELETE'])
def post_comment_delete(request, pk):
    comment = get_object_or_404(PostComment, id=pk, player=request.user.profile)
    comment.delete()

    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'delete-comment'
    return response

def bookmark_toggle(model):
    def inner_func(func):
        def wrapper(request, *args, **kwargs):
            object = get_object_or_404(model, id=kwargs.get('id'))
            player_exists = object.bookmarks.filter(discord=request.user.profile.discord).exists()
            if player_exists:
                object.bookmarks.remove(request.user.profile)
            else:
                object.bookmarks.add(request.user.profile)
            return func(request, object)
        return wrapper
    return inner_func

