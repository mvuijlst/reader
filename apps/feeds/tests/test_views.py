from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.feeds.models import Feed, Folder, Item, Preferences


def make_item(feed, n, **kwargs):
    now = timezone.now()
    defaults = {
        "guid": f"{feed.pk}-{n}",
        "guid_hash": f"{feed.pk:08d}{n:032d}"[-40:],
        "title": f"Item {n}",
        "url": f"https://example.com/{n}",
        "content": f"<p>Body {n}</p>",
        "summary": f"Body {n}",
        "published": now - timedelta(minutes=n),
        "last_seen_at": now,
    }
    defaults.update(kwargs)
    return Item.objects.create(feed=feed, **defaults)


@override_settings(READER_PAGE_SIZE=5)
class ReaderViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("me", password="pw-for-tests-only")
        self.client.force_login(self.user)
        self.folder = Folder.objects.create(name="Tech")
        self.feed = Feed.objects.create(url="https://a.example/feed", title="Feed A", folder=self.folder)
        self.other = Feed.objects.create(url="https://b.example/feed", title="Feed B")
        self.items = [make_item(self.feed, n) for n in range(8)]
        make_item(self.other, 100, title="Something about zebras")

    def test_login_required(self):
        self.client.logout()
        for name in ("feeds:all", "feeds:starred", "feeds:manage", "feeds:sidebar"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login/", response["Location"])
        response = self.client.post(reverse("feeds:item-state", args=[self.items[0].pk]), {"read": 1})
        self.assertEqual(response.status_code, 302)

    def test_full_page_and_htmx_partial(self):
        response = self.client.get(reverse("feeds:all"))
        self.assertContains(response, 'id="nav-tree"')
        self.assertContains(response, "Feed A")
        self.assertEqual(response.context["unread_count"], 9)

        response = self.client.get(reverse("feeds:all"), HTTP_HX_REQUEST="true", HTTP_HX_TARGET="main")
        self.assertNotContains(response, 'id="nav-tree"')
        self.assertContains(response, 'id="stream"')
        self.assertEqual(response["Content-Security-Policy"].split(";")[0], "default-src 'self'")

    def test_cursor_pagination_survives_reading(self):
        first = self.client.get(reverse("feeds:feed", args=[self.feed.pk]))
        page1 = [i.pk for i in first.context["items"]]
        self.assertEqual(len(page1), 5)
        next_url = first.context["next_url"]
        # Reading page 1 must not make page 2 skip anything (offsets would).
        Item.objects.filter(pk__in=page1).update(is_read=True)
        second = self.client.get(next_url)
        page2 = [i.pk for i in second.context["items"]]
        self.assertEqual(len(page2), 3)
        self.assertFalse(set(page1) & set(page2))
        self.assertIsNone(second.context["next_url"])

    def test_item_state(self):
        item = self.items[0]
        url = reverse("feeds:item-state", args=[item.pk])
        data = self.client.post(url, {"read": "1"}).json()
        self.assertTrue(data["is_read"] and data["changed"])
        data = self.client.post(url, {"read": "1"}).json()
        self.assertFalse(data["changed"])
        data = self.client.post(url, {"starred": "1"}).json()
        self.assertTrue(data["is_starred"])
        item.refresh_from_db()
        self.assertIsNotNone(item.starred_at)

    def test_mark_all_read_respects_scope_and_max_id(self):
        max_id = Item.objects.order_by("-id").values_list("id", flat=True)[0]
        late = make_item(self.feed, 50)  # arrived after the page was loaded
        response = self.client.post(reverse("feeds:mark-all-read"), {"scope": f"folder:{self.folder.pk}", "max_id": max_id})
        self.assertEqual(response.json()["marked"], 8)
        late.refresh_from_db()
        self.assertFalse(late.is_read)
        self.assertFalse(Item.objects.get(feed=self.other).is_read)
        response = self.client.post(reverse("feeds:mark-all-read"), {"scope": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_unread_only_preference(self):
        Item.objects.filter(pk=self.items[0].pk).update(is_read=True)
        response = self.client.get(reverse("feeds:feed", args=[self.feed.pk]) + "?x=1")
        self.assertNotIn(self.items[0], response.context["items"])
        self.client.post(reverse("feeds:prefs"), {"unread_only": "0"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertFalse(Preferences.load().unread_only)
        response = self.client.get(reverse("feeds:feed", args=[self.feed.pk]))
        self.assertIn(self.items[0], response.context["items"])

    def test_search(self):
        response = self.client.get(reverse("feeds:search"), {"q": "zebra"})
        self.assertEqual([i.title for i in response.context["items"]], ["Something about zebras"])

    def test_sidebar_counts(self):
        response = self.client.get(reverse("feeds:sidebar"))
        self.assertContains(response, 'data-count="folder:%d" data-n="8"' % self.folder.pk)
        self.assertContains(response, 'data-count="all" data-n="9"')

    def test_manage_and_feed_update(self):
        self.assertEqual(self.client.get(reverse("feeds:manage")).status_code, 200)
        response = self.client.post(
            reverse("feeds:feed-update", args=[self.other.pk]),
            {"custom_title": "Renamed", "folder": "", "new_folder": "Fresh"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, "Renamed")
        self.other.refresh_from_db()
        self.assertEqual(self.other.folder.name, "Fresh")
        self.assertEqual(self.other.name, "Renamed")

    def test_unsubscribe_deletes_items(self):
        self.client.post(reverse("feeds:feed-unsubscribe", args=[self.feed.pk]))
        self.assertFalse(Item.objects.filter(feed_id=self.feed.pk).exists())

    def test_opml_export(self):
        response = self.client.get(reverse("feeds:opml-export"))
        self.assertIn(b'xmlUrl="https://a.example/feed"', response.content)
        self.assertIn("attachment", response["Content-Disposition"])
