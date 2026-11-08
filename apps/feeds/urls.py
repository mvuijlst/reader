from django.urls import path

from . import views

app_name = "feeds"

urlpatterns = [
    path("", views.index, name="index"),
    # Streams (full page, or just the main pane for htmx)
    path("all/", views.all_items, name="all"),
    path("starred/", views.starred, name="starred"),
    path("folder/<int:pk>/", views.folder_stream, name="folder"),
    path("feed/<int:pk>/", views.feed_stream, name="feed"),
    path("search/", views.search, name="search"),
    path("sidebar/", views.sidebar, name="sidebar"),
    # Subscriptions
    path("subscribe/", views.subscribe, name="subscribe"),
    path("feed/<int:pk>/icon/", views.feed_icon, name="icon"),
    path("feed/<int:pk>/edit/", views.feed_update, name="feed-update"),
    path("feed/<int:pk>/fetch/", views.feed_fetch, name="feed-fetch"),
    path("feed/<int:pk>/unsubscribe/", views.feed_unsubscribe, name="feed-unsubscribe"),
    path("folder/new/", views.folder_create, name="folder-create"),
    path("folder/<int:pk>/edit/", views.folder_update, name="folder-update"),
    path("folder/<int:pk>/delete/", views.folder_delete, name="folder-delete"),
    path("manage/", views.manage, name="manage"),
    path("manage/fetch-all/", views.fetch_all, name="fetch-all"),
    path("opml/export/", views.opml_export, name="opml-export"),
    path("opml/import/", views.opml_import, name="opml-import"),
    # JSON endpoints used by reader.js
    path("api/items/<int:pk>/", views.item_state, name="item-state"),
    path("api/mark-all-read/", views.mark_all_read, name="mark-all-read"),
    path("api/prefs/", views.set_prefs, name="prefs"),
]
