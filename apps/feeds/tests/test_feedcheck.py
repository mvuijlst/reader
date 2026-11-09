"""Pruning dead feeds out of an OPML file (services/feedcheck.py, prune_opml)."""
import csv
import socket
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.feeds.services import feedcheck, http
from apps.feeds.services.feedcheck import DEAD, FOUND, OK, UNSURE, Check, check_feed, titles_match

from .helpers import HTML_WITH_FEEDS, RSS, fake_web, response

OLD = "https://feeds.example.net/example"
SITE = "https://example.com/"
NEW = "https://example.com/feed.xml"


def fails(message):
    def get(headers):
        raise http.FetchError(message)
    return get


class CheckFeedTests(SimpleTestCase):
    def test_working_feed(self):
        with fake_web({OLD: response(RSS, url=OLD)}):
            check = check_feed(OLD, SITE, "Example")
        self.assertEqual(check.verdict, OK)
        self.assertEqual(check.new_url, "")
        self.assertEqual(check.title, "Example & Co")
        self.assertEqual(check.entries, 3)
        self.assertEqual(check.newest, datetime(2026, 11, 3, 10, tzinfo=timezone.utc))

    def test_permanent_redirect_updates_the_address(self):
        with fake_web({OLD: response(RSS, url=NEW, permanent_url=NEW)}):
            check = check_feed(OLD, SITE, "Example")
        self.assertEqual((check.verdict, check.new_url), (OK, NEW))

    def test_404_without_a_site_is_dead(self):
        with fake_web({}):
            check = check_feed(OLD, "", "Example")
        self.assertEqual((check.verdict, check.detail), (DEAD, "HTTP 404"))

    def test_gone_feed_is_replaced_by_the_sites_feed_of_the_same_name(self):
        routes = {SITE: response(HTML_WITH_FEEDS, url=SITE), NEW: response(RSS, url=NEW)}
        with fake_web(routes):
            check = check_feed(OLD, SITE, "Example & Co")
        self.assertEqual((check.verdict, check.new_url), (FOUND, NEW))
        self.assertIn("HTTP 404", check.detail)

    def test_a_differently_named_feed_on_the_old_site_is_not_taken_for_it(self):
        # e.g. a lapsed domain that now hosts someone else's blog
        routes = {SITE: response(HTML_WITH_FEEDS, url=SITE), NEW: response(RSS, url=NEW)}
        with fake_web(routes):
            check = check_feed(OLD, SITE, "Garden diary")
        self.assertEqual(check.verdict, DEAD)
        self.assertIn("different feed", check.detail)

    def test_address_that_no_longer_serves_a_feed_is_dead(self):
        with fake_web({OLD: response(b"<html><body>Parked</body></html>", url="https://parking.example/")}):
            check = check_feed(OLD, "", "Example")
        self.assertEqual(check.verdict, DEAD)
        self.assertIn("no feed there any more", check.detail)

    def test_domain_that_no_longer_exists_is_dead(self):
        with fake_web({OLD: fails("Could not connect: Failed to resolve")}), \
             mock.patch.object(feedcheck, "domain_exists", return_value=False):
            check = check_feed(OLD, "", "Example")
        self.assertEqual((check.verdict, check.detail), (DEAD, "feeds.example.net no longer exists"))

    def test_inconclusive_answers_are_unsure(self):
        cases = {
            "timeout": fails("Timed out"),
            "server error": response(b"oops", status=503, url=OLD),
            "forbidden": response(b"no", status=403, url=OLD),
            "bot wall": response(b"Just a moment...", status=403, url=OLD, headers={"cf-mitigated": "challenge"}),
        }
        for name, route in cases.items():
            with self.subTest(name), fake_web({OLD: route}), \
                 mock.patch.object(feedcheck, "domain_exists", return_value=True):
                self.assertEqual(check_feed(OLD, SITE, "Example").verdict, UNSURE)

    def test_domain_exists_only_says_no_when_dns_does(self):
        gone = socket.gaierror(socket.EAI_NONAME, "not found")
        trouble = socket.gaierror(socket.EAI_AGAIN, "try again")
        with mock.patch("socket.getaddrinfo", side_effect=gone):
            self.assertFalse(feedcheck.domain_exists("gone.example"))
        with mock.patch("socket.getaddrinfo", side_effect=trouble):
            self.assertTrue(feedcheck.domain_exists("flaky.example"))

    def test_titles_match(self):
        self.assertTrue(titles_match("Daring Fireball", "Daring Fireball"))
        self.assertTrue(titles_match("studio42", "Studio 42"))
        self.assertTrue(titles_match("Anna's recipes", "Recipes by Anna"))
        self.assertTrue(titles_match("Neighbourhood", "Neighborhood"))  # spelling variant
        self.assertFalse(titles_match("My Thoughts", "Best casino bonuses"))
        self.assertFalse(titles_match("Pixel Weekly", "BETWIN88"))
        self.assertFalse(titles_match("Blog", "Casino blog"))  # generic words don't count
        self.assertFalse(titles_match("", "Anything"))


OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0"><head><title>Subscriptions</title></head><body>
<outline text="Friends" title="Friends">
  <outline type="rss" text="Alive" title="Alive" xmlUrl="https://alive.example/feed" htmlUrl="https://alive.example/"/>
  <outline type="rss" text="Gone" title="Gone" xmlUrl="https://gone.example/feed"/>
</outline>
<outline text="Old stuff" title="Old stuff">
  <outline type="rss" text="Also gone" title="Also gone" xmlUrl="https://gone2.example/feed"/>
</outline>
<outline text="Empty" title="Empty"/>
<outline type="rss" text="Moved" title="Moved" xmlUrl="http://moved.example/rss"/>
<outline type="rss" text="Quiet" title="Quiet" xmlUrl="https://quiet.example/feed"/>
<outline type="rss" text="Flaky" title="Flaky" xmlUrl="https://flaky.example/feed"/>
<outline type="rss" text="Alive again" title="Alive again" xmlUrl="https://alive.example/feed"/>
<outline type="rss" text="Moved again" title="Moved again" xmlUrl="http://moved.example/rss"/>
<outline text="Magazine" title="Magazine">
  <outline type="rss" text="Mag: Design" title="Mag: Design" xmlUrl="https://mag.example/design/feed"/>
  <outline type="rss" text="Mag: Type" title="Mag: Type" xmlUrl="https://mag.example/type/feed"/>
</outline>
<outline type="rss" text="Mag main" title="Mag main" xmlUrl="http://mag.example/feed"/>
</body></opml>"""

CHECKS = {
    "https://alive.example/feed": Check(OK, newest=datetime(2026, 10, 1, tzinfo=timezone.utc), entries=5),
    "https://gone.example/feed": Check(DEAD, "HTTP 404"),
    "https://gone2.example/feed": Check(DEAD, "gone2.example no longer exists"),
    "http://moved.example/rss": Check(OK, "moved permanently", new_url="https://moved.example/rss",
                                      newest=datetime(2026, 9, 1, tzinfo=timezone.utc), entries=2),
    "https://quiet.example/feed": Check(OK, newest=datetime(2015, 1, 1, tzinfo=timezone.utc), entries=9),
    "https://flaky.example/feed": Check(UNSURE, "Timed out"),
    # two dead section feeds whose site now has one main feed
    "https://mag.example/design/feed": Check(FOUND, "HTTP 404", new_url="https://mag.example/feed", entries=4),
    "https://mag.example/type/feed": Check(FOUND, "HTTP 404", new_url="https://mag.example/feed", entries=4),
    "http://mag.example/feed": Check(OK, "moved permanently", new_url="https://mag.example/feed", entries=4),
}


class PruneOpmlCommandTests(TestCase):  # TestCase: the system checks touch the database
    def run_command(self, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "subs.opml"
            source.write_text(OPML, encoding="utf-8")
            calls = []

            def fake_check(url, site, title):
                calls.append(url)
                return CHECKS[url]

            with mock.patch.object(feedcheck, "check_feed", side_effect=fake_check):
                # skip_checks=False: run it the way manage.py does, system checks included
                call_command("prune_opml", str(source), *flags, "--workers", "2",
                             stdout=StringIO(), skip_checks=False)
            self.assertEqual(source.read_text(encoding="utf-8"), OPML)  # original untouched
            body = ET.fromstring((Path(tmp) / "subs.pruned.opml").read_bytes()).find("body")
            with open(Path(tmp) / "subs.report.csv", encoding="utf-8-sig", newline="") as fh:
                report = list(csv.DictReader(fh))
        feeds = {el.get("title"): el.get("xmlUrl") for el in body.iter("outline") if el.get("xmlUrl")}
        folders = [el.get("title") for el in body.findall("outline") if not el.get("xmlUrl")]
        return feeds, folders, report, calls

    def test_removes_dead_feeds_and_the_folders_they_leave_empty(self):
        feeds, folders, report, calls = self.run_command()
        self.assertEqual(feeds, {
            "Alive": "https://alive.example/feed",
            "Moved": "https://moved.example/rss",  # permanent redirect: new address
            "Quiet": "https://quiet.example/feed",  # dormant feeds stay unless asked
            "Flaky": "https://flaky.example/feed",  # unsure: kept
            # the file listed these twice itself: that stays as it was
            "Alive again": "https://alive.example/feed",
            "Moved again": "https://moved.example/rss",
            # two dead sections switch to the main feed, which the file has too
            "Mag main": "https://mag.example/feed",
        })
        # "Old stuff" lost its only feed; "Magazine" its sections (duplicates now)
        self.assertEqual(folders, ["Friends", "Empty"])
        self.assertEqual(calls.count("https://flaky.example/feed"), 2)  # unsure ones get a second try
        self.assertEqual(calls.count("http://moved.example/rss"), 1)  # each address checked once
        by_title = {row["title"]: row for row in report}
        self.assertEqual(len(report), 11)
        self.assertEqual((by_title["Gone"]["action"], by_title["Gone"]["why"]), ("removed", "HTTP 404"))
        self.assertEqual(by_title["Moved"]["action"], "new address")
        self.assertEqual(by_title["Mag main"]["action"], "new address")  # yours wins, though it comes later
        self.assertEqual(by_title["Mag: Design"]["action"], "removed (duplicate)")
        self.assertEqual(by_title["Mag: Type"]["action"], "removed (duplicate)")
        self.assertEqual(by_title["Quiet"]["latest post"], "2015-01-01")
        self.assertEqual(report[0]["verdict"], DEAD)  # removals first

    def test_switched_feeds_landing_on_the_same_address_keep_one(self):
        with mock.patch.dict(CHECKS, {"http://mag.example/feed": Check(DEAD, "HTTP 404")}):
            feeds, folders, _, _ = self.run_command()
        self.assertEqual(feeds["Mag: Design"], "https://mag.example/feed")
        self.assertNotIn("Mag: Type", feeds)
        self.assertIn("Magazine", folders)

    def test_optional_drops(self):
        feeds, _, report, _ = self.run_command("--drop-dormant", "5", "--drop-unsure")
        self.assertEqual(set(feeds), {"Alive", "Moved", "Alive again", "Moved again", "Mag main"})
        by_title = {row["title"]: row for row in report}
        self.assertEqual((by_title["Quiet"]["verdict"], by_title["Quiet"]["action"]), ("dormant", "removed"))
        self.assertEqual(by_title["Flaky"]["action"], "removed")
