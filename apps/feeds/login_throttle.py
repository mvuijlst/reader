"""Slow down password guessing.

After MAX_FAILURES failed sign-ins from one address within WINDOW, further
attempts from that address are refused without the password being checked at
all, until the oldest of those failures is WINDOW old. A successful sign-in
clears the address's failures. It covers /login/ and the admin's sign-in page.

It counts per address, not per username: with one user, locking the username
would let anyone lock its owner out.
"""
import ipaddress
import logging
import math
from datetime import timedelta

from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import LoginFailure

MAX_FAILURES = 10
WINDOW = timedelta(minutes=15)

# gunicorn listens on 127.0.0.1 behind nginx, so every real request arrives from
# there and nginx passes the browser's address in X-Real-IP (deploy/nginx-reader.conf).
# From anywhere else that header could be forged, so it only counts from here.
_PROXY_ADDRESSES = {"127.0.0.1", "::1"}

logger = logging.getLogger(__name__)


def client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "")
    if remote in _PROXY_ADDRESSES:
        forwarded = request.META.get("HTTP_X_REAL_IP", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return remote


class ThrottledLoginMixin:
    def clean(self):
        ip = client_ip(self.request) if self.request else ""
        now = timezone.now()
        recent = LoginFailure.objects.filter(ip=ip, at__gte=now - WINDOW)
        if recent.count() >= MAX_FAILURES:
            oldest = recent.order_by("at").first()
            minutes = max(1, math.ceil((oldest.at + WINDOW - now).total_seconds() / 60))
            logger.warning("Sign-in refused for %s: %d failed attempts in %s", ip, MAX_FAILURES, WINDOW)
            raise ValidationError(
                "Too many failed sign-ins from your address. Try again in %(minutes)s minute%(s)s.",
                code="throttled",
                params={"minutes": minutes, "s": "" if minutes == 1 else "s"},
            )
        try:
            cleaned = super().clean()
        except ValidationError:
            # The password was checked and didn't do (or the account can't sign in).
            LoginFailure.objects.create(ip=ip)
            LoginFailure.objects.filter(at__lt=now - WINDOW).delete()
            raise
        # A missing username or password also gets here, with no user: only a
        # real sign-in clears the slate.
        if self.get_user() is not None:
            recent.delete()
        return cleaned


class ThrottledAuthenticationForm(ThrottledLoginMixin, AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "That username and password didn't match.",
    }


class ThrottledAdminAuthenticationForm(ThrottledLoginMixin, AdminAuthenticationForm):
    pass
