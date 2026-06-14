from django import template
import json

register = template.Library()

@register.filter(name='times') 
def times(number):
    try:  return range(number)  
    except:  return []
    


@register.filter(name='json_encode')
def json_encode(value):
    return json.dumps(value)


@register.filter(name='cache_bust')
def cache_bust(image_field, version=0):
    """Return an ImageField's URL with a ?v=<version> cache-busting param.

    Reusable for any image + version-integer pair (e.g. small_icon +
    small_icon_version, piece.small_icon + front_version). Reads the version
    that's already loaded with the row, so it adds no storage or DB cost.
    Returns '' for an empty/missing field so unguarded templates don't error.
    """
    if not image_field:
        return ''
    try:
        return f"{image_field.url}?v={version or 0}"
    except (ValueError, AttributeError):
        return ''
