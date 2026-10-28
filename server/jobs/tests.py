from datetime import timedelta
from unittest import mock

import numpy as np
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TestCase, override_settings
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


class QueueTests(TestCase):
    def test_a_job_is_queued_once(self):
        first = runner.queue("mixes", JobRun.APP)
        self.assertEqual(runner.queue("mixes", JobRun.SCHEDULE), first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            JobRun.objects.create(job="mixes", trigger=JobRun.APP)

    def test_a_run_starts_only_if_still_queued(self):
        run = JobRun.objects.create(job="mixes", trigger=JobRun.APP, state=JobRun.CANCELLED)
        with mock.patch.object(runner, "execute") as execute, mock.patch.object(runner, "close_old_connections"):  # it would close the test's
            runner.Worker.run_in_thread(run.pk)
            execute.assert_not_called()
            queued = JobRun.objects.create(job="mixes", trigger=JobRun.APP)
            runner.Worker.run_in_thread(queued.pk)
            runner.Worker.run_in_thread(queued.pk)  # a second worker that also saw it queued
        execute.assert_called_once()
        self.assertEqual(JobRun.objects.get(pk=queued.pk).state, JobRun.RUNNING)


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
