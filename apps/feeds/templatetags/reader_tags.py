from datetime import timedelta

from django import template
from django.utils import dateformat, timezone
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def readerdate(value):
    """Compact Reader-style date: 14:05 today, 7 Nov this year, 7 Nov 2024 before."""
    if not value:
        return ""
    local, now = timezone.localtime(value), timezone.localtime()
    if local.date() == now.date():
        return dateformat.format(local, "H:i")
    if local.year == now.year:
        return dateformat.format(local, "j M")
    return dateformat.format(local, "j M Y")


@register.filter
def readerdatetime(value):
    if not value:
        return ""
    local, now = timezone.localtime(value), timezone.localtime()
    fmt = "j M, H:i" if local.year == now.year else "j M Y, H:i"
    return dateformat.format(local, fmt)


@register.filter
def unreadcount(n):
    if not n:
        return ""
    return "1000+" if n > 1000 else str(n)


@register.filter
def ago(value):
    """'3 days ago' rather than '3 days, 4 hours'."""
    if not value:
        return ""
    if timezone.now() - value < timedelta(minutes=1):
        return "just now"
    return f"{timesince(value, depth=1)} ago"


@register.filter
def per_week(recent_30_days):
    """Items per week, from a 30-day count."""
    rate = (recent_30_days or 0) * 7 / 30
    if rate == 0:
        return "0"
    return f"{rate:.1f}" if rate < 10 else f"{rate:.0f}"
