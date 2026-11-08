from contextlib import contextmanager
from unittest import mock

from apps.feeds.services.http import HttpResult

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <title>Example &amp; Co</title>
  <link>https://example.com/</link>
  <item>
    <title>First &lt;em&gt;post&lt;/em&gt;</title>
    <link>https://example.com/1</link>
    <guid>https://example.com/1</guid>
    <pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate>
    <description><![CDATA[<p>Hello <script>alert(1)</script><a href="/rel" onclick="x()">there</a></p><img src="/a.png">]]></description>
  </item>
  <item>
    <title>Podcast episode</title>
    <link>https://example.com/2</link>
    <guid isPermaLink="false">ep-2</guid>
    <pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate>
    <description>Plain text
second line</description>
    <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg" length="1234"/>
  </item>
  <item>
    <title>Ancient</title>
    <link>https://example.com/old</link>
    <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
    <description>Old news</description>
  </item>
</channel>
</rss>"""

HTML_WITH_FEEDS = b"""<!doctype html><html><head>
<link rel="alternate" type="application/rss+xml" title="Posts" href="/feed.xml">
<link rel="alternate" type="application/rss+xml" title="Comments" href="/comments.xml">
<link rel="icon" href="/icon.png">
</head><body>hi</body></html>"""

HTML_FORMAT_VARIANTS = b"""<!doctype html><html><head>
<link rel="alternate" type="application/feed+json" title="JSON Feed" href="/feed.json">
<link rel="alternate" type="application/atom+xml" title="Atom 1.0" href="/atom.xml">
<link rel="alternate" type="application/rss+xml" title="RSS 2.0" href="/rss.xml">
</head></html>"""

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def response(content=b"", status=200, url="https://example.com/feed.xml", headers=None, permanent_url=""):
    return HttpResult(
        status=status,
        url=url,
        headers={k.lower(): v for k, v in (headers or {}).items()},
        content=content,
        permanent_url=permanent_url,
    )


@contextmanager
def fake_web(routes):
    """Patch the HTTP layer: `routes` maps URL -> HttpResult (or a callable taking headers)."""
    calls = []

    def get(url, headers=None, **kwargs):
        calls.append((url, headers or {}))
        route = routes.get(url)
        if route is None:
            return response(b"not found", status=404, url=url)
        return route(headers or {}) if callable(route) else route

    with mock.patch("apps.feeds.services.http.get", side_effect=get):
        yield calls
