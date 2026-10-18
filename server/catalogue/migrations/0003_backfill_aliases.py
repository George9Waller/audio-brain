from django.db import migrations


def backfill_aliases(apps, schema_editor):
    """Give every existing track the alias it was scrobbled under, and move its unverified Last.fm MBID onto it."""
    Track = apps.get_model("catalogue", "Track")
    TrackAlias = apps.get_model("catalogue", "TrackAlias")
    TrackAlias.objects.bulk_create(
        TrackAlias(artist_name=track.artist.name, track_name=track.name, album_name=track.album.name if track.album else "", source_mbid=track.mbid, track=track)
        for track in Track.objects.select_related("artist", "album")
    )
    Track.objects.update(mbid="")


class Migration(migrations.Migration):

    dependencies = [
        ("catalogue", "0002_label_track_first_release_date_track_length_ms_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_aliases, migrations.RunPython.noop),
    ]
