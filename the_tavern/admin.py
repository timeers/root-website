from django.contrib import admin
from .models import PostComment, GameComment, Survey, Question, Choice, LikertScale, SurveyResponse, Answer, RankedAnswer, QuestionTemplate


class PostCommentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'post', 'post__component', 'public')
    search_fields = ['post__title', 'player__discord', 'player__dwd', 'player__display_name', 'body']

class GameCommentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'public')
    search_fields = ['player__discord', 'player__dwd', 'player__display_name', 'body']    


# Survey Admin Configuration

class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 1
    fields = ['text', 'order']
    ordering = ['order']

class QuestionInline(admin.StackedInline):
    model = Question
    extra = 1
    fields = ['text', 'question_type', 'likert_scale', 'order', 'required', 'help_text']
    ordering = ['order']

class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0
    readonly_fields = ['question', 'get_answer_display']
    fields = ['question', 'get_answer_display']
    can_delete = False

    def get_answer_display(self, obj):
        return obj.get_display_value()
    get_answer_display.short_description = 'Answer'

class LikertScaleAdmin(admin.ModelAdmin):
    list_display = ['name', 'min_value', 'max_value', 'min_label', 'max_label']
    search_fields = ['name']

class SurveyAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active', 'created_at', 'start_date', 'end_date', 'response_count', 'created_by']
    list_filter = ['is_active', 'is_public', 'created_at']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [QuestionInline]
    readonly_fields = ['created_at', 'response_count']

    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'description', 'slug', 'created_by')
        }),
        ('Availability', {
            'fields': ('is_active', 'start_date', 'end_date')
        }),
        ('Settings', {
            'fields': ('is_public', 'is_pinned','allow_multiple_responses', 'show_results_to_respondents', 'show_results_on_close')
        }),
        ('Meta', {
            'fields': ('created_at', 'response_count'),
            'classes': ('collapse',)
        }),
    )

    def response_count(self, obj):
        return obj.responses.count()
    response_count.short_description = 'Responses'

class QuestionAdmin(admin.ModelAdmin):
    list_display = ['survey', 'text_preview', 'question_type', 'order', 'required']
    list_filter = ['survey', 'question_type', 'required']
    search_fields = ['text', 'survey__title']
    inlines = [ChoiceInline]

    def text_preview(self, obj):
        return obj.text[:50] + '...' if len(obj.text) > 50 else obj.text
    text_preview.short_description = 'Question'

class ChoiceAdmin(admin.ModelAdmin):
    list_display = ['question_preview', 'text', 'post__title', 'order']
    list_filter = ['question__survey']
    search_fields = ['text', 'question__text']

    def question_preview(self, obj):
        return obj.question.text[:30] + '...' if len(obj.question.text) > 30 else obj.question.text
    question_preview.short_description = 'Question'

class SurveyResponseAdmin(admin.ModelAdmin):
    list_display = ['user', 'survey', 'submitted_at', 'answer_count']
    list_filter = ['survey', 'submitted_at']
    search_fields = ['user__display_name', 'user__discord', 'survey__title']
    readonly_fields = ['survey', 'user', 'submitted_at', 'updated_at']
    inlines = [AnswerInline]

    def answer_count(self, obj):
        return obj.answers.count()
    answer_count.short_description = 'Answers'

    def has_add_permission(self, request):
        # Prevent adding responses through admin
        return False

class AnswerAdmin(admin.ModelAdmin):
    list_display = ['response', 'question_preview', 'answer_preview']
    list_filter = ['response__survey', 'question__question_type']
    search_fields = ['response__user__display_name', 'question__text']
    readonly_fields = ['response', 'question', 'get_display_value']

    def question_preview(self, obj):
        return obj.question.text[:30] + '...' if len(obj.question.text) > 30 else obj.question.text
    question_preview.short_description = 'Question'

    def answer_preview(self, obj):
        value = obj.get_display_value()
        return value[:50] + '...' if len(value) > 50 else value
    answer_preview.short_description = 'Answer'

    def has_add_permission(self, request):
        return False

class RankedAnswerAdmin(admin.ModelAdmin):
    list_display = ['answer', 'choice', 'rank']
    list_filter = ['answer__response__survey']
    readonly_fields = ['answer', 'choice', 'rank']

    def has_add_permission(self, request):
        return False

class QuestionTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'question_type', 'is_public', 'created_by', 'created_at']
    list_filter = ['question_type', 'is_public', 'created_at']
    search_fields = ['name', 'text']
    readonly_fields = ['created_at']

    fieldsets = (
        ('Template Information', {
            'fields': ('name', 'text', 'question_type', 'likert_scale', 'ta_enabled_days', 'help_text', 'required')
        }),
        ('Choices', {
            'fields': ('choices_data',),
            'description': 'For multiple choice questions, enter a JSON list of choice texts'
        }),
        ('Post Choices', {
            'fields': ('post_component', 'post_selection_mode', 'post_choices',),
            'description': 'For questions with post options'
        }),
        ('Settings', {
            'fields': ('is_public', 'created_by', 'created_at')
        }),
    )


# Register survey models
admin.site.register(LikertScale, LikertScaleAdmin)
admin.site.register(Survey, SurveyAdmin)
admin.site.register(Question, QuestionAdmin)
admin.site.register(Choice, ChoiceAdmin)
admin.site.register(SurveyResponse, SurveyResponseAdmin)
admin.site.register(Answer, AnswerAdmin)
admin.site.register(RankedAnswer, RankedAnswerAdmin)
admin.site.register(QuestionTemplate, QuestionTemplateAdmin)
admin.site.register(PostComment, PostCommentAdmin)
admin.site.register(GameComment, GameCommentAdmin)