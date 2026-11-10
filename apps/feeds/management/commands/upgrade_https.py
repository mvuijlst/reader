"""One-off: switch subscriptions from http:// to https:// where the site allows it.

    python manage.py upgrade_https --dry-run    # report only
    python manage.py upgrade_https              # apply

For every feed whose address starts with http://, this requests the https://
address and switches only if it serves a real feed. Feeds that don't (no TLS,
certificate errors, timeouts, not a feed) stay as they are and are listed.
Items, read state and stars are kept: only the feed's address changes.
"""
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.feeds.models import Feed
from apps.feeds.services import discovery, http

TIMEOUT = (10, 20)


def https_variant(url):
    return "https://" + url[len("http://"):]


def check(url):
    """Return (ok, detail) for the https address."""
    try:
        res = http.get(url, timeout=TIMEOUT)
    except http.FetchError as e:
        return False, str(e)
    if res.status != 200:
        return False, f"HTTP {res.status}"
    if not discovery.is_feed(discovery.parse_feed(res)):
        return False, "not a feed"
    return True, ""


class Command(BaseCommand):
    help = "Switch feeds from http:// to https:// where the https address serves the feed."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would change; change nothing.")
        parser.add_argument("--workers", type=int, default=8, help="Parallel checks (default 8).")

    def handle(self, *args, **opts):
        feeds = list(Feed.objects.defer("icon").filter(url__istartswith="http://"))
        if not feeds:
            self.stdout.write("No http:// feeds. Nothing to do.")
            return
        self.stdout.write(f"Checking {len(feeds)} http:// feed(s)…")

        with ThreadPoolExecutor(max_workers=max(1, opts["workers"])) as pool:
            results = list(pool.map(lambda f: check(https_variant(f.url)), feeds))

        taken = set(Feed.objects.filter(url__istartswith="https://").values_list("url", flat=True))
        upgraded, kept, skipped = 0, [], []
        for feed, (ok, detail) in zip(feeds, results):
            new_url = https_variant(feed.url)
            if not ok:
                kept.append((feed, detail))
                continue
            if new_url in taken:
                skipped.append((feed, "already subscribed over https"))
                continue
            upgraded += 1
            taken.add(new_url)
            self.stdout.write(f"  {feed.name}: {feed.url} -> {new_url}")
            if not opts["dry_run"]:
                feed.url = new_url
                # Validators belong to the old address; refetch soon and start clean.
                feed.etag = feed.last_modified = feed.last_error = ""
                feed.error_count = 0
                feed.next_fetch_at = timezone.now()
                feed.save(update_fields=["url", "etag", "last_modified", "last_error", "error_count", "next_fetch_at"])

        verb = "Would upgrade" if opts["dry_run"] else "Upgraded"
        self.stdout.write(self.style.SUCCESS(f"{verb} {upgraded} feed(s)."))
        for label, rows in (("Left on http (https didn't work)", kept), ("Skipped", skipped)):
            if rows:
                self.stdout.write(f"{label}: {len(rows)}")
                for feed, why in rows:
                    self.stdout.write(f"  {feed.name} <{feed.url}>: {why}")
