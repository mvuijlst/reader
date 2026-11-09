"""Check every feed in an OPML file; write a copy without the dead ones, plus a report.

    python manage.py prune_opml subscriptions.opml

writes subscriptions.pruned.opml and subscriptions.report.csv next to it and
leaves the original alone. See services/feedcheck.py for what counts as dead.
"""
import csv
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import chain, zip_longest
from pathlib import Path
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from apps.feeds.services import feedcheck, opml
from apps.feeds.services.feedcheck import DEAD, FOUND, OK, UNSURE

DORMANT = "dormant"  # works, but nothing new in --drop-dormant years
PER_HOST = 2  # requests at a time to one host: many feeds share feedburner, blogspot, …
ORDER = [DEAD, DORMANT, UNSURE, FOUND, OK]
STALE_YEARS = (1, 3, 5, 10)


@dataclass
class Subscription:
    parent: ET.Element
    element: ET.Element
    url_key: str  # the xmlUrl attribute's spelling in this file
    url: str
    site: str
    title: str
    folder: str


def subscriptions(node, folder=""):
    for el in node.findall("outline"):
        keys = {k.lower(): k for k in el.attrib}
        url = (el.get(keys.get("xmlurl", "")) or "").strip()
        if url:
            title = el.get(keys.get("title", "")) or el.get(keys.get("text", "")) or ""
            site = (el.get(keys.get("htmlurl", "")) or "").strip()
            yield Subscription(node, el, keys["xmlurl"], url, site, title.strip(), folder)
        else:
            name = (el.get("title") or el.get("text") or "").strip()
            yield from subscriptions(el, f"{folder} / {name}" if folder else name)


def prune_empty_folders(node, emptied):
    """Drop folders that lost all their feeds (but not ones that were empty already)."""
    for child in node.findall("outline"):
        if not any(k.lower() == "xmlurl" for k in child.attrib):
            prune_empty_folders(child, emptied)
            if child in emptied and not child.findall("outline"):
                node.remove(child)
                emptied.add(node)


def interleave_hosts(urls):
    """Spread feeds on the same host through the queue so the per-host limit
    doesn't leave workers waiting."""
    by_host = defaultdict(list)
    for url in urls:
        by_host[urlsplit(url).hostname or ""].append(url)
    return [u for u in chain.from_iterable(zip_longest(*by_host.values())) if u]


class Command(BaseCommand):
    help = (
        "Check every feed in an OPML file and write a copy without the ones that no longer "
        "exist, plus a CSV report. The original file is left alone."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument("-o", "--output", type=Path,
                            help="Cleaned OPML file (default: NAME.pruned.opml next to the input).")
        parser.add_argument("--report", type=Path,
                            help="CSV report (default: NAME.report.csv next to the input).")
        parser.add_argument("--drop-dormant", type=float, metavar="YEARS",
                            help="Also drop feeds that still work but have posted nothing in YEARS years.")
        parser.add_argument("--drop-unsure", action="store_true",
                            help="Also drop feeds that couldn't be checked (timeouts, server errors, bot protection).")
        parser.add_argument("--workers", type=int, default=12, help="Feeds checked at once (default 12).")

    def handle(self, *args, path, output, report, drop_dormant, drop_unsure, workers, **opts):
        output = output or path.with_name(f"{path.stem}.pruned.opml")
        report = report or path.with_name(f"{path.stem}.report.csv")
        if output.resolve() == path.resolve():
            raise CommandError("Write the result to a new file, not over the original.")
        try:
            root = opml.load_opml(path.read_bytes())
        except (OSError, opml.OPMLError) as e:
            raise CommandError(str(e)) from e
        subs = list(subscriptions(root.find("body")))
        if not subs:
            raise CommandError("There are no feeds in that file.")

        feeds = {}
        for sub in subs:
            feeds.setdefault(sub.url, sub)
        started = time.monotonic()
        self.stdout.write(f"Checking {len(feeds)} feeds…")
        results = self.check_feeds(feeds, workers)
        unsure = [url for url, check in results.items() if check.verdict == UNSURE]
        if unsure:
            self.stdout.write(f"Trying the {len(unsure)} unsure ones again…")
            results.update(self.check_feeds({url: feeds[url] for url in unsure}, max(1, workers // 2)))

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=365.25 * drop_dormant) if drop_dormant else None
        verdicts = {}
        for url, check in results.items():
            verdict = check.verdict
            if cutoff and verdict in (OK, FOUND) and (check.entries == 0 or (check.newest and check.newest < cutoff)):
                verdict = DORMANT
            verdicts[url] = verdict
        drop = {DEAD, DORMANT} | ({UNSURE} if drop_unsure else set())

        # Feeds that moved or were switched can land on an address another
        # subscription already has (several dead section feeds -> the magazine's
        # main feed): keep the one already there, else one that merely moved, else
        # the first switched one. `taken` maps an address to the original address
        # that claimed it, so a feed the file itself lists twice stays as it was.
        taken = {}
        for claims in (lambda c: not c.new_url, lambda c: c.verdict == OK):
            for s in subs:
                check = results[s.url]
                if verdicts[s.url] not in drop and claims(check):
                    taken.setdefault(check.new_url or s.url, s.url)
        rows, emptied = [], set()
        for sub in subs:
            check, verdict = results[sub.url], verdicts[sub.url]
            duplicate = bool(check.new_url) and taken.setdefault(check.new_url, sub.url) != sub.url
            if verdict in drop or duplicate:
                sub.parent.remove(sub.element)
                emptied.add(sub.parent)
                action = "removed" if verdict in drop else "removed (duplicate)"
            elif check.new_url:
                sub.element.set(sub.url_key, check.new_url)
                action = "switched feed" if verdict == FOUND else "new address"
            else:
                action = "kept"
            rows.append((verdict, action, check, sub))
        prune_empty_folders(root.find("body"), emptied)

        ET.indent(root)
        output.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        self.write_report(report, rows)
        self.summarise(results, verdicts, rows, now, cutoff, drop_unsure, time.monotonic() - started)
        kept = sum(1 for r in rows if not r[1].startswith("removed"))
        self.stdout.write(f"Wrote {output} ({kept} of {len(subs)} subscriptions) and {report}.")

    def check_feeds(self, feeds, workers):
        locks, guard = defaultdict(lambda: threading.BoundedSemaphore(PER_HOST)), threading.Lock()

        def one(url):
            sub = feeds[url]
            with guard:
                lock = locks[urlsplit(url).hostname or ""]
            with lock:
                try:
                    return url, feedcheck.check_feed(url, sub.site, sub.title)
                except Exception as e:  # one odd feed shouldn't sink the whole run
                    return url, feedcheck.Check(UNSURE, f"check failed: {e!r}"[:200])

        results = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, url) for url in interleave_hosts(feeds)]
            for n, future in enumerate(as_completed(futures), 1):
                url, check = future.result()
                results[url] = check
                if n % 50 == 0 or n == len(futures):
                    self.stdout.write(f"  {n}/{len(futures)}")
        return results

    def write_report(self, path, rows):
        rows = sorted(rows, key=lambda r: (ORDER.index(r[0]), r[3].folder.lower(), r[3].title.lower()))
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
            out = csv.writer(fh)
            out.writerow(["verdict", "action", "why", "folder", "title", "feed", "new feed", "latest post", "items"])
            for verdict, action, check, sub in rows:
                out.writerow([
                    verdict, action, check.detail, sub.folder, sub.title, sub.url, check.new_url,
                    check.newest.date().isoformat() if check.newest else "",
                    check.entries if check.verdict in (OK, FOUND) else "",
                ])

    def summarise(self, results, verdicts, rows, now, cutoff, drop_unsure, seconds):
        counts = Counter(verdicts.values())
        moved = sum(1 for url, c in results.items() if verdicts[url] == OK and c.new_url)
        lines = {
            OK: f"working{f', {moved} at a new address (updated)' if moved else ''}",
            FOUND: "gone, but the site has a feed of the same name: switched to it",
            DEAD: "gone: removed",
            DORMANT: "working, but nothing new for a long time: removed",
            UNSURE: "couldn't tell (timeouts, server errors, bot protection): "
                    + ("removed" if drop_unsure else "kept, --drop-unsure removes them"),
        }
        self.stdout.write(f"\nChecked {len(results)} feeds in {seconds / 60:.0f} min:")
        for verdict in [OK, FOUND, DEAD, DORMANT, UNSURE]:
            if counts[verdict]:
                self.stdout.write(f"  {counts[verdict]:5}  {lines[verdict]}")
        duplicates = sum(1 for r in rows if r[1] == "removed (duplicate)")
        if duplicates:
            self.stdout.write(f"  {duplicates:5}  ended up at an address already in the file: removed")
        if not cutoff:
            working = [c for url, c in results.items() if verdicts[url] in (OK, FOUND)]
            stale = [
                f"{sum(1 for c in working if c.newest and c.newest < now - timedelta(days=365.25 * y))} for {y}+ years"
                for y in STALE_YEARS
            ]
            empty = sum(1 for c in working if c.entries == 0)
            self.stdout.write(
                f"Of the working feeds, nothing new: {', '.join(stale)}"
                f"{f'; {empty} are empty' if empty else ''}. --drop-dormant YEARS removes those."
            )
