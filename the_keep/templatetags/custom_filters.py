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
