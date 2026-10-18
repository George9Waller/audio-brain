from django.contrib import admin

from library.models import Favourite


@admin.register(Favourite)
class FavouriteAdmin(admin.ModelAdmin):
    list_display = ["__str__", "kind", "platform", "added_at"]
    list_filter = ["platform", "kind"]
    raw_id_fields = ["track", "album", "artist"]
