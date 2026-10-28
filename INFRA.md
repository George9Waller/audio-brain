# audio-brain infrastructure plan

Written 28 Oct 2026, after a health check found the nightly jobs failing. Each task below can be taken cold by one
session. The general rules (branches, migrations, tests, style) are BACKLOG.md's "Rules for every agent"; the
deployment as it stands is `deploy/KUBERNETES.md` and `helm/`. Tasks marked **(you)** need George: the cluster,
AWS, accounts or a decision. This repo is public: write `<node>` and `<tailnet>` in place of real host names, and
never paste a token.

## What the check found (28 Oct)

The API is healthy (0.2–0.8 s per request; `/map` is 7 MB raw, and already gzipped by `GZipMiddleware`). The nightly
pipeline is not:

| Night | models | enrichment | Cause |
|---|---|---|---|
| 24 Oct | failed (31 min) | ok, 13 h | `IntegrityError` writing `recommend_fusedvector`: track 55471 deleted mid-run |
| 25 Oct | failed (40 min) | ok, 8 h | same, in `recommend_tastevector` and `recommend_mappoint` |
| 26 Oct | failed at 03:02 | failed at 03:02 | worker pod died ("The worker stopped while this was running") |
| 27 Oct | ok (48 min) | ok, **17 h** | |
| 28 Oct | failed at 03:09 | failed at 03:09 | worker pod died again |

So taste vectors, contexts, the scorer, the ranker and the map have rebuilt once in five nights. Mixes (06:30) and
candidates (07:00) still succeed, on stale models.

Why:

1. **Enrichment is bound by MusicBrainz.** The candidates job brings in about 6k tracks a night (the catalogue is
   heading from 33k to about 80k), and every one goes through `enrich --stage=identity` at one request a second, with
   long runs of 503 back-offs. Enrichment now takes 2–17 hours.
2. **Enrichment overlaps models.** Enrichment runs on the `lookups` lane (`jobs/registry.py`), in a thread beside the
   `main` lane, in the same worker process (`jobs/runner.py`). That was harmless when enrichment took minutes.
   - *Deleted tracks:* `enrichment.identity.merge_tracks` deletes duplicate tracks while models' `bulk_create`s
     (`recommend/fused.py`, `taste.py`, `atlas.py`, `session_vectors.py`, `contexts.py`) still hold their IDs.
   - *Memory:* both jobs share the worker's 3.5 GiB limit. Models was about 2.2 GB at 33k tracks, and the
     catalogue's highest track ID is now about 62k. The two deaths were OOM kills: on 28 Oct `kubectl describe pod`
     showed the worker's `Last State: OOMKilled` (exit 137) at 03:09:20, and 2 restarts.
3. **Postgres is small too:** a 1 GiB limit, `shared_buffers=256MB` and a 10 Gi volume, which also has to hold the
   backtest clones (about 1.2 GB each, growing with the catalogue).
4. **The MCP `sql` tool is broken on the cluster.** The API's query connection fails with "fe_sendauth: no password
   supplied": the running API pod has an empty `POSTGRES_RO_PASSWORD`, although the AWS secret has the key. The
   pod most likely started before the key reached the Kubernetes Secret (`envFrom` is read only at pod start).

New hardware: a second node with 32 GB RAM and a 10th-gen i5.

### How to check the server without kubectl

The API answers to the `API_TOKEN` in `server/.env`:

```sh
B=http://<node>.<tailnet>.ts.net:30800/api/v1; H="Authorization: Bearer $API_TOKEN"
curl -s -H "$H" $B/jobs                              # worker heartbeat, each job's last run and next due
curl -s -H "$H" "$B/runs?job=models&limit=12"        # a job's recent runs
curl -s -H "$H" $B/runs/747                          # one run: steps, last log lines, error
```

"The worker stopped while this was running" as a run's error means the worker process died mid-run.

---

## Wave 0: quick fixes (all parallel)

**I0 (you). Fix the MCP's SQL tool.** *Folded into I5:* the key is in `audio-brain-aws-secrets` (28 Oct), so the
fresh API pod on the new node reads it, and its `migrate` sets the role's password. I5 checks it.
*Diagnosed 28 Oct:* the ExternalSecret is synced and the Secret has the key (32 characters), but the API pod's
environment doesn't. The pod started 23 Oct 23:40, and the Secret last changed at 23:48. Nothing upstream needs
fixing; any restart of the API pod resolves it. We decided not to restart it on its own, since I5 restarts it
anyway. Only if it still fails afterwards:
- The AWS secret already has `POSTGRES_RO_PASSWORD`; the running API pod doesn't. Check each link in turn:
  1. The Kubernetes Secret has it: `kubectl get secret audio-brain-aws-secrets -o jsonpath='{.data.POSTGRES_RO_PASSWORD}' | base64 -d | wc -c`.
     If empty, check `kubectl get externalsecret audio-brain-aws-secrets` (status, last refresh) and force a refresh
     with `kubectl annotate externalsecret audio-brain-aws-secrets force-sync=$(date +%s) --overwrite`. Check also
     that the key is spelt exactly so, with no trailing space, at the top level of the secret's JSON.
  2. The pod has it: `kubectl exec deploy/audio-brain-api -c api -- printenv POSTGRES_RO_PASSWORD | wc -c`. If the
     Secret has it but the pod doesn't, the pod predates it.
- Then `kubectl rollout restart deploy/audio-brain-api`: its `migrate` init container runs
  `api.query.grant_read_access`, which sets the role's password from the same variable, so the role and the
  connection agree.
- *Done when:* the MCP `sql` tool answers `SELECT count(*) FROM catalogue_track`.

**I1 (you). Give sessions access to the cluster, and the new node's facts.**
- Copy a kubeconfig to the Mac (k3s: `/etc/rancher/k3s/k3s.yaml`, with `server:` set to the old node's Tailscale
  name) and/or add the Mac's SSH key to both nodes, so later sessions can run `kubectl` and see OOM kills.
- Join the new node to the same cluster if it isn't already, and note in this file (with placeholders): its
  `kubernetes.io/hostname` label, CPU model (`lscpu`), free disk and its filesystem, and the StorageClass in use
  (`kubectl get sc`; probably `local-path`, which pins a volume to one node).
- Decide whether the MusicBrainz mirror (I6) has room on it: see I6 for the sizes to check.
- *Done when:* `kubectl get nodes -o wide` works from the Mac and the facts are in the **Cluster facts** section below.

**I2. Stop enrichment's merges from breaking the models job.**
- *Why:* two of the five failures (24, 25 Oct).
- *What:*
  - A Postgres advisory lock for "track deletions": the models job holds it for its whole run (take it in the runner
    around the job, keyed by a `Job` flag such as `holds_catalogue=True`, rather than in each step), and
    `merge_tracks` (`enrichment/identity.py`) takes it per merge, waiting its turn. Enrichment then just pauses its
    merges for the 30–50 minutes models runs. Use `pg_advisory_lock` on a dedicated connection or
    `pg_advisory_xact_lock` inside the merge's transaction; mind that Django's connections are per thread.
  - As a second line of defence, each vector or map writer drops IDs that no longer exist just before its
    `bulk_create` (`Track.objects.filter(pk__in=ids).values_list("pk", flat=True)`, in the same transaction). Note
    `ignore_conflicts` doesn't cover foreign-key failures.
  - Look for other track deletions (`contexts.py` deletes `ContextTrack`s only, which is fine) and route any you find
    through the same lock.
- *Files:* `jobs/registry.py`, `jobs/runner.py`, `enrichment/identity.py`, `recommend/{fused,taste,atlas,session_vectors}.py`.
- *Tests:* a merge during a models run waits; a writer given a deleted track ID skips it.
- *Done when:* tests pass, and after deploy a models run that overlapped enrichment merges succeeds.
- *Built 28 Oct* (`catalogue/deletions.py`): models holds the lock exclusively on a connection of its own, and each
  merge takes it shared inside its transaction, so merges never wait for each other (that matters for I6's thread
  pool). The lock covers a span of steps, not a whole job: from the first to the last `MODELS` step in a run
  (`registry.HOLDS_TRACKS`). That way `discover`, which runs enrichment itself before models, can't deadlock on
  its own lock. Fused, taste, session and map writers drop IDs that are gone (`still_there`). `merge_tracks` is the
  only code that deletes tracks.

**I3. Measure memory and rate-limit waits per step.**
- *Why:* the limits were set from one measurement at 33k tracks. Nobody knows which step peaks, or how much of
  enrichment's time is MusicBrainz waiting (which decides I6), Discogs or Tidal.
- *What:*
  - After each step, the runner logs and stores in the step's JSON the process's peak RSS during that step. On Linux,
    reset the high-water mark with `echo 5 > /proc/self/clear_refs` before the step and read `VmHWM` from
    `/proc/self/status` after it. It's process-wide, so with two lanes running it's the joint peak: say so in the log
    line. On macOS fall back to `resource.getrusage`.
  - `clients.base.APIClient` counts requests, busy answers (429/503) and seconds spent waiting, per client. Each
    enrichment stage logs them at the end ("musicbrainz: 3,412 requests, 611 busy, 4h12m waiting").
  - Also log the catalogue's size at the start of models (tracks, analysed tracks), so memory can be read against it.
- *Files:* `jobs/runner.py`, `clients/base.py`, the enrich command.
- *Done when:* a run's detail (`/runs/{id}`) shows each step's peak memory, and enrichment's log has per-client
  totals.

**I4. Requeue a run the worker died during.**
- *Why:* after a death at 03:09 the models job waits a whole day, and mixes are built on stale models.
- *What:* when the worker starts, any run still `running` is marked failed (as now). If it was a scheduled run of a
  main-lane job that started within the last 12 hours and isn't a retry itself, queue it once more
  (`options={"retry_of": id}`). Never retry a retry, so a run that kills the worker every time can't loop.
- *Files:* `jobs/runner.py`, `jobs/models.py` if a field is needed.
- *Done when:* tests show one requeue, and no second one.
- *Built 28 Oct* (`Worker.recover`): the interrupted run's error says "queued again as run N".

---

## Wave 1: move to the new node (needs I1; I2 first is safer)

**I5. Run audio-brain on the 32 GB node, with room to grow.**
- *Plan to agree with George first:* api, worker and postgres move to the new node. The MCP pod stays where Tailscale
  Funnel serves it, since `helm/templates/mcp.yaml` already has a `nodeSelector`. It reaches the API by service
  name, so that works across nodes. Argo CD syncs from the `George9Waller/helm` app-of-apps: find out whether values
  are overridden there or only here.
- *Chart changes (`helm/values.yaml`, templates):*
  - a `nodeSelector` (or a `node` value) on api, worker and postgres;
  - starting limits, to be revised from I3's numbers: worker 10 Gi (request 3 Gi), api 4 Gi (request 1.5 Gi),
    postgres 6 Gi with `sharedBuffers: 2GB` (and `effective_cache_size`, `work_mem`, `maintenance_work_mem` passed as
    `-c` args like `shared_buffers`);
  - postgres storage 10 Gi → 60 Gi (the database, plus four backtest clones at 80k tracks, plus headroom), and
    backups 10 Gi → 30 Gi;
  - the `data` PVC (models, HF cache, rate-limit files) is RWO and shared by api and worker, so they must stay on
    the same node: say so in a comment;
  - thread caps for the 10th-gen i5 (6 cores if it's a desktop part; check I1's `lscpu`): `OMP_NUM_THREADS`,
    `MKL_NUM_THREADS` and torch's threads, so models and analysis don't fight the API for cores.
- *Data move* (with `local-path`, volumes can't follow a pod; write this as a runbook in `deploy/KUBERNETES.md`):
  1. Pick a quiet time (after 10:00 and before 00:15, when no nightly job runs). Scale the worker to 0.
  2. Take a fresh `pg_dump -Fc` and copy it, and the `data` volume's `models/` (Essentia graphs, scorer, ranker), to
     the Mac.
  3. Deploy the chart with the new node selector and new PVC names (or delete the old PVCs once copies are safe:
     **confirm with George before deleting anything**).
  4. Restore the dump (`deploy/KUBERNETES.md` has the `pg_restore` lines), copy `models/` back, start the API, check
     `/health` and `/jobs`, then scale the worker to 1.
  5. Rollback: the old PVCs and the dump are kept until a full night's run succeeds on the new node.
- *Done when:* every nightly job succeeds two nights running on the new node, the step peaks (I3) are under 70% of
  their limits, and the MCP `sql` tool answers (I0).

**I6b. Split the worker by lane (optional, after I5).**
- *Why:* one lane's memory spike shouldn't kill the others' runs, and limits can be set per lane.
- *What:* `run_jobs --lane main|lookups|sync` runs only that lane's jobs (with scheduling for those only); the chart
  gets one Deployment per lane, each `replicas: 1`, all on the same node (they share the `data` PVC, and the
  rate-limit `flock` files in `RATE_LIMIT_DIR` only work on one filesystem). `jobs_workerstatus` becomes one row per
  lane, and `/health` and `/jobs` show each.
- *Done when:* killing one lane's pod leaves the others' runs going.
- *Built 28 Oct:* `run_jobs --lane` (repeatable; every lane if left out). A worker recovers, schedules and starts
  only its own lanes' jobs, and claims a run by moving it from queued to running in one update. A partial unique
  index allows one queued or running run per job. `/health` adds `lanes_alive`, and `/jobs` adds `workers` (one per
  lane), while `worker` stays as all lanes together, so the app is unchanged. The chart has `worker.perLane`
  (default **false**) with a limit for each lane. Turn it on with or after I5: on the old node the three limits
  together are more than it has free. The chart hasn't been rendered with `helm template` yet.

---

## Wave 2: local copies of the rate-limited sources (I6 needs I1's decision; I7 is independent)

**I6. A local MusicBrainz mirror.**
- *Why:* MusicBrainz's one request a second (and its 503s) is most of enrichment's 2–17 hours, and it's what makes
  enrichment overlap everything else. A local copy has no limit, so the identity stage can run in parallel and finish
  in minutes.
- *Decision (you), after I3's numbers:*
  - **A. Full mirror (recommended if the disk allows):** MetaBrainz's `musicbrainz-docker` stack (server in mirror
    mode, its Postgres, Solr search and indexer, RabbitMQ, Redis), replicating hourly. It serves the same `/ws/2/` API,
    search included, so audio-brain only changes its base URL. It needs a free MetaBrainz replication token
    (non-commercial). Check the current RAM and disk requirements in its README before committing. With search
    indexes it's large: likely hundreds of GB of disk and several GB of RAM, so verify the real figures.
  - **B. Database only:** load the MusicBrainz database dump (no web server, no Solr) and query it directly: ISRC →
    recording through `isrc`, name matching with `pg_trgm` on `recording` and `artist_credit`. Much smaller, but
    `clients/musicbrainz.py`'s five methods need SQL rewrites, and search scoring has to be rebuilt.
- *Deployment (A):* its own Argo CD app and namespace, with its PVCs on whichever node has the disk; reached as
  `http://musicbrainz.musicbrainz.svc:5000/ws/2/` or similar. The initial import takes hours: run it, and the first
  indexing, as a one-off.
- *audio-brain changes:*
  - a `MUSICBRAINZ_BASE_URL` setting (default the public one) read by `MusicBrainzClient`;
  - when it's local: `min_interval = 0`, no `shared_lane`, and the identity stage and artist profiles look up with a
    small thread pool (`MUSICBRAINZ_WORKERS`, say 8);
  - keep the public API as a fallback when the mirror is down (connection refused), at the public rate;
  - lag: the mirror is up to an hour behind, which doesn't matter for identity.
- *Done when:* a night's enrichment of about 6k new tracks finishes in under 30 minutes, with the same resolve rate as
  before (compare `enrichment_enrichmentattempt` status counts).

**I7. Discogs from its monthly data dumps.**
- *Why:* Discogs styles are the finest genre signal in the catalogue (`discogs_style` tags), and the API allows 60
  requests a minute. The monthly dumps (`data.discogs.com`: artists, labels, masters and releases as gzipped XML)
  hold all of it.
- *What:*
  - First read the genres stage (`enrichment/`, `clients/discogs.py`) to learn how a track is matched to a Discogs
    release now, and keep the same matching.
  - A `discogs_import` management command: stream-parse the releases (and masters) XML with `iterparse`, and keep only
    what's used: release ID, master ID, title, year, artists, labels and catalogue numbers, genres and styles, and
    track titles (for matching). Write them to tables in a separate `discogs` schema, or a separate database, so
    they never enter backtest clones (they'd add GBs to each). Check the releases dump's current size first.
  - A monthly job (`Monthly`, or `Weekly` with a check for a new dump) downloads and loads the newest dump, then
    swaps it in.
  - The genres stage reads locally and falls back to the API for anything newer than the dump.
- *Done when:* the genres stage for 6k tracks takes minutes, with a match rate at least as good as the API's.

---

## Wave 3: bigger things the new node makes possible (parallel; after I5)

**I8. Backtests on a schedule.**
- *Why:* every model change is backtested by hand on the Mac. On the cluster, the four-cutoff comparison (about 30
  minutes cold, a few minutes per variant with `--reuse`) can run every week against whatever's enabled.
- *What:*
  - A `backtest` job, weekly (Sunday 12:00, when nothing else runs), main lane: `backtest --cutoffs 42,84,126,168
    --variant default` plus every variant listed in a `BACKTEST_VARIANTS` setting, with `--reuse`, refreshing the
    clones monthly.
  - Store each report (it's already JSON under `server/models/backtests/multi/`) in a table as well, so the API, the
    app and the MCP can show the trend: "played rate 9.6% → 10.1% since the scorer came back".
  - Check postgres's disk (I5 sized it for four clones) and drop clones older than the refresh.
- *Done when:* two weekly reports exist and can be compared through the API.

**I9. A health view of the server.**
- *Why:* this check needed curl and guesswork. A health check should be one call.
- *What:*
  - `GET /api/v1/health/detail`: per job, the last 7 runs (state, duration, failed steps, peak memory from I3), the
    enrichment backlog (tracks missing each stage), catalogue size, database size, the worker's last restart, and how
    long the models are out of date ("models last rebuilt 26 h ago").
  - A `server_health` MCP tool (`claude_mcp/`) that shows it.
  - A warning when models are more than 36 hours old, or a nightly job failed: in the app's Jobs screen, and as a
    push through the existing notification path if there is one (check `android/`). Otherwise just the screen.
- *Done when:* the MCP tool answers "how is the server doing" with the table from the top of this file.

**I10. Off-box backups (part you).**
- *Why:* backups sit on the same node as the database. The off-box copy is a manual `kubectl cp` to the Mac.
- *What:*
  - **(you)** an S3 bucket with a lifecycle rule (30 days, say) and an IAM key that can only write to it, added to
    the AWS secret.
  - A second container in the backup CronJob, or a following job, that uploads each new dump with `aws s3 cp`. With
    two nodes, keeping a copy on the other node is a cheaper alternative.
  - A restore drill in `deploy/KUBERNETES.md`: download, restore into a scratch database, and count tracks.
- *Done when:* last night's dump is in S3, and the drill has been run once.

**I11. Feed the faster pipeline more (ideas to scope once I6 lands).**
- Let the candidates job bring in more a night, or lift its cap: what limits it now is enrichment time.
- Use the local mirror's relationships, which were too costly through the rate limit: remixer, producer and label
  credits for every track, release groups (originals versus remixes), and artist-to-artist links, as scorer features
  and discovery seeds.
- ListenBrainz publishes similar-recordings and similar-artists data from many listeners: a second candidate source
  beside Tidal's similar tracks, compared with a backtest.
- Each of these is a model or list change, so BACKLOG.md's backtest rule applies.

---

## Order

1. I0 and I1 (George), with I2, I3 and I4 in parallel sessions. I2 alone should stop the integrity failures.
2. I5 once I1's facts are in, ideally after a night of I3's numbers, so the limits are measured rather than guessed.
3. I6 (decision first) and I7 in parallel; I6b if memory still causes trouble.
4. I8, I9 and I10 in parallel; I11 after I6.

## Cluster facts

(Filled in by I1. Placeholders only: this repo is public.)

Checked 28 Oct with `kubectl` from the Mac. Both nodes run k3s v1.36.5, untainted.

- Old node: Lenovo Tiny, 8 GB, 4 CPUs, 57 GiB root disk, `<node>`; control plane. Runs the MCP (Tailscale Funnel)
  and, for now, api, worker and postgres. Memory in use was 83% (6.5 GiB) at idle, 28 Oct evening.
- New node: `<new-node>`, joined 27 Oct. 32 GB, Intel Core i5-10500T (6 cores, 12 threads, 2.3 GHz), 231 GiB
  root disk, plus a separate NVMe partition at `/mnt/data` (234 GB, 222 GB free on 28 Oct). It has the label
  `size=large`, which a `nodeSelector` can use without naming the host.
- StorageClasses:
  - `local-path` (default): every audio-brain volume is on it, so all of them are pinned to the old node: postgres
    10 Gi, backups 10 Gi, data 5 Gi, mcp 100 Mi. It can't expand volumes, so moving postgres to the new node means
    a new volume and a dump and restore, not a resize.
  - `data-nvme` (added 27 Oct): provisioner `rancher.io/local-path-data` (in the `local-path-data` namespace),
    which writes only to `/mnt/data/k3s-storage` on the new node, with `WaitForFirstConsumer` and expansion allowed.
    I5's postgres volume belongs here.
- Room for I6: after I5's 60 Gi postgres volume, `/mnt/data` has about 160 GB left. A full MusicBrainz mirror
  with search indexes may not fit there; check its real size first (I6). The new node's root disk is the fallback,
  but no StorageClass provisions volumes onto it yet.
- Access: kubectl from the Mac uses k3s's kubeconfig with `server: https://<node>:6443`. The API certificate
  covers the short host name and the old node's LAN IP, not the `<node>.<tailnet>.ts.net` name. The Mac has no SSH
  key on the nodes yet.

## Status

- [ ] I0 SQL tool password: diagnosed (pod predates the key); resolves with I5's fresh API pod
- [x] I1 Cluster access and new node facts (28 Oct; SSH keys from the Mac still optional)
- [x] I2 Merges vs models lock (28 Oct; check a night that overlaps after deploy)
- [ ] I3 Memory and rate-limit measurement
- [x] I4 Requeue after a worker death (28 Oct)
- [ ] I5 Move to the new node
- [x] I6b Worker per lane (28 Oct; `worker.perLane` off until I5)
- [ ] I6 MusicBrainz mirror (decision: A or B)
- [ ] I7 Discogs dumps
- [ ] I8 Scheduled backtests
- [ ] I9 Health view and MCP tool
- [ ] I10 Off-box backups
- [ ] I11 More from the faster pipeline
