from django.db import models

from catalogue.models import Track


class EnrichmentAttempt(models.Model):
    """The latest result of one enrichment stage for one track.

    A stage works on tracks without an attempt, so runs are resumable and `not_found` tracks aren't retried every run.
    """

    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR = "error"
    STATUSES = [(OK, "OK"), (NOT_FOUND, "Not found"), (ERROR, "Error")]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="enrichment_attempts")
    stage = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=STATUSES)
    source = models.CharField(max_length=30, blank=True)
    detail = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["track", "stage"]

    def __str__(self):
        return f"{self.track} {self.stage}: {self.status}"
