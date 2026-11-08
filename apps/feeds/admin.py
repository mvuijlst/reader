from django.contrib import admin

from .models import Feed, Folder, Item, Preferences


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ["name", "folder", "last_success_at", "next_fetch_at", "error_count"]
    list_filter = ["folder"]
    search_fields = ["title", "custom_title", "url"]
    readonly_fields = ["created_at", "last_fetched_at", "last_success_at", "icon_checked_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).defer("icon")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["title", "feed", "published", "is_read", "is_starred"]
    list_filter = ["is_read", "is_starred", "feed"]
    search_fields = ["title", "url"]
    date_hierarchy = "published"
    list_select_related = ["feed"]
    raw_id_fields = ["feed"]


admin.site.register(Preferences)
