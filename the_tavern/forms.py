from django import forms
from .models import GameComment, PostComment

class GameCommentCreateForm(forms.ModelForm):
    class Meta:
        model = GameComment
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

class PostCommentCreateForm(forms.ModelForm):
    class Meta:
        model = PostComment
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

class CommentCreateForm(forms.ModelForm):
    class Meta:
        model = None  # Set this dynamically later
        fields = ['body']
        widgets = {
            'body': forms.TextInput(attrs={'placeholder': 'Add note...'})
        }
        labels = {
            'body': ''
        }

    def set_model(self, model):
        """Method to set the model for the form dynamically."""
        self.Meta.model = model