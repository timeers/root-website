from django import template

register = template.Library()

def int_to_roman(num):
    val = [
        1000, 900, 500, 400,
        100, 90, 50, 40,
        10, 9, 5, 4, 1
    ]
    syms = [
        "M", "CM", "D", "CD",
        "C", "XC", "L", "XL",
        "X", "IX", "V", "IV", "I"
    ]
    roman_num = ''
    for i in range(len(val)):
        count = int(num / val[i])
        roman_num += syms[i] * count
        num -= val[i] * count
    return roman_num

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