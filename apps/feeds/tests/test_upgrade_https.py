from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from apps.feeds.models import Feed
from apps.feeds.services.http import FetchError, HttpResult

from .helpers import RSS


def fake_get(url, **kwargs):
    if "good.example" in url or "dupe.example" in url:
        return HttpResult(status=200, url=url, headers={"content-type": "application/rss+xml"}, content=RSS)
    if "html.example" in url:
        return HttpResult(status=200, url=url, headers={"content-type": "text/html"}, content=b"<html></html>")
    raise FetchError("Could not connect")


@mock.patch("apps.feeds.management.commands.upgrade_https.http.get", side_effect=fake_get)
class UpgradeHttpsTests(TestCase):
    def setUp(self):
        self.good = Feed.objects.create(url="http://good.example/feed", etag="x", error_count=3)
        self.html = Feed.objects.create(url="http://html.example/feed")
        self.down = Feed.objects.create(url="http://down.example/feed")
        self.dupe = Feed.objects.create(url="http://dupe.example/feed")
        Feed.objects.create(url="https://dupe.example/feed")

    def run_cmd(self, *args):
        out = StringIO()
        call_command("upgrade_https", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self, _):
        out = self.run_cmd("--dry-run")
        self.assertIn("Would upgrade 1", out)
        self.good.refresh_from_db()
        self.assertEqual(self.good.url, "http://good.example/feed")

    def test_upgrades_only_working_feeds(self, _):
        self.run_cmd()
        for feed in (self.good, self.html, self.down, self.dupe):
            feed.refresh_from_db()
        self.assertEqual(self.good.url, "https://good.example/feed")
        self.assertEqual((self.good.etag, self.good.error_count), ("", 0))
        self.assertEqual(self.html.url, "http://html.example/feed")
        self.assertEqual(self.down.url, "http://down.example/feed")
        self.assertEqual(self.dupe.url, "http://dupe.example/feed")  # https twin already exists
