import os
import threading
from datetime import timedelta
from unittest import mock

import numpy as np
from django.core.management import CommandError, call_command
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from catalogue.deletions import TRACK_DELETIONS, holding_tracks, still_there
from catalogue.models import Artist, Track
from enrichment.identity import merge_tracks
from jobs import runner
from jobs.models import JobRun, WorkerStatus
from jobs.registry import JOBS, LOOKUP, MAIN, SYNC, Job

LOCKED = []  # whether the tracks were held, at each step


def note_lock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = %s AND granted AND mode = 'ExclusiveLock'", [TRACK_DELETIONS])
        LOCKED.append(cursor.fetchone()[0] == 1)


def give_up_waiting():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '200ms'")


class TrackDeletionTests(TestCase):
    def setUp(self):
        artist = Artist.objects.create(name="CID")
        self.track = Track.objects.create(name="Jumpin'", artist=artist)
        self.into = Track.objects.create(name="Jumpin", artist=artist)

    def test_a_merge_waits_while_the_tracks_are_held(self):
        give_up_waiting()
        with holding_tracks():
            with self.assertRaises(OperationalError):
                merge_tracks(self.track, into=self.into)
        self.assertTrue(Track.objects.filter(pk=self.track.pk).exists())
        merge_tracks(self.track, into=self.into)
        self.assertFalse(Track.objects.filter(pk=self.track.pk).exists())

    def test_merges_dont_wait_for_each_other(self):
        give_up_waiting()
        with transaction.atomic():
            merge_tracks(self.track, into=self.into)
            other = Track.objects.create(name="Jumpin (Edit)", artist=self.into.artist)
            merge_tracks(other, into=self.into)  # the shared lock from the first merge is still held

    def test_writers_skip_deleted_tracks(self):
        from recommend import session_vectors
        from recommend.models import TrackVector

        gone = self.track.pk
        self.track.delete()
        vectors = np.ones((2, session_vectors.DIMENSIONS))
        with mock.patch.object(session_vectors, "train_vectors", return_value=([gone, self.into.pk], vectors, [3, 3])):
            session_vectors.build_track_vectors()
        self.assertEqual(list(TrackVector.objects.values_list("track_id", flat=True)), [self.into.pk])
        self.assertEqual(still_there([gone, self.into.pk]), {self.into.pk})


def job(key, lane=MAIN, steps=(), schedule=None):
    return Job(key, key, "", list(steps), schedule=schedule, lane=lane)


class ExecuteTests(TestCase):
    def test_tracks_are_held_from_the_first_to_the_last_models_step(self):
        steps = [("Before", "before"), ("Models 1", "models1"), ("Models 2", "models2"), ("After", "after")]
        LOCKED.clear()
        with mock.patch.dict(JOBS, {"test": job("test", steps=steps)}), mock.patch.object(runner, "HOLDS_TRACKS", {"models1", "models2"}), \
                mock.patch.object(runner, "call", side_effect=lambda target, log: note_lock()):
            run = runner.execute(JobRun.objects.create(job="test", trigger=JobRun.CLI))
        self.assertEqual(run.state, JobRun.SUCCEEDED)
        self.assertEqual(LOCKED, [False, True, True, False])

    def test_each_step_records_its_peak_memory_and_requests(self):
        from clients.base import usage

        def lookups(target, log):
            if target == "lookups":
                usage()["musicbrainz"].requests += 3
                usage()["musicbrainz"].busy += 1
                usage()["musicbrainz"].waited += 4000.4

        Track.objects.create(name="Archangel", artist=Artist.objects.create(name="Burial"))
        steps = [("Lookups", "lookups"), ("Models", "models")]
        with mock.patch.dict(JOBS, {"test": job("test", steps=steps)}), mock.patch.object(runner, "HOLDS_TRACKS", {"models"}), \
                mock.patch.object(runner, "call", side_effect=lookups), self.assertLogs(runner.logger) as logs:
            run = runner.execute(JobRun.objects.create(job="test", trigger=JobRun.CLI))
        first, second = run.steps
        self.assertGreater(first["peak_mb"], 0)
        self.assertEqual(first["clients"], {"musicbrainz": {"requests": 3, "busy": 1, "waited": 4000}})
        self.assertNotIn("clients", second)  # counted afresh for each step
        self.assertNotIn("catalogue", first)
        self.assertEqual(second["catalogue"]["tracks"], 1)
        self.assertIn("musicbrainz: 3 requests, 1 busy, 1h06m waiting", "\n".join(logs.output))

    def test_a_peak_that_couldnt_be_reset_says_so(self):
        with mock.patch.dict(JOBS, {"test": job("test", steps=[("Step", "step")])}), \
                mock.patch.object(runner, "call"), mock.patch.object(runner, "reset_peak", return_value=False):
            run = runner.execute(JobRun.objects.create(job="test", trigger=JobRun.CLI))
        self.assertTrue(run.steps[0]["peak_since_start"])
        self.assertIn("since the run started", runner.describe_peak(run.steps[0]))


class QueueTests(TestCase):
    def test_a_job_is_queued_once(self):
        first = runner.queue("mixes", JobRun.APP)
        self.assertEqual(runner.queue("mixes", JobRun.SCHEDULE), first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobRun.objects.create(job="mixes", trigger=JobRun.APP)

    def test_a_run_starts_only_if_still_queued(self):
        def finish(pk):
            JobRun.objects.filter(pk=pk).update(state=JobRun.SUCCEEDED)
            return 0

        run = JobRun.objects.create(job="mixes", trigger=JobRun.APP, state=JobRun.CANCELLED)
        with mock.patch.object(runner, "run_in_process", side_effect=finish) as started, \
                mock.patch.object(runner, "close_old_connections"):  # it would close the test's
            runner.Worker.run_in_thread(run.pk)
            started.assert_not_called()
            queued = JobRun.objects.create(job="mixes", trigger=JobRun.APP)
            runner.Worker.run_in_thread(queued.pk)
            runner.Worker.run_in_thread(queued.pk)  # a second worker that also saw it queued
        started.assert_called_once_with(queued.pk)
        self.assertEqual(JobRun.objects.get(pk=queued.pk).state, JobRun.SUCCEEDED)


class ProcessTests(TestCase):
    def test_a_run_whose_process_is_killed_fails_and_is_queued_again(self):
        run = JobRun.objects.create(job="models", trigger=JobRun.SCHEDULE)
        with mock.patch.object(runner, "run_in_process", return_value=-9), mock.patch.object(runner, "close_old_connections"):
            runner.Worker.run_in_thread(run.pk)
        run.refresh_from_db()
        retry = JobRun.objects.get(state=JobRun.QUEUED)
        self.assertEqual((run.state, retry.options), (JobRun.FAILED, {"retry_of": run.pk}))
        self.assertIn("killed by SIGKILL, likely for memory", run.error)

    def test_a_run_its_process_finished_is_left_as_recorded(self):
        run = JobRun.objects.create(job="mixes", trigger=JobRun.APP)

        def failed(pk):
            JobRun.objects.filter(pk=pk).update(state=JobRun.FAILED, error="Mixes\nTraceback")
            return 0

        with mock.patch.object(runner, "run_in_process", side_effect=failed), mock.patch.object(runner, "close_old_connections"):
            runner.Worker.run_in_thread(run.pk)
        run.refresh_from_db()
        self.assertEqual((run.state, run.error), (JobRun.FAILED, "Mixes\nTraceback"))
        self.assertFalse(JobRun.objects.filter(state=JobRun.QUEUED).exists())

    def test_run_job_carries_out_only_a_claimed_run(self):
        queued = JobRun.objects.create(job="mixes", trigger=JobRun.APP)
        with self.assertRaises(CommandError):
            call_command("run_job", run=queued.pk)
        JobRun.objects.filter(pk=queued.pk).update(state=JobRun.RUNNING)
        with mock.patch("jobs.management.commands.run_job.execute") as execute:
            call_command("run_job", run=queued.pk)
        self.assertEqual(execute.call_args.args[0].pk, queued.pk)
        self.assertEqual(threading.current_thread().name, "job-mixes")
        threading.current_thread().name = "MainThread"

    def test_describing_an_exit(self):
        self.assertEqual(runner.describe_exit(1), "exit code 1")
        self.assertEqual(runner.describe_exit(-15), "killed by SIGTERM")


class SeparateProcessTests(TransactionTestCase):
    """A real run in a process of its own, against the test database (so not inside a test's transaction)."""

    def test_the_worker_runs_a_job_in_another_process(self):
        run = JobRun.objects.create(job="discovery_summary", trigger=JobRun.APP)
        with mock.patch.dict(os.environ, {"POSTGRES_DB": connection.settings_dict["NAME"]}):
            runner.Worker.run_in_thread(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.state, JobRun.SUCCEEDED, run.error)
        self.assertGreater(run.steps[0]["peak_mb"], 0)


class RecoverTests(TestCase):
    def interrupted(self, key="models", trigger=JobRun.SCHEDULE, hours_ago=1, **options):
        return JobRun.objects.create(job=key, trigger=trigger, state=JobRun.RUNNING, options=options, started_at=timezone.now() - timedelta(hours=hours_ago))

    def test_a_scheduled_main_run_is_queued_again_once(self):
        run = self.interrupted()
        runner.Worker().recover()
        run.refresh_from_db()
        retry = JobRun.objects.get(state=JobRun.QUEUED)
        self.assertEqual((run.state, retry.job, retry.options), (JobRun.FAILED, "models", {"retry_of": run.pk}))
        self.assertIn(f"queued again as run {retry.pk}", run.error)

        JobRun.objects.filter(pk=retry.pk).update(state=JobRun.RUNNING, started_at=timezone.now())
        runner.Worker().recover()
        self.assertEqual(JobRun.objects.get(pk=retry.pk).state, JobRun.FAILED)
        self.assertFalse(JobRun.objects.filter(state=JobRun.QUEUED).exists())

    def test_other_runs_are_only_marked_failed(self):
        self.interrupted("models", trigger=JobRun.APP)
        self.interrupted("mixes", hours_ago=13)
        self.interrupted("enrichment")  # lookups lane
        runner.Worker().recover()
        self.assertEqual(JobRun.objects.filter(state=JobRun.FAILED).count(), 3)
        self.assertFalse(JobRun.objects.filter(state=JobRun.QUEUED).exists())

    def test_a_worker_leaves_other_lanes_runs_alone(self):
        models = self.interrupted("models")
        runner.Worker(lanes=[SYNC]).recover()
        self.assertEqual(JobRun.objects.get(pk=models.pk).state, JobRun.RUNNING)


@override_settings(API_TOKEN="secret")
class LaneTests(TestCase):
    def test_a_lane_worker_schedules_and_starts_only_its_lanes_jobs(self):
        worker = runner.Worker(lanes=[SYNC])
        worker.started -= timedelta(days=2)  # every scheduled job is due
        JobRun.objects.create(job="mixes", trigger=JobRun.APP)
        with mock.patch.object(runner.threading, "Thread") as thread:
            worker.tick()
        queued = set(JobRun.objects.filter(state=JobRun.QUEUED).values_list("job", flat=True))
        self.assertEqual(queued, {"mixes"} | {job.key for job in JOBS.values() if job.lane == SYNC and job.schedule})
        started = {call.kwargs["name"] for call in thread.call_args_list}
        self.assertEqual(len(started), 1)
        self.assertTrue(started < {f"job-{job.key}" for job in JOBS.values() if job.lane == SYNC})
        self.assertEqual(list(WorkerStatus.objects.values_list("lane", flat=True)), [SYNC])

    def test_every_lane_needs_a_worker(self):
        now = timezone.now()
        for lane in (MAIN, SYNC):
            WorkerStatus.objects.create(lane=lane, started_at=now, seen_at=now, host="h", pid=1)
        self.assertEqual(runner.lanes_alive(), {MAIN: True, LOOKUP: False, SYNC: True})
        health = self.client.get("/api/v1/health", headers={"Authorization": "Bearer secret"}).json()
        self.assertEqual((health["worker_alive"], health["lanes_alive"][LOOKUP]), (False, False))
        jobs = self.client.get("/api/v1/jobs", headers={"Authorization": "Bearer secret"}).json()
        self.assertEqual([w["alive"] for w in jobs["workers"]], [True, False, True])
        self.assertFalse(jobs["worker"]["alive"])
