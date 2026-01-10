from django.db.models import Prefetch
from the_keep.models import FAQ, Law, PostTranslation


def faq_queryset2(*, language, require_post=True):


    """
    Base FAQ queryset with optimized joins.

    - If require_post=True:
        - filters post__isnull=False
        - select_related post + expansion
        - prefetch post translations
    - Always prefetch reference laws
    """

    qs = FAQ.objects.filter(language=language)

    if require_post:
        qs = qs.filter(post__isnull=False).select_related(
            'post',
            'post__expansion',
        )
    else:
        qs = qs.filter(post__isnull=True)

    prefetches = [
        Prefetch(
            'reference_laws',
            queryset=Law.objects.select_related(
                'group',
                'language',
            )
        )
    ]

    if require_post:
        prefetches.append(
            Prefetch(
                'post__translations',
                queryset=PostTranslation.objects.filter(language=language),
                to_attr='filtered_translations'
            )
        )

    return qs.prefetch_related(*prefetches)