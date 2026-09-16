from django import template

from the_gatehouse.services.markdown_utils import render_description_markdown

register = template.Library()


@register.filter(name='render_markdown')
def render_markdown(value):
    return render_description_markdown(value)
