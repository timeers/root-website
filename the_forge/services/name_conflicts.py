from the_keep.models import Faction, PostTranslation


def find_faction_name_conflicts(name, profile):
    name = (name or '').strip()
    if not name:
        return []
    conflicts = []

    factions = (Faction.objects
        .filter(title__iexact=name, component='Faction')
        .exclude(designer=profile)
        .exclude(co_designers=profile)
        .select_related('designer')
        .distinct())
    for f in factions:
        conflicts.append({
            'label': f.title,
            'designer_name': f.designers_list or (f.designer.name if f.designer else 'Unknown'),
        })

    translations = (PostTranslation.objects
        .filter(translated_title__iexact=name, post__component='Faction')
        .exclude(designer=profile)
        .exclude(post__designer=profile)
        .exclude(post__co_designers=profile)
        .select_related('post', 'post__designer', 'designer')
        .distinct())
    for t in translations:
        conflicts.append({
            'label': t.translated_title,
            'designer_name': (t.post.designers_list
                              or (t.post.designer.name if t.post.designer else 'Unknown')),
        })

    return conflicts
