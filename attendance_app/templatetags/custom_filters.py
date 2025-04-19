from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Return the value for the key in a dictionary."""
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def first_letter(value):
    """Return the first letter of the value."""
    if value and len(value) > 0:
        return value[0]
    return ""