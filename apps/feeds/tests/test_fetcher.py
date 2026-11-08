from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.feeds.models import Feed, Item
from apps.feeds.services import discovery, fetcher, opml
from apps.feeds.services.sanitize import clean_html, first_image

from .helpers import HTML_FORMAT_VARIANTS, HTML_WITH_FEEDS, PNG, RSS, fake_web, response

FEED_URL = "https://example.com/feed.xml"


@override_settings(READER_AUTO_READ_DAYS=30, READER_KEEP_DAYS=180)
class FetchTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(url=FEED_URL)

    def routes(self, **extra):
        routes = {
            FEED_URL: response(RSS, url=FEED_URL, headers={"ETag": '"v1"'}),
            "https://example.com/favicon.ico": response(PNG),
        }
        routes.update(extra)
        return routes

    def test_fetch_stores_items_and_feed_metadata(self):
        with fake_web(self.routes()):
            stats = fetcher.fetch_feeds([self.feed])
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.title, "Example & Co")
        self.assertEqual(self.feed.site_url, "https://example.com/")
        self.assertEqual(self.feed.etag, '"v1"')
        self.assertEqual(self.feed.icon_type, "image/png")
        self.assertEqual(self.feed.error_count, 0)
        self.assertEqual(Item.objects.count(), 3)
        self.assertEqual(stats.new_items, 2)  # the 2024 item arrives already read

        first = Item.objects.get(guid="https://example.com/1")
        self.assertEqual(first.title, "First post")  # markup stripped from titles
        self.assertNotIn("<script", first.content)
        self.assertNotIn("onclick", first.content)
        self.assertIn('href="https://example.com/rel"', first.content)  # made absolute
        self.assertIn('target="_blank"', first.content)
        self.assertIn('loading="lazy"', first.content)

        ancient = Item.objects.get(title="Ancient")
        self.assertTrue(ancient.is_read)

    def test_plain_text_and_enclosures(self):
        with fake_web(self.routes()):
            fetcher.fetch_feeds([self.feed])
        episode = Item.objects.get(guid="ep-2")
        self.assertIn("<br>", episode.content)
        self.assertEqual(episode.enclosures[0]["type"], "audio/mpeg")
        self.assertEqual(episode.summary, "Plain text second line")

    def test_refetch_keeps_state_and_sends_conditional_headers(self):
        with fake_web(self.routes()):
            fetcher.fetch_feeds([self.feed])
        Item.objects.filter(guid="ep-2").update(is_read=True, is_starred=True)
        self.feed.refresh_from_db()

        with fake_web(self.routes(**{FEED_URL: response(status=304, url=FEED_URL)})) as calls:
            stats = fetcher.fetch_feeds([self.feed])
        self.assertEqual(calls[0][1].get("If-None-Match"), '"v1"')
        self.assertEqual(stats.not_modified, 1)
        self.assertEqual(Item.objects.count(), 3)
        item = Item.objects.get(guid="ep-2")
        self.assertTrue(item.is_read and item.is_starred)

        # Full refetch: same items, no duplicates, state untouched.
        self.feed.refresh_from_db()
        with fake_web(self.routes()):
            stats = fetcher.fetch_feeds([self.feed])
        self.assertEqual(stats.new_items, 0)
        self.assertEqual(Item.objects.count(), 3)
        self.assertTrue(Item.objects.get(guid="ep-2").is_starred)

    def test_errors_back_off(self):
        with fake_web(self.routes(**{FEED_URL: response(status=500, url=FEED_URL)})):
            stats = fetcher.fetch_feeds([self.feed])
        self.feed.refresh_from_db()
        self.assertEqual(stats.errors, 1)
        self.assertEqual(self.feed.error_count, 1)
        self.assertEqual(self.feed.last_error, "HTTP 500")
        first_delay = self.feed.next_fetch_at - self.feed.last_fetched_at

        with fake_web(self.routes(**{FEED_URL: response(status=500, url=FEED_URL)})):
            fetcher.fetch_feeds([self.feed])
        self.feed.refresh_from_db()
        self.assertGreater(self.feed.next_fetch_at - self.feed.last_fetched_at, first_delay)

    def test_not_a_feed(self):
        with fake_web(self.routes(**{FEED_URL: response(b"<html><body>nope</body></html>", url=FEED_URL)})):
            fetcher.fetch_feeds([self.feed])
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.last_error.startswith("Not a valid feed"))

    def test_permanent_redirect_updates_url(self):
        new_url = "https://example.org/feed.xml"
        with fake_web(self.routes(**{FEED_URL: response(RSS, url=new_url, permanent_url=new_url)})):
            fetcher.fetch_feeds([self.feed])
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.url, new_url)

    def test_maintenance_auto_reads_and_purges_only_dropped_items(self):
        with fake_web(self.routes()):
            fetcher.fetch_feeds([self.feed])
        now = timezone.now()
        old = now - timedelta(days=400)
        # Still in the feed: must survive. Dropped out of the feed: purged.
        Item.objects.filter(guid="ep-2").update(published=old, is_read=True)
        Item.objects.filter(title="Ancient").update(published=old, last_seen_at=now - timedelta(days=2))
        starred = Item.objects.get(guid="https://example.com/1")
        Item.objects.filter(pk=starred.pk).update(
            published=old, is_read=False, is_starred=True, last_seen_at=now - timedelta(days=2)
        )

        auto_read, purged = fetcher.maintenance(now)
        self.assertEqual(auto_read, 1)  # the unread starred one
        self.assertEqual(purged, 1)  # "Ancient" only
        self.assertTrue(Item.objects.filter(guid="ep-2").exists())
        self.assertTrue(Item.objects.filter(pk=starred.pk).exists())


class ThumbnailTests(TestCase):
    def test_first_real_image_wins(self):
        html = (
            '<p><img src="https://feeds.feedburner.com/~r/x/~4/abc" width="1" height="1">'
            '<img src="https://s.w.org/images/core/emoji/15/svg/1f600.svg">'
            '<img src="https://example.com/icon.png" width="16" height="16">'
            '<img src="https://example.com/photo.jpg?a=1&amp;b=2" width="800"></p>'
        )
        self.assertEqual(first_image(html), "https://example.com/photo.jpg?a=1&b=2")
        self.assertEqual(first_image("<p>No pictures.</p>"), "")

    def test_fetch_sets_thumbnails(self):
        feed = Feed.objects.create(url=FEED_URL)
        with fake_web({FEED_URL: response(RSS, url=FEED_URL)}):
            fetcher.fetch_feeds([feed])
        # From the content's <img> (made absolute by feedparser).
        self.assertEqual(Item.objects.get(guid="https://example.com/1").thumbnail, "https://example.com/a.png")
        self.assertEqual(Item.objects.get(guid="ep-2").thumbnail, "https://example.com/art/ep2.jpg")  # itunes:image
        self.assertEqual(Item.objects.get(title="Ancient").thumbnail, "")


class SanitizeTests(TestCase):
    def test_strips_dangerous_markup(self):
        html = clean_html(
            '<p style="position:fixed">x</p><iframe src="https://evil"></iframe>'
            '<a href="javascript:alert(1)">y</a><img src="x" onerror="alert(1)">'
        )
        self.assertNotIn("iframe", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("onerror", html)
        self.assertNotIn("style=", html)


class DiscoveryTests(TestCase):
    def test_feed_url_itself(self):
        with fake_web({FEED_URL: response(RSS, url=FEED_URL)}):
            found = discovery.discover(FEED_URL)
        self.assertEqual([c.url for c in found], [FEED_URL])
        self.assertEqual(found[0].title, "Example & Co")

    def test_links_on_page_comments_last(self):
        with fake_web({"https://example.com": response(HTML_WITH_FEEDS, url="https://example.com/")}):
            found = discovery.discover("example.com")
        self.assertEqual([c.url for c in found], ["https://example.com/feed.xml", "https://example.com/comments.xml"])

    def test_format_variants_collapse_to_xml(self):
        with fake_web({"https://example.com": response(HTML_FORMAT_VARIANTS, url="https://example.com/")}):
            found = discovery.discover("https://example.com")
        self.assertEqual([c.url for c in found], ["https://example.com/atom.xml"])

    def test_common_path_fallback(self):
        routes = {
            "https://example.com": response(b"<html></html>", url="https://example.com/"),
            "https://example.com/rss": response(RSS, url="https://example.com/rss"),
        }
        with fake_web(routes):
            found = discovery.discover("https://example.com")
        self.assertEqual([c.url for c in found], ["https://example.com/rss"])

    def test_youtube_channel_shortcut(self):
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw"
        with fake_web({url: response(RSS, url=url)}):
            found = discovery.discover("https://www.youtube.com/channel/UCYO_jab_esuFRV4b17AJtAw")
        self.assertEqual(found[0].url, url)

    def test_rejects_non_web_urls(self):
        with self.assertRaises(discovery.DiscoveryError):
            discovery.discover("ftp://example.com/feed")


class OPMLTests(TestCase):
    def test_round_trip(self):
        data = b"""<?xml version="1.0"?><opml version="1.0"><body>
          <outline text="Tech"><outline text="A" xmlUrl="https://a.example/feed"/>
            <outline text="Nested"><outline text="B" xmlUrl="https://b.example/feed"/></outline></outline>
          <outline text="C" title="C" xmlUrl="https://c.example/feed" htmlUrl="https://c.example/"/>
          <outline text="Bad" xmlUrl="javascript:alert(1)"/>
        </body></opml>"""
        added, skipped = opml.import_opml(data)
        self.assertEqual((added, skipped), (3, 1))
        self.assertEqual(Feed.objects.get(url="https://b.example/feed").folder.name, "Tech")
        self.assertIsNone(Feed.objects.get(url="https://c.example/feed").folder)

        added, skipped = opml.import_opml(data)  # idempotent
        self.assertEqual(added, 0)

        exported = opml.export_opml()
        self.assertEqual(len(opml.parse_opml(exported)), 3)

    def test_rejects_entities(self):
        with self.assertRaises(opml.OPMLError):
            opml.parse_opml(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><opml><body/></opml>')
