"""OPML import/export — how you move subscriptions between readers."""
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from ..models import Feed, Folder


class OPMLError(Exception):
    pass


def load_opml(data):
    """Parse an OPML document (bytes) into its root element."""
    if b"<!ENTITY" in data[:4096].upper():
        raise OPMLError("OPML files with entity declarations are not supported.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise OPMLError(f"Not a valid OPML file ({e}).") from e
    if root.find("body") is None:
        raise OPMLError("Not an OPML file: no <body>.")
    return root


def parse_opml(data):
    """Return [(url, title, folder_name)] from an OPML document.

    Nested folders are flattened to their top-level folder, since a feed here
    lives in at most one folder.
    """
    body = load_opml(data).find("body")
    found = []

    def walk(node, folder):
        for outline in node.findall("outline"):
            attrs = {k.lower(): v for k, v in outline.attrib.items()}
            url = (attrs.get("xmlurl") or "").strip()
            title = (attrs.get("title") or attrs.get("text") or "").strip()
            if url:
                found.append((url, title, folder))
            else:
                walk(outline, folder or title)

    walk(body, "")
    return found


@transaction.atomic
def import_opml(data):
    """Create folders and feeds from OPML. Returns (added, skipped)."""
    added = skipped = 0
    folders = {f.name.lower(): f for f in Folder.objects.all()}
    existing = set(Feed.objects.values_list("url", flat=True))
    now = timezone.now()
    for url, title, folder_name in parse_opml(data):
        if urlsplit(url).scheme not in ("http", "https") or url in existing:
            skipped += 1
            continue
        folder = None
        if folder_name:
            folder_name = folder_name[:200]
            folder = folders.get(folder_name.lower())
            if folder is None:
                folder = folders[folder_name.lower()] = Folder.objects.create(name=folder_name)
        Feed.objects.create(url=url[:2000], title=title[:500], folder=folder, next_fetch_at=now)
        existing.add(url)
        added += 1
    return added, skipped


def export_opml():
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "Reader subscriptions"
    ET.SubElement(head, "dateCreated").text = timezone.now().strftime("%a, %d %b %Y %H:%M:%S %z")
    body = ET.SubElement(root, "body")

    def add_feed(parent, feed):
        attrs = {"type": "rss", "text": feed.name, "title": feed.name, "xmlUrl": feed.url}
        if feed.site_url:
            attrs["htmlUrl"] = feed.site_url
        ET.SubElement(parent, "outline", attrs)

    feeds = sorted(Feed.objects.defer("icon"), key=lambda f: f.name.lower())
    for folder in Folder.objects.all():
        node = ET.SubElement(body, "outline", text=folder.name, title=folder.name)
        for feed in feeds:
            if feed.folder_id == folder.pk:
                add_feed(node, feed)
    for feed in feeds:
        if feed.folder_id is None:
            add_feed(body, feed)

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
