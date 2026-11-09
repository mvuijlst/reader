"""Is a feed still there? Used to prune dead subscriptions out of an OPML file.

A feed counts as gone only on clear evidence: its domain no longer exists, it
answers 404/410, or its address no longer serves a feed. Even then, if the
publication's site still offers a feed under the same name, that one replaces
it. Anything inconclusive (timeouts, server errors, refusals, bot protection)
is "unsure", never "dead".
"""
import re
import socket
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from . import discovery, http
from .sanitize import clean_text

OK = "ok"  # works; new_url is set when it moved permanently
FOUND = "found"  # gone, but the site has a feed of the same name at new_url
DEAD = "dead"
UNSURE = "unsure"  # couldn't tell; worth another try later

TIMEOUT = (10, 20)  # connect, read

_NO_SUCH_NAME = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
if sys.platform == "win32":
    _NO_SUCH_NAME.add(11004)  # WSANO_DATA: the name exists but has no address

# Words that say nothing about which publication a feed belongs to.
_STOPWORDS = {
    "a", "an", "and", "at", "atom", "blog", "comments", "de", "een", "feed", "feeds", "for",
    "from", "het", "home", "in", "latest", "my", "news", "of", "on", "posts", "rss", "site",
    "the", "van", "weblog", "with", "your",
}


@dataclass
class Check:
    verdict: str
    detail: str = ""
    new_url: str = ""
    title: str = ""  # the feed's own title, when it could be read
    newest: datetime | None = None  # date of its most recent item
    entries: int = 0


def domain_exists(host):
    """False only when DNS says the name doesn't exist (not on DNS trouble)."""
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return e.errno not in _NO_SUCH_NAME
    except (UnicodeError, ValueError):  # not a valid host name at all
        return False
    return True


def newest_entry(parsed):
    dates = []
    for entry in parsed.entries:
        t = entry.get("published_parsed") or entry.get("updated_parsed")
        if t:
            dates.append(datetime(*t[:6], tzinfo=timezone.utc))
    return max(dates, default=None)


def _words(title):
    return [w for w in re.findall(r"\w+", title.lower()) if w not in _STOPWORDS]


def titles_match(old, new):
    """Loose check that a replacement feed belongs to the same publication, so a
    lapsed domain now serving someone else's blog isn't taken for it."""
    a, b = "".join(_words(old)), "".join(_words(new))
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return True
    if min(len(a), len(b)) >= 6 and SequenceMatcher(None, a, b).ratio() >= 0.85:
        return True  # the same name spelled a little differently
    return bool({w for w in _words(old) if len(w) >= 4} & {w for w in _words(new) if len(w) >= 4})


def _working(parsed, **fields):
    return Check(
        OK,
        title=clean_text(parsed.feed.get("title", "")),
        newest=newest_entry(parsed),
        entries=len(parsed.entries),
        **fields,
    )


def _get(url):
    return http.get(url, headers={"Accept": discovery.ACCEPT}, timeout=TIMEOUT)


def _failed_host(err):
    """The host a FetchError was about: after redirects it isn't always the first one."""
    request = getattr(err.__cause__, "request", None)
    return urlsplit(getattr(request, "url", "") or "").hostname


def check_feed(url, site_url="", title=""):
    """Check one subscription. `site_url` and `title` come from the OPML file."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return Check(UNSURE, "not a web address")
    try:
        result = _get(url)
    except http.FetchError as e:
        host = _failed_host(e) or parts.hostname
        if not domain_exists(host):
            return _look_on_site(url, site_url, title, f"{host} no longer exists", dead_host=host)
        return Check(UNSURE, str(e))
    blocked = http.bot_protection(result)
    if blocked:
        return Check(UNSURE, "blocked by bot protection" + ("" if blocked == "unknown" else f" ({blocked})"))
    if result.status in (404, 410):
        return _look_on_site(url, site_url, title, f"HTTP {result.status}")
    if not result.ok:
        return Check(UNSURE, f"HTTP {result.status}")
    parsed = discovery.parse_feed(result)
    if not discovery.is_feed(parsed):
        where = f" (ends up at {result.url})" if result.url != url else ""
        return _look_on_site(url, site_url, title, f"no feed there any more{where}")
    moved = result.permanent_url if result.permanent_url and result.permanent_url != url else ""
    return _working(parsed, new_url=moved, detail="moved permanently" if moved else "")


def _look_on_site(url, site_url, title, reason, dead_host=None):
    """The feed is gone: does the publication's site have a new one?"""
    site_host = urlsplit(site_url).hostname if site_url else None
    if not site_host or site_host == dead_host:
        return Check(DEAD, reason)
    try:
        candidates = discovery.discover(site_url)
    except discovery.DiscoveryError:
        return Check(DEAD, reason)
    other = ""
    for candidate in candidates[:3]:
        if candidate.url == url:
            continue
        try:
            result = _get(candidate.url)
        except http.FetchError:
            continue
        if not result.ok or http.bot_protection(result):
            continue
        parsed = discovery.parse_feed(result)
        if not discovery.is_feed(parsed):
            continue
        found = _working(parsed)
        if titles_match(title, found.title or candidate.title):
            return replace(found, verdict=FOUND, new_url=result.permanent_url or candidate.url,
                           detail=f"{reason}; the site's feed moved")
        other = other or found.title or candidate.url
    if other:
        reason += f"; the site now has a different feed (“{other}”)"
    return Check(DEAD, reason)
