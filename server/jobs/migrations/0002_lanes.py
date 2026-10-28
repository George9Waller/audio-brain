from django.db import migrations, models


def one_active_run_per_job(apps, schema_editor):
    """Before the constraint: if a job somehow has more than one queued or running run, cancel all but the newest."""
    JobRun = apps.get_model("jobs", "JobRun")
    seen = set()
    for run in JobRun.objects.filter(state__in=["queued", "running"]).order_by("-queued_at"):
        if run.job in seen:
            run.state = "cancelled"
            run.save(update_fields=["state"])
        seen.add(run.job)


class Migration(migrations.Migration):

    dependencies = [("jobs", "0001_initial")]

    operations = [
        migrations.AddField(model_name="workerstatus", name="lane", field=models.CharField(default="main", max_length=10), preserve_default=False),
        migrations.AlterField(model_name="workerstatus", name="lane", field=models.CharField(max_length=10, unique=True)),
        migrations.RunPython(one_active_run_per_job, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="jobrun",
            constraint=models.UniqueConstraint(condition=models.Q(("state__in", ["queued", "running"])), fields=("job",), name="one_active_run_per_job"),
        ),
    ]
