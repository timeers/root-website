from django import template
import json

register = template.Library()

@register.simple_tag(takes_context=True)
def filters_active(context):
    """Return True when the request has real filter params applied.

    Ignores pagination (`page`) and the forced `official` default so the filter
    panel only auto-opens / shows its active indicator when the user has actually
    filtered. Used by partials/universal_game_filter.html.
    """
    request = context.get('request')
    if not request:
        return False
    return any(key not in ('page', 'official') for key in request.GET.keys())


@register.filter(name='intcomma')
def intcomma(value):
    """Add thousands separators to a number, preserving any decimal part.

    e.g. 1234567 -> '1,234,567' and 1234.56 -> '1,234.56'. Like humanize's
    intcomma, it groups the integer part and leaves the fractional part as-is
    (no rounding). Opt-in per value so it never affects raw numeric IDs
    elsewhere (unlike the global USE_THOUSAND_SEPARATOR setting). Returns the
    value unchanged if it isn't a number.
    """
    try:
        number = float(value)
    except (ValueError, TypeError):
        return value
    if number == int(number):
        return f"{int(number):,}"
    int_part, _, frac_part = str(value).strip().lstrip('-').partition('.')
    sign = '-' if str(value).strip().startswith('-') else ''
    return f"{sign}{int(int_part):,}.{frac_part}"


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
