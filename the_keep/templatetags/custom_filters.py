from django import template

register = template.Library()

@register.filter(name='times') 
def times(number):
    try:  return range(number)  
    except:  return []
    

# @register.filter(name='range') 
# def filter_range(start, end):   
#     return range(start, end)