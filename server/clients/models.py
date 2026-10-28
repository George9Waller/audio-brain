from django.db import models


class RateLimitLane(models.Model):
    """One shared rate-limit lane (clients.base.SharedLane). Times are seconds since the epoch, by the database's clock."""

    name = models.CharField(max_length=64, primary_key=True)
    next_at = models.FloatField(default=0)  # the earliest a request not yet booked may start
    held_until = models.FloatField(default=0)  # no request before this: the service said it was busy

    def __str__(self):
        return self.name
