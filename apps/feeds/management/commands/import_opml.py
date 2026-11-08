from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.feeds.services import opml


class Command(BaseCommand):
    help = "Import subscriptions from an OPML file (e.g. a Google Takeout or another reader's export)."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)

    def handle(self, *args, path, **opts):
        try:
            added, skipped = opml.import_opml(path.read_bytes())
        except (OSError, opml.OPMLError) as e:
            raise CommandError(str(e)) from e
        self.stdout.write(f"Added {added} feeds, skipped {skipped}. Run fetch_feeds to download them.")
