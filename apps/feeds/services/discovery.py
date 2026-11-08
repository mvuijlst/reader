"""Find the feed behind a URL: the URL itself, a <link rel=alternate>, or a common path."""
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

import feedparser

from . import http
from .sanitize import clean_text

FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/feed+json",
    "application/json",
    "application/xml",
    "text/xml",
}

COMMON_PATHS = ["/feed", "/rss", "/feed.xml", "/atom.xml", "/rss.xml", "/index.xml", "/feed/"]

ACCEPT = (
    "application/rss+xml, application/atom+xml, application/feed+json, "
    "application/xml;q=0.9, text/xml;q=0.9, text/html;q=0.8, */*;q=0.5"
)


class DiscoveryError(Exception):
    pass


@dataclass
class Candidate:
    url: str
    title: str = ""


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.base = None

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            self.links.append({k.lower(): (v or "") for k, v in attrs})
        elif tag == "base" and self.base is None:
            self.base = dict(attrs).get("href")


def parse_links(html_text, page_url):
    """All <link> tags on a page, with hrefs made absolute."""
    parser = _LinkParser()
    try:
        parser.feed(html_text)
    except Exception:  # malformed HTML: use whatever we got
        pass
    base = urljoin(page_url, parser.base) if parser.base else page_url
    links = []
    for link in parser.links:
        if link.get("href"):
            link["href"] = urljoin(base, link["href"].strip())
            link["rel"] = link.get("rel", "").lower().split()
            link["type"] = link.get("type", "").lower().split(";")[0].strip()
            links.append(link)
    return links


def normalize_url(url):
    url = url.strip()
    if url.startswith("feed:"):
        url = url[5:].lstrip("/")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise DiscoveryError("That doesn't look like a web address.")
    return url


def parse_feed(result):
    return feedparser.parse(
        result.content,
        response_headers={
            "content-location": result.url,
            "content-type": result.headers.get("content-type", ""),
        },
    )


def is_feed(parsed):
    return bool(parsed.get("version"))


_FORMAT_WORDS = re.compile(r"\b(rss|atom|json|xml|feed|rdf|\d+(\.\d+)?)\b", re.I)


def _is_format_variant_set(candidates):
    """True when the candidates look like one feed offered in several formats
    ("RSS 2.0" / "Atom 1.0", or identical titles), so there's nothing to choose."""
    if len(candidates) < 2:
        return True
    labels = {_FORMAT_WORDS.sub("", c.title).strip(" -–—:|·").lower() for c in candidates}
    return len(labels) == 1


def _youtube_shortcut(url):
    """Channel and playlist URLs map straight onto YouTube's feed URLs."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not (host == "youtube.com" or host.endswith(".youtube.com")):
        return None
    query = parse_qs(parts.query)
    if "list" in query:
        return f"https://www.youtube.com/feeds/videos.xml?playlist_id={query['list'][0]}"
    match = re.match(r"^/channel/(UC[\w-]{10,})", parts.path)
    if match:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}"
    return None


def _request_headers(url):
    headers = {"Accept": ACCEPT}
    host = (urlsplit(url).hostname or "").lower()
    if host == "youtube.com" or host.endswith(".youtube.com"):
        # YouTube's "reject all" consent state; without it EU servers get the
        # consent wall instead of the channel page.
        headers["Cookie"] = "SOCS=CAI"
    return headers


def choose(candidates):
    """Collapse format variants to the best one; keep genuinely different feeds."""
    if _is_format_variant_set(candidates):
        # Prefer XML over JSON Feed: better supported everywhere.
        return sorted(candidates, key=lambda c: "json" in (c.title + c.url).lower())[:1]
    return candidates


def discover(url):
    """Return feed candidates for a URL, best first. Raises DiscoveryError."""
    url = normalize_url(url)
    url = _youtube_shortcut(url) or url
    try:
        result = http.get(url, headers=_request_headers(url), max_bytes=5 * 1024 * 1024)
    except http.FetchError as e:
        raise DiscoveryError(str(e)) from e
    if not result.ok:
        raise DiscoveryError(f"The site answered HTTP {result.status}.")

    parsed = parse_feed(result)
    if is_feed(parsed):
        return [Candidate(result.url, clean_text(parsed.feed.get("title", "")))]

    seen, candidates = set(), []
    for link in parse_links(result.text(), result.url):
        if "alternate" in link["rel"] and link["type"] in FEED_TYPES:
            href = link["href"]
            if href not in seen and urlsplit(href).scheme in ("http", "https"):
                seen.add(href)
                candidates.append(Candidate(href, clean_text(link.get("title", ""))))
    if candidates:
        # Comment feeds are rarely what you want; push them to the back.
        candidates.sort(key=lambda c: "comment" in (c.title + c.url).lower())
        return choose(candidates)

    root = f"{urlsplit(result.url).scheme}://{urlsplit(result.url).netloc}"
    for path in COMMON_PATHS:
        try:
            guess = http.get(root + path, headers={"Accept": ACCEPT}, max_bytes=5 * 1024 * 1024)
        except http.FetchError:
            continue
        if guess.ok:
            parsed = parse_feed(guess)
            if is_feed(parsed):
                return [Candidate(guess.url, clean_text(parsed.feed.get("title", "")))]
    return []
