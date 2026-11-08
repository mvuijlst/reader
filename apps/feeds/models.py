from urllib.parse import urlsplit

from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse


class Folder(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = [Lower("name")]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("feeds:folder", kwargs={"pk": self.pk})

    @property
    def scope(self):
        return f"folder:{self.pk}"


class Feed(models.Model):
    url = models.URLField(max_length=2000, unique=True)
    title = models.CharField(max_length=500, blank=True, help_text="As published by the feed.")
    custom_title = models.CharField(max_length=500, blank=True, help_text="Your rename, if any.")
    site_url = models.URLField(max_length=2000, blank=True)
    folder = models.ForeignKey(
        Folder, null=True, blank=True, on_delete=models.SET_NULL, related_name="feeds"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Polling state
    etag = models.CharField(max_length=500, blank=True)
    last_modified = models.CharField(max_length=200, blank=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_parsed_at = models.DateTimeField(
        null=True, blank=True, help_text="Last fetch that returned content (not a 304)."
    )
    next_fetch_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    # Favicon, stored inline: a few KB per feed, served by our own view.
    icon = models.BinaryField(null=True, blank=True, editable=False)
    icon_type = models.CharField(max_length=100, blank=True)
    icon_checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("feeds:feed", kwargs={"pk": self.pk})

    @property
    def name(self):
        return self.custom_title or self.title or urlsplit(self.url).hostname or self.url

    @property
    def scope(self):
        return f"feed:{self.pk}"

    @property
    def has_icon(self):
        return bool(self.icon_type)

    @property
    def is_failing(self):
        return self.error_count >= 3

    @property
    def link(self):
        return self.site_url or self.url


class Item(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="items")
    guid = models.TextField()
    guid_hash = models.CharField(max_length=40)
    url = models.URLField(max_length=2000, blank=True)
    title = models.TextField(blank=True)
    author = models.CharField(max_length=500, blank=True)
    content = models.TextField(blank=True, help_text="Sanitized HTML.")
    summary = models.TextField(blank=True, help_text="Plain-text snippet for list view.")
    image = models.URLField(max_length=2000, blank=True, help_text="Thumbnail, when the content has none.")
    enclosures = models.JSONField(default=list, blank=True)

    published = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(help_text="Last time the entry was present in its feed.")

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    is_starred = models.BooleanField(default=False)
    starred_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["feed", "guid_hash"], name="unique_item_per_feed"),
        ]
        indexes = [
            models.Index(fields=["is_read", "published"], name="item_read_pub"),
            models.Index(fields=["feed", "is_read", "published"], name="item_feed_read_pub"),
            models.Index(fields=["is_starred", "published"], name="item_star_pub"),
        ]

    def __str__(self):
        return self.title or self.url or self.guid


class Preferences(models.Model):
    """Single-row table of reader preferences (there is only one user)."""

    VIEW_EXPANDED = "expanded"
    VIEW_LIST = "list"
    VIEW_CHOICES = [(VIEW_EXPANDED, "Expanded"), (VIEW_LIST, "List")]

    view_mode = models.CharField(max_length=10, choices=VIEW_CHOICES, default=VIEW_EXPANDED)
    unread_only = models.BooleanField(default=True)
    oldest_first = models.BooleanField(default=False)
    mark_read_on_scroll = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "preferences"

    def __str__(self):
        return "Preferences"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
