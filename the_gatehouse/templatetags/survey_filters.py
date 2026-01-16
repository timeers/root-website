from django import template
import json

register = template.Library()

@register.filter
def make_list_range(min_val, max_val):
    """Create a range from min_val to max_val (inclusive)"""
    return range(int(min_val), int(max_val) + 1)

@register.filter
def to_json(value):
    """Convert Python object to JSON string"""
    return json.dumps(value)

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key"""
    if not dictionary:
        return None
    # Convert key to string since JSON keys are strings
    return dictionary.get(str(key))

@register.filter
def get_form_field(form, field_name):
    """Get a form field by name"""
    return form.fields.get(field_name) if hasattr(form, 'fields') else None

@register.filter
def get_question_field(form, question_id):
    """Get a form field for a specific question ID"""
    field_name = f'question_{question_id}'
    try:
        return form[field_name]
    except (KeyError, AttributeError):
        return None
