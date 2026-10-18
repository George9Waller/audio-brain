from django.contrib import admin
from django.utils import timezone

from discovery.models import Release


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "release_date", "source", "label", "score", "state"]
    list_filter = ["state", "source", "kind"]
    search_fields = ["title", "artists__name"]
    raw_id_fields = ["album", "label"]
    filter_horizontal = ["artists"]
    actions = ["dismiss"]

    @admin.action(description="Dismiss: keep out of Fresh")
    def dismiss(self, request, queryset):
        queryset.update(state=Release.DISMISSED, state_changed_at=timezone.now())
