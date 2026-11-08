"""Polling feeds and storing their entries.

Downloads and parsing run in a thread pool; every database write happens on
the calling thread, so SQLite only ever sees one writer per process.
"""
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone
from filelock import FileLock, Timeout

from ..models import Feed, Item
from . import http
from .discovery import parse_feed, parse_links
from .sanitize import clean_html, clean_text, first_image, plain_to_html, text_snippet

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 10 * 1024 * 1024
MAX_ICON_BYTES = 200 * 1024
ICON_RECHECK = timedelta(days=30)
ACCEPT = (
    "application/rss+xml, application/atom+xml, application/feed+json, "
    "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
)
ITEM_UPDATE_FIELDS = ["title", "content", "summary", "url", "author", "image", "thumbnail", "enclosures"]
LOCK_PATH = settings.BASE_DIR / ".fetch.lock"


@dataclass
class FeedJob:
    """What a worker thread needs to know about a feed (no ORM objects cross threads)."""

    id: int
    url: str
    etag: str = ""
    last_modified: str = ""
    site_url: str = ""
    want_icon: bool = False


@dataclass
class FetchResult:
    feed_id: int
    error: str = ""
    retry_after: int | None = None
    not_modified: bool = False
    permanent_url: str = ""
    etag: str = ""
    last_modified: str = ""
    title: str = ""
    site_url: str = ""
    entries: list = field(default_factory=list)
    icon: tuple | None = None  # (bytes, content_type)
    icon_checked: bool = False


@dataclass
class RunStats:
    feeds: int = 0
    new_items: int = 0
    errors: int = 0
    not_modified: int = 0
    auto_read: int = 0
    purged: int = 0

    def __str__(self):
        return (
            f"{self.feeds} feeds, {self.new_items} new items, {self.not_modified} unchanged, "
            f"{self.errors} errors, {self.auto_read} auto-read, {self.purged} purged"
        )


# --- worker side (no database access) ---------------------------------------


def _safe_url(url):
    url = (url or "").strip()
    return url if urlsplit(url).scheme in ("http", "https") else ""


def _entry_date(entry, now):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=dt_timezone.utc)
            except (TypeError, ValueError):
                continue
            return min(dt, now)
    return now


_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")


def _as_html(value, declared_type):
    # feedparser often labels plain text (e.g. YouTube descriptions) as HTML;
    # a body without a single tag is plain text whatever the label says.
    if "html" in (declared_type or "text/html") and _TAG.search(value):
        return value
    return plain_to_html(value)


def _entry_content(entry):
    for c in entry.get("content") or []:
        value = c.get("value") or ""
        if value.strip():
            return _as_html(value, c.get("type"))
    summary = entry.get("summary") or entry.get("media_description") or ""
    if not summary.strip():
        return ""
    return _as_html(summary, (entry.get("summary_detail") or {}).get("type"))


def _entry_media(entry):
    """(enclosures, thumbnail) — audio/video attachments and a lead image."""
    enclosures, seen, thumb = [], set(), ""
    sources = [(e.get("href"), e.get("type", ""), e.get("length", "")) for e in entry.get("enclosures") or []]
    for m in entry.get("media_content") or []:
        mtype = m.get("type") or (f"{m['medium']}/*" if m.get("medium") else "")
        sources.append((m.get("url"), mtype, m.get("filesize", "")))
    for url, mtype, length in sources:
        url = _safe_url(url)
        if not url or url in seen:
            continue
        seen.add(url)
        mtype = (mtype or "").lower()
        if mtype.startswith(("audio/", "video/")):
            enclosures.append({"url": url, "type": mtype, "length": str(length or "")})
        elif mtype.startswith("image/") and not thumb:
            thumb = url
    for t in entry.get("media_thumbnail") or []:
        if _safe_url(t.get("url")):
            thumb = thumb or t["url"]
            break
    # Podcast episode artwork (<itunes:image href="…">).
    episode_art = _safe_url((entry.get("image") or {}).get("href", ""))
    return enclosures, thumb or episode_art


def normalize_entries(entries, now):
    out = {}
    for entry in entries:
        link = _safe_url(entry.get("link"))
        title = clean_text(entry.get("title", ""))
        content = clean_html(_entry_content(entry))
        guid = (entry.get("id") or link or f"{title}|{entry.get('published', '')}").strip()
        if not guid.strip("|"):
            guid = hashlib.sha1(content.encode()).hexdigest()
        guid_hash = hashlib.sha1(guid.encode()).hexdigest()
        if guid_hash in out:
            continue
        enclosures, thumb = _entry_media(entry)
        out[guid_hash] = {
            "guid": guid,
            "guid_hash": guid_hash,
            "url": link[:2000],
            "title": title,
            "author": clean_text(entry.get("author", ""))[:500],
            "content": content,
            "summary": text_snippet(content),
            "image": thumb[:2000] if thumb and "<img" not in content else "",
            "thumbnail": (thumb or first_image(content))[:2000],
            "enclosures": enclosures,
            "published": _entry_date(entry, now),
        }
    return list(out.values())


def _retry_after(headers):
    value = headers.get("retry-after")
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        return max(0, int((parsedate_to_datetime(value) - timezone.now()).total_seconds()))
    except (TypeError, ValueError):
        return None


def _sniff_image(data):
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None  # SVG deliberately excluded: it can carry script


def _try_image(url):
    try:
        r = http.get(url, max_bytes=MAX_ICON_BYTES, timeout=(5, 10))
    except http.FetchError:
        return None
    if not r.ok or not r.content:
        return None
    ctype = _sniff_image(r.content)
    return (r.content, ctype) if ctype else None


def find_icon(feed_icon, site_url, feed_url):
    base = site_url or feed_url
    parts = urlsplit(base)
    root = f"{parts.scheme}://{parts.netloc}"
    candidates = []
    if feed_icon:
        candidates.append(urljoin(feed_url, feed_icon))
    candidates.append(root + "/favicon.ico")
    for url in candidates:
        found = _try_image(url)
        if found:
            return found
    try:
        page = http.get(root + "/", max_bytes=2 * 1024 * 1024, timeout=(5, 10))
    except http.FetchError:
        return None
    if page.ok:
        links = [
            link["href"]
            for link in parse_links(page.text(), page.url)
            if {"icon", "apple-touch-icon"} & set(link["rel"]) and not link["href"].endswith(".svg")
        ]
        for href in links[:3]:
            found = _try_image(href)
            if found:
                return found
    return None


def download(job):
    """Fetch and parse one feed. Never raises; problems end up in result.error."""
    res = FetchResult(job.id)
    parsed = None
    try:
        headers = {"Accept": ACCEPT}
        if job.etag:
            headers["If-None-Match"] = job.etag
        if job.last_modified:
            headers["If-Modified-Since"] = job.last_modified
        r = http.get(job.url, headers=headers, max_bytes=MAX_FEED_BYTES)
        if r.status == 304:
            res.not_modified = True
        elif not r.ok:
            res.error = f"HTTP {r.status}"
            res.retry_after = _retry_after(r.headers)
        else:
            parsed = parse_feed(r)
            if not parsed.get("version") and not parsed.entries:
                reason = parsed.get("bozo_exception")
                res.error = "Not a valid feed" + (f": {reason}" if reason else "")
            else:
                res.etag = r.headers.get("etag", "")
                res.last_modified = r.headers.get("last-modified", "")
                res.permanent_url = r.permanent_url
                res.title = clean_text(parsed.feed.get("title", ""))
                res.site_url = _safe_url(parsed.feed.get("link", ""))
                res.entries = normalize_entries(parsed.entries, timezone.now())
    except http.FetchError as e:
        res.error = str(e)
    except Exception as e:  # a broken feed must not take the whole run down
        log.exception("Unexpected error fetching %s", job.url)
        res.error = f"Unexpected error: {e.__class__.__name__}: {e}"

    if job.want_icon and not res.error:
        res.icon_checked = True
        feed_icon = parsed.feed.get("icon", "") if parsed else ""
        try:
            res.icon = find_icon(feed_icon, res.site_url or job.site_url, job.url)
        except Exception:
            log.exception("Icon lookup failed for %s", job.url)
    return res


# --- main-thread side --------------------------------------------------------


def store_entries(feed, entries, now):
    """Insert new entries, refresh changed ones. Returns the number of new items."""
    if not entries:
        return 0
    hashes = [e["guid_hash"] for e in entries]
    existing = {
        item.guid_hash: item
        for item in Item.objects.filter(feed=feed, guid_hash__in=hashes).only(
            "id", "guid_hash", *ITEM_UPDATE_FIELDS
        )
    }
    days = settings.READER_AUTO_READ_DAYS
    auto_read_before = now - timedelta(days=days) if days else None

    to_create, to_update = [], []
    for entry in entries:
        item = existing.get(entry["guid_hash"])
        if item is None:
            old = bool(auto_read_before and entry["published"] < auto_read_before)
            to_create.append(
                Item(feed=feed, last_seen_at=now, is_read=old, read_at=now if old else None, **entry)
            )
            continue
        changed = False
        for name in ITEM_UPDATE_FIELDS:
            if getattr(item, name) != entry[name]:
                setattr(item, name, entry[name])
                changed = True
        if changed:
            to_update.append(item)

    # ignore_conflicts: a concurrent fetch of the same feed may have won the race.
    Item.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        Item.objects.bulk_update(to_update, ITEM_UPDATE_FIELDS, batch_size=200)
    Item.objects.filter(feed=feed, guid_hash__in=hashes).update(last_seen_at=now)
    return sum(1 for item in to_create if not item.is_read)


def apply_result(feed, res, now):
    """Write one fetch result to the database. Returns the number of new unread items."""
    interval = timedelta(minutes=settings.READER_FETCH_INTERVAL_MINUTES)
    feed.last_fetched_at = now
    if res.icon_checked:
        feed.icon_checked_at = now
        if res.icon:
            feed.icon, feed.icon_type = res.icon

    if res.error:
        feed.error_count += 1
        feed.last_error = res.error[:2000]
        backoff = min(interval * 2 ** min(feed.error_count - 1, 8), timedelta(hours=24))
        if res.retry_after:
            backoff = max(backoff, timedelta(seconds=min(res.retry_after, 7 * 86400)))
        feed.next_fetch_at = now + backoff
        feed.save(force_update=True)
        return 0

    new = 0
    with transaction.atomic():
        feed.error_count = 0
        feed.last_error = ""
        feed.last_success_at = now
        feed.next_fetch_at = now + interval
        if not res.not_modified:
            feed.last_parsed_at = now
            feed.etag = res.etag[:500]
            feed.last_modified = res.last_modified[:200]
            if res.title:
                feed.title = res.title[:500]
            if res.site_url:
                feed.site_url = res.site_url[:2000]
            moved = res.permanent_url
            if moved and moved != feed.url and not Feed.objects.filter(url=moved).exists():
                log.info("Feed %s moved permanently to %s", feed.url, moved)
                feed.url = moved[:2000]
            new = store_entries(feed, res.entries, now)
        feed.save(force_update=True)
    return new


def make_job(feed, now):
    return FeedJob(
        id=feed.pk,
        url=feed.url,
        etag=feed.etag,
        last_modified=feed.last_modified,
        site_url=feed.site_url,
        want_icon=feed.icon_checked_at is None or feed.icon_checked_at < now - ICON_RECHECK,
    )


def fetch_feeds(feeds, workers=None):
    """Fetch the given feeds in parallel and store the results."""
    feeds = list(feeds)
    stats = RunStats(feeds=len(feeds))
    if not feeds:
        return stats
    now = timezone.now()
    by_id = {f.pk: f for f in feeds}
    workers = max(1, min(workers or settings.READER_FETCH_WORKERS, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
        futures = [pool.submit(download, make_job(f, now)) for f in feeds]
        for future in as_completed(futures):
            res = future.result()
            feed = by_id[res.feed_id]
            try:
                stats.new_items += apply_result(feed, res, timezone.now())
            except DatabaseError:
                # Most likely unsubscribed while we were downloading.
                log.warning("Could not save %s (deleted meanwhile?)", feed.url)
                continue
            if res.error:
                stats.errors += 1
                log.info("✗ %s — %s", feed.url, res.error)
            elif res.not_modified:
                stats.not_modified += 1
    return stats


def due_feeds(now=None):
    now = now or timezone.now()
    return Feed.objects.defer("icon").filter(Q(next_fetch_at__isnull=True) | Q(next_fetch_at__lte=now))


def maintenance(now=None):
    """Google Reader's 30-day auto-read, plus pruning of old read items."""
    now = now or timezone.now()
    auto_read = purged = 0
    if settings.READER_AUTO_READ_DAYS:
        auto_read = Item.objects.filter(
            is_read=False, published__lt=now - timedelta(days=settings.READER_AUTO_READ_DAYS)
        ).update(is_read=True, read_at=now)
    if settings.READER_KEEP_DAYS:
        # Only delete items that have dropped out of their feed: deleting one
        # that is still published would make it reappear as new.
        purged, _ = Item.objects.filter(
            is_read=True,
            is_starred=False,
            published__lt=now - timedelta(days=settings.READER_KEEP_DAYS),
            last_seen_at__lt=F("feed__last_parsed_at"),
        ).delete()
    return auto_read, purged


def run(feeds=None, workers=None, lock_timeout=0, do_maintenance=True):
    """Fetch due feeds (or the given ones) under a lock so runs never overlap.

    Returns RunStats, or None when another run holds the lock.
    """
    try:
        with FileLock(LOCK_PATH, timeout=lock_timeout):
            stats = fetch_feeds(due_feeds() if feeds is None else feeds, workers=workers)
            if do_maintenance:
                stats.auto_read, stats.purged = maintenance()
            return stats
    except Timeout:
        return None
