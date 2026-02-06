from django import template
from the_gatehouse.utils import int_to_roman

register = template.Library()

@register.filter
def roman(value):
    try:
        return int_to_roman(int(value))
    except (ValueError, TypeError):
        return ''


# Converts integer to lowercase alphabetic (a, b, ..., z, aa, ab, ...)
def int_to_letters(n):
    if n < 1:
        return ''
    result = ''
    while n > 0:
        n -= 1
        result = chr(97 + (n % 26)) + result
        n //= 26
    return result

@register.filter
def alpha(value):
    try:
        return int_to_letters(int(value))
    except (ValueError, TypeError):
        return ''