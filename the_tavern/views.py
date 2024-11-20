from django.shortcuts import render, redirect, get_object_or_404
from .forms import GameCommentCreateForm, PostCommentCreateForm
from django.contrib.auth.decorators import login_required
from the_warroom.models import Game
from the_keep.models import Post
from .models import GameComment, PostComment
from django.contrib import messages

# Create your views here.

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
    return redirect('game-detail', game.id)

@login_required
def game_comment_delete(request, pk):
    comment = get_object_or_404(GameComment, id=pk, player=request.user.profile)
    context = {
        'comment': comment
    }

    if request.method == 'POST':
        comment.delete()
        messages.success(request, 'Comment deleted')
        return redirect('game-detail', comment.game.id)
    return render(request, 'the_tavern/game_comment_delete.html', context)


@login_required
def post_comment_sent(request, pk):
    post = get_object_or_404(Post, id=pk)
    component = post.component

    if request.method == 'POST':
        form = PostCommentCreateForm(request.POST)
        if form.is_valid:
            comment = form.save(commit=False)
            comment.player = request.user.profile
            comment.post = post
            comment.save()
    return redirect(f'{component.lower()}-detail', post.slug)

@login_required
def post_comment_delete(request, pk):
    comment = get_object_or_404(PostComment, id=pk, player=request.user.profile)
    component = comment.post.component

    if request.method == 'POST':
        comment.delete()
        messages.success(request, 'Comment deleted')
        return redirect(f'{component.lower()}-detail', comment.post.slug)
    return render(request, 'the_tavern/post_comment_delete.html', {'comment': comment})