import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, close_old_connections
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST

from .models import Feed, Folder, Item, Preferences
from .services import discovery, fetcher, opml

log = logging.getLogger(__name__)


# --- helpers ----------------------------------------------------------------


def is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def safe_next(request, fallback="feeds:all"):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        return nxt
    return fallback


def sidebar_context(scope=""):
    feeds = list(Feed.objects.defer("icon"))
    counts = dict(
        Item.objects.filter(is_read=False).order_by().values_list("feed_id").annotate(n=Count("id"))
    )
    by_folder = defaultdict(list)
    for feed in feeds:
        feed.unread = counts.get(feed.pk, 0)
        by_folder[feed.folder_id].append(feed)
    for group in by_folder.values():
        group.sort(key=lambda f: f.name.lower())
    folders = list(Folder.objects.all())
    for folder in folders:
        folder.feed_list = by_folder.get(folder.pk, [])
        folder.unread = sum(f.unread for f in folder.feed_list)
    return {
        "folders": folders,
        "loose_feeds": by_folder.get(None, []),
        "total_unread": sum(counts.values()),
        "starred_count": Item.objects.filter(is_starred=True).count(),
        "has_feeds": bool(feeds),
        "scope": scope,
    }


def _parse_cursor(value):
    if not value or "~" not in value:
        return None
    stamp, _, pk = value.rpartition("~")
    try:
        return datetime.fromisoformat(stamp), int(pk)
    except ValueError:
        return None


def _stream(request, kind, obj=None):
    prefs = Preferences.load()
    items = Item.objects.select_related("feed").defer("feed__icon")
    q = ""
    if kind == "all":
        title, scope = "All items", "all"
    elif kind == "starred":
        items = items.filter(is_starred=True)
        title, scope = "Starred items", "starred"
    elif kind == "folder":
        items = items.filter(feed__folder=obj)
        title, scope = obj.name, obj.scope
    elif kind == "feed":
        items = items.filter(feed=obj)
        title, scope = obj.name, obj.scope
    elif kind == "search":
        q = request.GET.get("q", "").strip()
        items = items.filter(Q(title__icontains=q) | Q(content__icontains=q)) if q else items.none()
        title, scope = (f"Search: {q}" if q else "Search"), "search"
    else:
        raise Http404

    # "N new items / all items" applies to subscription views, not starred or search.
    filters_unread = kind in ("all", "folder", "feed")
    unread_count = items.filter(is_read=False).count() if filters_unread else None
    unread_only = filters_unread and prefs.unread_only
    if unread_only:
        items = items.filter(is_read=False)

    newest_first = not prefs.oldest_first
    cursor = _parse_cursor(request.GET.get("after"))
    if cursor:
        when, pk = cursor
        if newest_first:
            items = items.filter(Q(published__lt=when) | Q(published=when, id__lt=pk))
        else:
            items = items.filter(Q(published__gt=when) | Q(published=when, id__gt=pk))
    order = ("-published", "-id") if newest_first else ("published", "id")
    page_size = settings.READER_PAGE_SIZE
    page = list(items.order_by(*order)[: page_size + 1])
    has_more = len(page) > page_size
    page = page[:page_size]

    next_url = None
    if has_more:
        params = request.GET.copy()
        params["after"] = f"{page[-1].published.isoformat()}~{page[-1].pk}"
        next_url = f"{request.path}?{params.urlencode()}"

    ctx = {
        "prefs": prefs,
        "kind": kind,
        "scope": scope,
        "title": title,
        "q": q,
        "feed": obj if kind == "feed" else None,
        "folder": obj if kind == "folder" else None,
        "items": page,
        "next_url": next_url,
        "is_continuation": bool(cursor),
        "unread_count": unread_count,
        "unread_only": unread_only,
        "show_source": kind != "feed",
    }
    if cursor:
        return render(request, "feeds/partials/items.html", ctx)

    ctx["max_id"] = Item.objects.aggregate(m=Max("id"))["m"] or 0
    ctx["all_folders"] = Folder.objects.all()
    if is_htmx(request) and request.headers.get("HX-Target") == "main":
        return render(request, "feeds/partials/stream.html", ctx)
    ctx.update(sidebar_context(scope))
    return render(request, "feeds/reader.html", ctx)


# --- streams ----------------------------------------------------------------


@login_required
def index(request):
    return redirect("feeds:all")


@login_required
def all_items(request):
    return _stream(request, "all")


@login_required
def starred(request):
    return _stream(request, "starred")


@login_required
def folder_stream(request, pk):
    return _stream(request, "folder", get_object_or_404(Folder, pk=pk))


@login_required
def feed_stream(request, pk):
    return _stream(request, "feed", get_object_or_404(Feed.objects.defer("icon"), pk=pk))


@login_required
def search(request):
    return _stream(request, "search")


@login_required
def sidebar(request):
    return render(request, "feeds/partials/sidebar.html", sidebar_context())


# --- item state (JSON) -------------------------------------------------------


@login_required
@require_POST
def item_state(request, pk):
    now = timezone.now()
    base = Item.objects.filter(pk=pk)
    if not base.exists():
        raise Http404
    changed = False
    if "read" in request.POST:
        read = request.POST["read"] == "1"
        changed = bool(base.filter(is_read=not read).update(is_read=read, read_at=now if read else None))
    if "starred" in request.POST:
        star = request.POST["starred"] == "1"
        changed = bool(base.filter(is_starred=not star).update(is_starred=star, starred_at=now if star else None)) or changed
    item = base.only("is_read", "is_starred").get()
    return JsonResponse({"id": pk, "is_read": item.is_read, "is_starred": item.is_starred, "changed": changed})


@login_required
@require_POST
def mark_all_read(request):
    scope = request.POST.get("scope", "")
    kind, _, pk = scope.partition(":")
    items = Item.objects.filter(is_read=False)
    try:
        max_id = int(request.POST.get("max_id") or 0)
    except ValueError:
        max_id = 0
    if max_id:  # don't mark items that arrived after the page was loaded
        items = items.filter(id__lte=max_id)
    if kind == "feed" and pk.isdigit():
        items = items.filter(feed_id=int(pk))
    elif kind == "folder" and pk.isdigit():
        items = items.filter(feed__folder_id=int(pk))
    elif kind == "starred":
        items = items.filter(is_starred=True)
    elif kind != "all":
        return JsonResponse({"error": "unknown scope"}, status=400)
    marked = items.update(is_read=True, read_at=timezone.now())
    return JsonResponse({"marked": marked})


@login_required
@require_POST
def set_prefs(request):
    prefs = Preferences.load()
    if request.POST.get("view_mode") in dict(Preferences.VIEW_CHOICES):
        prefs.view_mode = request.POST["view_mode"]
    for name in ("unread_only", "oldest_first", "mark_read_on_scroll"):
        if name in request.POST:
            setattr(prefs, name, request.POST[name] in ("1", "true", "on"))
    prefs.save()
    if is_htmx(request) or request.headers.get("X-Requested-With") == "fetch":
        return HttpResponse(status=204)
    return redirect(safe_next(request, "feeds:manage"))


# --- subscriptions -----------------------------------------------------------


def _folder_from_post(request):
    """Folder chosen in a form: a new name wins over the select."""
    new_name = request.POST.get("new_folder", "").strip()[:200]
    if new_name:
        folder = Folder.objects.filter(name__iexact=new_name).first()
        return folder or Folder.objects.create(name=new_name)
    folder_id = request.POST.get("folder", "")
    return Folder.objects.filter(pk=folder_id).first() if folder_id.isdigit() else None


@login_required
def subscribe(request):
    if request.method != "POST":
        return render(request, "feeds/subscribe.html", {"folders": Folder.objects.all()})

    url = request.POST.get("url", "").strip()
    if not url:
        messages.error(request, "Paste the address of a feed or a website.")
        return redirect(safe_next(request))

    if request.POST.get("chosen"):
        candidates = [discovery.Candidate(url, request.POST.get("title", ""))]
    else:
        try:
            normalized = discovery.normalize_url(url)
        except discovery.DiscoveryError as e:
            messages.error(request, str(e))
            return redirect(safe_next(request))
        existing = Feed.objects.filter(url=normalized).first()
        if existing:
            messages.info(request, f"You're already subscribed to {existing.name}.")
            return redirect(existing)
        try:
            candidates = discovery.discover(url)
        except discovery.DiscoveryError as e:
            messages.error(request, f"Couldn't load {url}: {e}")
            return redirect(safe_next(request))

    if not candidates:
        messages.error(request, f"No feed found at {url}.")
        return redirect(safe_next(request))
    if len(candidates) > 1:
        return render(
            request,
            "feeds/subscribe.html",
            {
                "candidates": candidates,
                "folders": Folder.objects.all(),
                "folder_id": request.POST.get("folder", ""),
                "source_url": url,
            },
        )

    chosen = candidates[0]
    existing = Feed.objects.filter(url=chosen.url).first()
    if existing:
        messages.info(request, f"You're already subscribed to {existing.name}.")
        return redirect(existing)
    try:
        feed = Feed.objects.create(url=chosen.url[:2000], title=chosen.title[:500], folder=_folder_from_post(request))
    except IntegrityError:
        return redirect(Feed.objects.get(url=chosen.url))
    fetcher.fetch_feeds([feed], workers=1)
    feed.refresh_from_db()
    if feed.last_error:
        messages.warning(request, f"Subscribed, but the first fetch failed: {feed.last_error}")
    else:
        messages.success(request, f"Subscribed to {feed.name}.")
    return redirect(feed)


@login_required
@cache_control(private=True, max_age=7 * 86400)
def feed_icon(request, pk):
    feed = get_object_or_404(Feed.objects.only("icon", "icon_type"), pk=pk)
    if not feed.icon_type or not feed.icon:
        raise Http404
    return HttpResponse(bytes(feed.icon), content_type=feed.icon_type)


def _manage_row(request, feed_id):
    feed = _feeds_with_stats().get(pk=feed_id)
    return render(request, "feeds/partials/manage_row.html", {"feed": feed, "all_folders": Folder.objects.all()})


@login_required
@require_POST
def feed_update(request, pk):
    feed = get_object_or_404(Feed.objects.defer("icon"), pk=pk)
    fields = []
    if "custom_title" in request.POST:
        title = request.POST["custom_title"].strip()[:500]
        feed.custom_title = "" if title == feed.title else title
        fields.append("custom_title")
    if "folder" in request.POST or request.POST.get("new_folder", "").strip():
        feed.folder = _folder_from_post(request)
        fields.append("folder")
    if fields:
        feed.save(update_fields=fields)
    if is_htmx(request):
        return _manage_row(request, feed.pk)
    messages.success(request, f"Saved {feed.name}.")
    return redirect(safe_next(request, feed.get_absolute_url()))


@login_required
@require_POST
def feed_fetch(request, pk):
    feed = get_object_or_404(Feed.objects.defer("icon"), pk=pk)
    stats = fetcher.fetch_feeds([feed], workers=1)
    feed.refresh_from_db(fields=["last_error"])
    if is_htmx(request):
        return _manage_row(request, feed.pk)
    if feed.last_error:
        messages.error(request, f"Fetch failed: {feed.last_error}")
    else:
        n = stats.new_items
        messages.success(request, f"{n} new item{'s' if n != 1 else ''}." if n else "No new items.")
    return redirect(safe_next(request, feed.get_absolute_url()))


@login_required
@require_POST
def feed_unsubscribe(request, pk):
    feed = get_object_or_404(Feed.objects.defer("icon"), pk=pk)
    name = feed.name
    feed.delete()
    if is_htmx(request):
        return HttpResponse("")
    messages.success(request, f"Unsubscribed from {name}.")
    return redirect(safe_next(request))


@login_required
@require_POST
def folder_create(request):
    name = request.POST.get("name", "").strip()[:200]
    if not name:
        messages.error(request, "A folder needs a name.")
    elif Folder.objects.filter(name__iexact=name).exists():
        messages.info(request, f"There's already a folder called {name}.")
    else:
        Folder.objects.create(name=name)
        messages.success(request, f"Created folder {name}.")
    return redirect(safe_next(request, "feeds:manage"))


@login_required
@require_POST
def folder_update(request, pk):
    folder = get_object_or_404(Folder, pk=pk)
    name = request.POST.get("name", "").strip()[:200]
    if not name:
        messages.error(request, "A folder needs a name.")
    elif Folder.objects.filter(name__iexact=name).exclude(pk=pk).exists():
        messages.error(request, f"There's already a folder called {name}.")
    else:
        folder.name = name
        folder.save(update_fields=["name"])
        messages.success(request, f"Renamed to {name}.")
    return redirect(safe_next(request, folder.get_absolute_url()))


@login_required
@require_POST
def folder_delete(request, pk):
    folder = get_object_or_404(Folder, pk=pk)
    name = folder.name
    folder.delete()  # feeds move to the top level (SET_NULL)
    messages.success(request, f"Deleted folder {name}; its feeds are now ungrouped.")
    return redirect(safe_next(request))


def _feeds_with_stats():
    since = timezone.now() - timedelta(days=30)
    return (
        Feed.objects.defer("icon")
        .select_related("folder")
        .annotate(
            total=Count("items"),
            unread=Count("items", filter=Q(items__is_read=False)),
            recent=Count("items", filter=Q(items__published__gte=since)),
            latest=Max("items__published"),
        )
    )


MANAGE_SORTS = {
    "name": lambda f: f.name.lower(),
    "folder": lambda f: ((f.folder.name.lower() if f.folder else "~"), f.name.lower()),
    "activity": lambda f: -f.recent,
    "latest": lambda f: -(f.latest.timestamp() if f.latest else 0),
    "status": lambda f: (-f.error_count, f.name.lower()),
}


@login_required
def manage(request):
    sort = request.GET.get("sort", "folder")
    feeds = sorted(_feeds_with_stats(), key=MANAGE_SORTS.get(sort, MANAGE_SORTS["folder"]))
    ctx = {
        "feeds": feeds,
        "sort": sort,
        "sort_options": [
            ("folder", "Folder"),
            ("name", "Name"),
            ("activity", "Activity"),
            ("latest", "Latest item"),
            ("status", "Status"),
        ],
        "all_folders": Folder.objects.annotate(n=Count("feeds")),
        "prefs": Preferences.load(),
        "failing": sum(1 for f in feeds if f.is_failing),
    }
    return render(request, "feeds/manage.html", ctx)


def _background_fetch(feed_ids=None):
    def work():
        try:
            close_old_connections()
            feeds = None if feed_ids is None else Feed.objects.defer("icon").filter(pk__in=feed_ids)
            stats = fetcher.run(feeds=feeds, lock_timeout=600)
            log.info("Background fetch: %s", stats)
        except Exception:
            log.exception("Background fetch failed")
        finally:
            close_old_connections()

    threading.Thread(target=work, daemon=True, name="background-fetch").start()


@login_required
@require_POST
def fetch_all(request):
    Feed.objects.update(next_fetch_at=timezone.now())
    _background_fetch()
    messages.success(request, "Fetching all feeds in the background. Counts update as they come in.")
    return redirect("feeds:manage")


@login_required
def opml_export(request):
    stamp = timezone.localdate().isoformat()
    response = HttpResponse(opml.export_opml(), content_type="text/x-opml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="reader-subscriptions-{stamp}.opml"'
    return response


@login_required
@require_POST
def opml_import(request):
    upload = request.FILES.get("opml")
    if not upload:
        messages.error(request, "Choose an OPML file to import.")
        return redirect("feeds:manage")
    try:
        added, skipped = opml.import_opml(upload.read())
    except opml.OPMLError as e:
        messages.error(request, str(e))
        return redirect("feeds:manage")
    if added:
        _background_fetch()
        messages.success(
            request,
            f"Imported {added} feed{'s' if added != 1 else ''}"
            + (f" ({skipped} already there or invalid)" if skipped else "")
            + ". Fetching them in the background.",
        )
    else:
        messages.info(request, f"Nothing new to import ({skipped} skipped).")
    return redirect("feeds:manage")
