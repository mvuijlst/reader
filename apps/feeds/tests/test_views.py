from datetime import timedelta
from unittest import mock

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
        self.assertContains(response, 'data-unread-only="1"')  # reader.js removes opened-on-site items
        self.client.post(reverse("feeds:prefs"), {"unread_only": "0"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertFalse(Preferences.load().unread_only)
        response = self.client.get(reverse("feeds:feed", args=[self.feed.pk]))
        self.assertIn(self.items[0], response.context["items"])

    def test_card_view(self):
        Item.objects.filter(pk=self.items[0].pk).update(thumbnail="https://example.com/pic.jpg")
        self.client.post(reverse("feeds:prefs"), {"view_mode": "cards"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(Preferences.load().view_mode, "cards")
        response = self.client.get(reverse("feeds:all"))
        self.assertContains(response, 'class="stream view-cards"')
        self.assertContains(response, 'class="item card unread"', count=1)  # the one with a picture
        self.assertContains(response, "card-text-only", count=4)  # rest of the page of 5
        self.assertContains(response, 'src="https://example.com/pic.jpg"')
        self.assertContains(response, 'data-value="cards" aria-pressed="true"')
        # Unknown view modes are ignored.
        self.client.post(reverse("feeds:prefs"), {"view_mode": "bogus"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(Preferences.load().view_mode, "cards")

    def test_magazine_view(self):
        Item.objects.filter(feed=self.feed).update(thumbnail="https://example.com/pic.jpg")
        self.client.post(reverse("feeds:prefs"), {"view_mode": "magazine"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(Preferences.load().view_mode, "magazine")

        # Folder view: feed names shown. READER_PAGE_SIZE is 5 here, and the page
        # runs on to the end of the block (hero block 3 + row 3 = 6 items).
        response = self.client.get(reverse("feeds:folder", args=[self.folder.pk]))
        self.assertContains(response, 'class="stream view-magazine"')
        self.assertContains(response, "mag-hero", count=1)
        self.assertContains(response, 'class="mag-pill">Feed A<')
        self.assertContains(response, 'class="mag-source">Feed A<')
        self.assertEqual(len(response.context["items"]), 6)
        next_url = response.context["next_url"]
        self.assertIn("mb=2", next_url)

        # The next page continues the cycle with its second row: the last two items.
        more = self.client.get(next_url)
        self.assertEqual([i.title for i in more.context["items"]], ["Item 6", "Item 7"])
        self.assertNotContains(more, "mag-featured")
        self.assertIsNone(more.context["next_url"])

        # Every card carries its backdrop pattern and tint (used when it has no picture).
        self.assertRegex(response.content.decode(), r'class="item mag pat-[a-z]+ tint-\d')

        # Single feed view: no feed names on the cards.
        response = self.client.get(reverse("feeds:feed", args=[self.feed.pk]))
        self.assertNotContains(response, "mag-pill")
        self.assertNotContains(response, "mag-source")

    def test_summary_only_detection(self):
        short = Feed.objects.create(url="https://news.example/rss", title="News", site_url="https://www.news.example/")
        for n in range(6):
            make_item(short, n, content="<p>One sentence about the story.</p>", url=f"https://www.news.example/{n}")
        self.assertTrue(short.looks_summary_only())
        Item.objects.filter(feed=self.feed).update(content="<p>" + "Long text. " * 60 + "</p>")
        self.assertFalse(self.feed.looks_summary_only())
        # Pictures (comics) and audio (podcasts) are content, not summaries.
        Item.objects.filter(feed=short).update(content='<p><img src="https://x/comic.png"></p>')
        self.assertFalse(short.looks_summary_only())
        Item.objects.filter(feed=short).update(content="<p>Episode 12.</p>", enclosures=[{"url": "https://x/a.mp3", "type": "audio/mpeg"}])
        self.assertFalse(short.looks_summary_only())
        # Too few items to judge.
        Item.objects.filter(feed=short).update(enclosures=[])
        Item.objects.filter(feed=short, guid__endswith=("-5")).delete()
        Item.objects.filter(feed=short, guid__endswith=("-4")).delete()
        self.assertFalse(short.looks_summary_only())

    def test_open_on_site(self):
        news = Feed.objects.create(url="https://news.example/rss", title="News", site_url="https://www.news.example/")
        for n in range(6):
            make_item(news, n, content="<p>One sentence.</p>", url=f"https://www.news.example/story-{n}")
        url = reverse("feeds:feed", args=[news.pk])

        # Undecided summary-only feed: the header offers it.
        response = self.client.get(url)
        self.assertContains(response, "Open on news.example")
        self.assertNotContains(response, "data-open-site")

        # Switching it on (from the offer, or the menu).
        response = self.client.post(reverse("feeds:feed-update", args=[news.pk]), {"open_on_site": "1", "next": url}, follow=True)
        news.refresh_from_db()
        self.assertIs(news.open_on_site, True)
        self.assertContains(response, "now open on news.example")
        self.assertNotContains(response, "stream-offer")
        self.assertContains(response, "Read on news.example")

        def marked_correctly(page):
            # Exactly the News items on the page carry the marker.
            expected = sum(1 for item in page.context["items"] if item.feed_id == news.pk)
            self.assertGreater(len(page.context["items"]), 0)
            self.assertContains(page, 'data-open-site="1"', count=expected)

        marked_correctly(response)
        # Every view marks the items, and so do mixed streams like All items.
        for mode in ("list", "cards", "magazine"):
            self.client.post(reverse("feeds:prefs"), {"view_mode": mode}, HTTP_X_REQUESTED_WITH="fetch")
            marked_correctly(self.client.get(url))
            marked_correctly(self.client.get(reverse("feeds:all")))

        # Manage page checkbox: hidden "0" plus checked "1" means on; "0" alone means off.
        manage = reverse("feeds:feed-update", args=[news.pk])
        self.client.post(manage, {"open_on_site": ["0"]}, HTTP_HX_REQUEST="true")
        news.refresh_from_db()
        self.assertIs(news.open_on_site, False)
        self.client.post(manage, {"open_on_site": ["0", "1"]}, HTTP_HX_REQUEST="true")
        news.refresh_from_db()
        self.assertIs(news.open_on_site, True)

        # Declining ("Keep reading here") stops the offer for good.
        self.client.post(manage, {"open_on_site": "0", "next": url})
        self.assertNotContains(self.client.get(url), "stream-offer")

    def test_disregard_button_in_every_view(self):
        url = reverse("feeds:all")
        # Per item: in the row or entry title; cards and magazine also carry one in
        # the hidden copy of the article that the reading panel shows.
        for mode, per_item in (("expanded", 1), ("list", 1), ("cards", 2), ("magazine", 2)):
            self.client.post(reverse("feeds:prefs"), {"view_mode": mode}, HTTP_X_REQUESTED_WITH="fetch")
            response = self.client.get(url)
            expected = per_item * len(response.context["items"])
            self.assertContains(response, 'data-action="disregard"', count=expected, msg_prefix=mode)

    def test_untitled_items(self):
        Item.objects.all().delete()
        make_item(
            self.feed, 1, title="", content="<p>First line here</p><p>Second paragraph</p>",
            summary="First line here Second paragraph",
        )
        url = reverse("feeds:all")

        def show(mode):
            self.client.post(reverse("feeds:prefs"), {"view_mode": mode}, HTTP_X_REQUESTED_WITH="fetch")
            return self.client.get(url)

        for mode in ("expanded", "list", "cards", "magazine"):
            self.assertNotContains(show(mode), "(untitled)", msg_prefix=mode)
        # List: the first line is the title, the rest the snippet.
        response = show("list")
        self.assertContains(response, '<span class="row-title">First line here</span>')
        self.assertContains(response, "Second paragraph")
        # Cards and magazine: no heading at all.
        self.assertNotContains(show("cards"), 'class="card-title"')
        self.assertNotContains(show("magazine"), 'class="mag-title"')
        # Expanded: the date and time stand in as the title.
        item = Item.objects.get()
        from apps.feeds.templatetags.reader_tags import readerdatetime
        self.assertContains(show("expanded"), f">{readerdatetime(item.published)}</a>")

    def test_search(self):
        response = self.client.get(reverse("feeds:search"), {"q": "zebra"})
        self.assertEqual([i.title for i in response.context["items"]], ["Something about zebras"])

    def test_sidebar_counts(self):
        response = self.client.get(reverse("feeds:sidebar"))
        self.assertContains(response, 'data-count="folder:%d" data-n="8"' % self.folder.pk)
        self.assertContains(response, 'data-count="all" data-n="9"')

    def test_sidebar_type_icons(self):
        yt = Feed.objects.create(url="https://www.youtube.com/feeds/videos.xml?channel_id=UC123", title="Channel")
        pod = Feed.objects.create(url="https://pod.example/feed", title="Pod")
        for n in range(3):
            make_item(pod, n, enclosures=[{"url": f"https://pod.example/{n}.mp3", "type": "audio/mpeg"}])
        make_item(pod, 9)  # one episode without audio doesn't change what it is
        response = self.client.get(reverse("feeds:sidebar"))
        html = response.content.decode()
        self.assertIn('<use href="#i-kind-youtube"/>', html)
        self.assertIn('<use href="#i-kind-podcast"/>', html)
        self.assertIn('<use href="#i-kind-rss"/>', html)  # Feed A and Feed B: no favicon
        self.assertNotIn("favicon-letter", html)
        self.assertEqual((yt.kind, Feed.objects.get(pk=pod.pk).kind), ("youtube", "rss"))  # podcast needs the sidebar's data
        # Explorer-style header actions are there.
        for action in ("toggle-tree", "subscribe", "new-folder", "reload", "toggle-read-feeds", "collapse-all"):
            self.assertIn(f'data-action="{action}"', html)

    def test_new_folder_from_sidebar(self):
        url = reverse("feeds:folder-create")
        data = self.client.post(url, {"name": "Reading"}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual(data, {"created": True, "message": "Created folder Reading."})
        data = self.client.post(url, {"name": "reading"}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertFalse(data["created"])
        self.assertEqual(Folder.objects.filter(name__iexact="reading").count(), 1)
        self.assertEqual(self.client.post(url, {"name": " "}, HTTP_X_REQUESTED_WITH="fetch").status_code, 400)
        # The Manage page form still redirects.
        self.assertEqual(self.client.post(url, {"name": "Later"}).status_code, 302)

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

    def test_move_feed(self):
        url = reverse("feeds:feed-move", args=[self.other.pk])
        data = self.client.post(url, {"folder": self.folder.pk}).json()
        self.assertEqual(data, {"feed": self.other.pk, "folder": self.folder.pk, "folder_name": "Tech"})
        self.other.refresh_from_db()
        self.assertEqual(self.other.folder, self.folder)

        # Out of its folder.
        self.assertIsNone(self.client.post(url, {"folder": ""}).json()["folder"])
        self.other.refresh_from_db()
        self.assertIsNone(self.other.folder)

        # Into a new folder; an existing name (any case) is reused, not duplicated.
        data = self.client.post(url, {"new_folder": "Comics"}).json()
        self.assertEqual(data["folder_name"], "Comics")
        data2 = self.client.post(url, {"new_folder": "comics"}).json()
        self.assertEqual(data2["folder"], data["folder"])
        self.assertEqual(Folder.objects.filter(name__iexact="comics").count(), 1)

        self.assertEqual(self.client.post(url, {"folder": "9999"}).status_code, 404)
        self.assertEqual(self.client.post(url, {"folder": "x"}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"folder": ""}).status_code, 302)

    def test_unsubscribe_deletes_items(self):
        self.client.post(reverse("feeds:feed-unsubscribe", args=[self.feed.pk]))
        self.assertFalse(Item.objects.filter(feed_id=self.feed.pk).exists())

    def test_manage_rows_can_be_selected_for_bulk_actions(self):
        response = self.client.get(reverse("feeds:manage"))
        self.assertContains(response, 'id="bulk-form"')
        self.assertContains(response, f'class="bulk-check" name="feeds" value="{self.feed.pk}" form="bulk-form"')
        self.assertContains(response, "js/manage")

    def test_bulk_unsubscribe(self):
        third = Feed.objects.create(url="https://c.example/feed", title="Feed C")
        back = reverse("feeds:manage") + "?sort=name"
        response = self.client.post(
            reverse("feeds:feeds-bulk"),
            {"action": "unsubscribe", "feeds": [self.feed.pk, third.pk, "nonsense", 9999], "next": back},
        )
        self.assertRedirects(response, back, fetch_redirect_response=False)  # back to the same sort
        self.assertEqual(list(Feed.objects.values_list("pk", flat=True)), [self.other.pk])
        self.assertFalse(Item.objects.filter(feed_id=self.feed.pk).exists())
        self.assertContains(self.client.get(back), "Unsubscribed from 2 feeds.")

    def test_bulk_fetch_runs_in_the_background(self):
        later = timezone.now() + timedelta(hours=1)
        Feed.objects.update(next_fetch_at=later)
        with mock.patch("apps.feeds.views._background_fetch") as background:
            self.client.post(reverse("feeds:feeds-bulk"), {"action": "fetch", "feeds": [self.other.pk]})
        background.assert_called_once_with([self.other.pk])
        self.other.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertLess(self.other.next_fetch_at, later)  # due now, in case the background run can't start
        self.assertEqual(self.feed.next_fetch_at, later)  # unselected feeds untouched

    def test_bulk_needs_a_selection_and_a_known_action(self):
        url = reverse("feeds:feeds-bulk")
        cases = [
            ({"action": "unsubscribe"}, "Select one or more feeds first."),
            ({"action": "explode", "feeds": [self.feed.pk]}, "Unknown action."),
        ]
        for data, message in cases:
            with self.subTest(message), mock.patch("apps.feeds.views._background_fetch") as background:
                self.assertContains(self.client.post(url, data, follow=True), message)
                background.assert_not_called()
        self.assertEqual(Feed.objects.count(), 2)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_opml_export(self):
        response = self.client.get(reverse("feeds:opml-export"))
        self.assertIn(b'xmlUrl="https://a.example/feed"', response.content)
        self.assertIn("attachment", response["Content-Disposition"])
