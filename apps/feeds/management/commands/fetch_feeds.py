import time

from django.core.management.base import BaseCommand

from apps.feeds.models import Feed
from apps.feeds.services import fetcher


class Command(BaseCommand):
    help = "Fetch feeds that are due (run this from the systemd timer or cron)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Fetch every feed, due or not.")
        parser.add_argument("--feed", type=int, action="append", help="Fetch only this feed id (repeatable).")
        parser.add_argument("--workers", type=int, help="Parallel downloads.")
        parser.add_argument("--wait", type=int, default=0, help="Seconds to wait if another run holds the lock.")
        parser.add_argument("--no-maintenance", action="store_true", help="Skip auto-read and purge.")

    def handle(self, *args, **opts):
        feeds = None
        if opts["feed"]:
            feeds = Feed.objects.defer("icon").filter(pk__in=opts["feed"])
        elif opts["all"]:
            feeds = Feed.objects.defer("icon").all()

        started = time.monotonic()
        stats = fetcher.run(
            feeds=feeds,
            workers=opts["workers"],
            lock_timeout=opts["wait"],
            do_maintenance=not opts["no_maintenance"],
        )
        if stats is None:
            self.stdout.write("Another fetch is running; skipped.")
            return
        self.stdout.write(f"{stats} in {time.monotonic() - started:.1f}s")
