from django.contrib import admin

from sets.models import Set, SetTrack


class SetTrackInline(admin.TabularInline):
    model = SetTrack
    fields = ["position", "cue", "artist_name", "track_name", "label_name", "track"]
    raw_id_fields = ["track"]
    extra = 0


@admin.register(Set)
class SetAdmin(admin.ModelAdmin):
    list_display = ["track", "status", "score", "reasons", "tracklist_status", "tracklist_title"]
    list_editable = ["status"]
    list_filter = ["status", "reviewed", "tracklist_status"]
    search_fields = ["track__name", "track__artist__name"]
    raw_id_fields = ["track"]
    inlines = [SetTrackInline]

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data:
            obj.reviewed = True
        super().save_model(request, obj, form, change)
