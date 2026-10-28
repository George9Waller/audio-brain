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

### The layout (agreed with George, 28 Oct)

Each node has one job. The **new node** does heavy compute and holds the big data, and is kept clear of everything
else, so models, the ranker, backtests and experiments get as much of its RAM and CPU as possible. The **old node**
runs what's always on, light and indifferent to slow cores, and is where future small projects go by default.

| New node (32 GB, 12 threads), tainted | Memory limit | Disk |
|---|---|---|
| worker `main` lane (models, ranker, mixes, candidates, analysis) | 12 Gi, CPU request 4 | |
| api (loads CLAP and the scorer's catalogue for Ask and radio) | 4 Gi | |
| worker `sync` lane (analyses the tracks it brings in) | 2 Gi | |
| postgres: `audio_brain`, plus the `discogs` database (I7) | 4 Gi, `shared_buffers=1GB` | 80 Gi, `data-nvme` |
| MusicBrainz mirror without search (I6): database, server, Redis | about 4 Gi | 100 Gi, `data-nvme` |
| `data` volume (Essentia graphs, scorer, ranker, HF cache) | | 10 Gi, `local-path` on the new node's root disk |
| **Total** | **about 26 Gi of 31** | **180 of 222 GB NVMe** |

| Old node (8 GB, 4 cores, 114 GiB root) | Memory | Disk |
|---|---|---|
| The other apps, Argo CD, k3s | about 2.3 GB | |
| mcp (stays where Tailscale Funnel serves it) | 256 Mi | |
| worker `lookups` lane (enrichment), once I5a is in | 1 Gi | |
| backups: the dumps live on a different machine from the database | | 30 Gi |
| **Left for future small projects** | **about 4 GB** | **about 60 GB** |

Why it splits this way:
- The api and the `main` and `sync` lanes share the `data` volume (RWO, so one node): the API reads the models the
  `main` lane trains, and `sync` analyses with the Essentia graphs. The API runs the scorer and CLAP itself, so it
  belongs with the heavy work anyway.
- Postgres stays beside them. Models and the backtest clones read and write it hard, and on the old node it would put
  that node back near 70% memory to save the new one about 4 Gi it doesn't need.
- Enrichment's lookups are API calls and need no local files once the rate-limit lanes are in the database (I5a). It
  mostly waits, so slow cores don't matter.
- Postgres is kept modest on purpose: the OS file cache does most of the caching, and the kernel takes it back when
  the worker needs memory. Raise `shared_buffers` only if I3 shows models waiting on reads.
- Limits are ceilings. Most of the time the mirror and the `discogs` database sit idle; actual use is well under 26 Gi.

How the new node is kept for the heavy work:
- **Taint** it `size=large:NoSchedule`; audio-brain's new-node pods get the toleration and a `nodeSelector` on
  `size=large`. Nothing else lands there unless it opts in. Before tainting, give the `local-path-data`
  provisioner's helper pod the toleration (or it can't create volumes there); the svclb pod already running stays.
- **CPU requests set the shares.** There are no CPU limits, so under contention CPU is shared by request: the `main`
  lane's 4 makes it win while it runs, at no cost while it's idle. API 1, postgres 1, `sync` 0.25, `lookups` 0.1.
- **Thread caps** (`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `torch.set_num_threads`): `main` 8, api 2–4, the other
  lanes 1–2.
- **Nothing heavy in the nightly window (00:15–07:00)** except the nightly jobs: mirror replication at 12:00, the
  Discogs import on the first Saturday of the month, backtests on Sunday at 12:00 (I8).
- **Revise from I3:** if `main` never peaks above about 6 GB, cut its limit and the node has more spare.

**I5a. Rate-limit lanes in the database.**
- *Why:* `clients.base.SharedLane` paces Tidal, MusicBrainz and Discogs across processes with `flock`ed files under
  `RATE_LIMIT_DIR`, which only works on one filesystem. Tidal's lane is shared by enrichment, candidates, mixes, the
  syncs and the API's `play`, so this is what ties the `lookups` lane to the `data` volume and the new node.
- *What:* a `ratelimit_lane` table (name, next allowed time). `wait()` locks the row (`SELECT ... FOR UPDATE`) in one
  short transaction, reads the next slot, books the one after it and commits; it sleeps after the commit, so no lock
  is held while sleeping. `hold_off()` is the same, pushing the time back. At about one request a second per
  service the load is nothing. Remove `RATE_LIMIT_DIR`.
- *Built 28 Oct* (`clients.RateLimitLane`, `clients/base.py`): each booking is one `UPDATE ... RETURNING` on a
  connection of the thread's own that autocommits, so the caller's transaction never holds a lane. Times come from
  the database's clock, so the nodes' clocks needn't agree. A hold-off is a separate `held_until` column: a process
  that booked a slot before the hold-off checks it after sleeping and books again, so busy answers still push back
  every waiter, as the `flock` did. Deploying it needs nothing but the migration; the `lookups` lane can then move
  to the old node with I5.
- *Files:* `clients/base.py`, a migration, `server/settings.py`, `helm/values.yaml`.
- *Tests:* two processes on one lane take turns and keep the interval; `hold_off` delays the other process.
- *Done when:* tests pass and the `lookups` lane runs on the old node with Tidal still paced (no 429 bursts).
- Not a blocker for I5: until it's in, the `lookups` lane starts on the new node and moves afterwards.

**I5. Run audio-brain on the 32 GB node, as laid out above.**
- Argo CD syncs from the `George9Waller/helm` app-of-apps: find out whether values are overridden there or only here.
- *Chart changes (`helm/values.yaml`, templates):*
  - the `size=large` `nodeSelector` and toleration on api, the `main` and `sync` lanes and postgres; the `lookups`
    lane, mcp and the backup CronJob pinned to the old node (a `nodeSelector` on its hostname label, as mcp has);
  - `worker.perLane: true` (I6b), with the limits, CPU requests and thread caps in the tables above;
  - postgres: the limits above, with `effective_cache_size=8GB`, `work_mem` and `maintenance_work_mem` passed as `-c`
    args like `shared_buffers`; storage 80 Gi on `data-nvme` (the database, four backtest clones at about 3 GB each
    at 80k tracks, the `discogs` database, headroom);
  - backups 10 Gi → 30 Gi, on the old node's `local-path`;
  - the `data` PVC at 10 Gi on `local-path`, which provisions on whichever node first mounts the claim, under that
    node's root disk, with a comment that api, `main` and `sync` share it.
- *Data move* (with `local-path`, volumes can't follow a pod; write this as a runbook in `deploy/KUBERNETES.md`):
  1. Pick a quiet time (after 10:00 and before 00:15, when no nightly job runs). Scale the worker to 0.
  2. Take a fresh `pg_dump -Fc` and copy it, and the `data` volume's `models/` (Essentia graphs, scorer, ranker), to
     the Mac.
  3. Deploy the chart with the new node selector and new PVC names (or delete the old PVCs once copies are safe:
     **confirm with George before deleting anything**).
  4. Restore the dump (`deploy/KUBERNETES.md` has the `pg_restore` lines), copy `models/` back, start the API, check
     `/health` and `/jobs`, then scale the worker to 1.
  5. Rollback: the old PVCs and the dump are kept until a full night's run succeeds on the new node.
- *Built 28 Oct* (chart and runbook; the move itself is George's to start): `nodes.large`/`nodes.small` in the
  values, and a `node` on api, each lane, postgres and the backup CronJob; `paused` stops the API and the workers,
  since Argo CD's self-heal undoes `kubectl scale`; new claims `postgres-nvme` (80 Gi, `data-nvme`) and `data-root`
  (10 Gi, `local-path`), with the old `data` and `postgres` claims kept in `legacyClaims` so pruning can't delete
  them; postgres settings as `-c` args and a 512 Mi `/dev/shm`; thread caps per pod. Backups keep their 10 Gi claim
  on the old node: local-path doesn't enforce the size. The steps are `deploy/KUBERNETES.md`, "Moving to another
  node". The PR merges with `paused: true`: merging is step 2.
- *Moved 28 Oct, 22:20–22:45,* by the runbook, in about 25 minutes of downtime. What happened:
  - The taint test passed on both StorageClasses. The sync pruned the old single worker, so no manual delete.
  - The dump was 666 MB (`-Fc` compresses; the database is about 1.2 GB on disk), restored with
    `--no-privileges`, with matching track counts. The data volume was copied by tar.
  - After unpausing: api, `worker-main`, `worker-sync` and postgres on the new node; `worker-lookups` and mcp on the
    old one; `/health` shows all three lanes alive.
  - The app over Tailscale still reaches the API through the old node: k3s opens a NodePort on every node and
    forwards to the pod on the other (`externalTrafficPolicy: Cluster`), in about 35 ms. The MCP reaches the API by
    Service name across the nodes. The old node is still the only way in from the tailnet, though: I12.
  - Kept for rollback until a good night: the old `data` and `postgres` claims (`legacyClaims`), and
    `~/audio-brain-move.dump` and `~/audio-brain-data.tar` on the Mac.
- *Still to do:* check the MCP `sql` tool (I0); after the first night, read the runs and step peaks (I3), then, with
  George's go-ahead, drop `legacyClaims`, delete the two old claims and the Mac copies (runbook step 8).
- *Done when:* every nightly job succeeds two nights running on the new node, the step peaks (I3) are under 70% of
  their limits, nothing but audio-brain runs on the new node, and the MCP `sql` tool answers (I0).

**I6b. Split the worker by lane (optional, after I5).**
- *Why:* one lane's memory spike shouldn't kill the others' runs, and limits can be set per lane.
- *What:* `run_jobs --lane main|lookups|sync` runs only that lane's jobs (with scheduling for those only); the chart
  gets one Deployment per lane, each `replicas: 1`, all on the same node (they share the `data` PVC, and the
  rate-limit `flock` files in `RATE_LIMIT_DIR` only worked on one filesystem, until I5a). `jobs_workerstatus` becomes one row per
  lane, and `/health` and `/jobs` show each.
- *Done when:* killing one lane's pod leaves the others' runs going.
- *Built 28 Oct:* `run_jobs --lane` (repeatable; every lane if left out). A worker recovers, schedules and starts
  only its own lanes' jobs, and claims a run by moving it from queued to running in one update. A partial unique
  index allows one queued or running run per job. `/health` adds `lanes_alive`, and `/jobs` adds `workers` (one per
  lane), while `worker` stays as all lanes together, so the app is unchanged. The chart has `worker.perLane`
  (default **false**) with a limit for each lane. Turn it on with or after I5: on the old node the three limits
  together are more than it has free. The chart hasn't been rendered with `helm template` yet.

---

## Wave 2: local copies of the rate-limited sources (after I5; I6 and I7 are independent)

**I6. A local MusicBrainz mirror.**
- *Why:* MusicBrainz's one request a second (and its 503s) is most of enrichment's 2–17 hours, and it's what makes
  enrichment overlap everything else. A local copy has no limit, so the identity stage can run in parallel and finish
  in minutes.
- *Decided 28 Oct: a mirror without search.* `musicbrainz-docker`'s README asks for 16 threads, 16 GB RAM and 350 GB
  disk with indexed search, or 2 threads, 4 GB and 100 GB without. Search would take half the new node's RAM for
  good, and audio-brain barely needs it:
  - Plain lookups, which the mirror serves from its database: `recordings_by_isrc` (`isrc/{isrc}`, the identity
    stage's first try), `recording/{mbid}`, `artist/{mbid}` and the `release?artist=` browse.
  - Search (`query=`), which needs Solr: `search_recordings` (the fallback when the ISRC finds nothing) and
    `recordings(batch)`, which fetches by ID through a `rid:` query and can become plain lookups.
  - So: the mirror without Solr, the indexer or RabbitMQ (database, MusicBrainz server, Redis), and searches go to
    the public API at its one request a second. They're the tracks the ISRC misses, a small share of each night's.
  - The README doesn't say outright that lookups work without search: check it on the first run.
  - If the share of searches turns out large, add name matching with `pg_trgm` on the mirror's `recording` and
    `artist_credit` tables, rather than adding Solr.
- *Deployment:* its own Argo CD app and namespace on the new node, translated from the compose file (three services),
  with its 100 Gi volume on `data-nvme`; reached as `http://musicbrainz.musicbrainz.svc:5000/ws/2/` or similar. It
  needs a free MetaBrainz replication token (non-commercial), kept in the AWS secret. Replication is daily: run it at
  12:00, outside the nightly window. The initial import takes hours: run it as a one-off.
- *audio-brain changes:*
  - a `MUSICBRAINZ_BASE_URL` setting (default the public one) read by `MusicBrainzClient`, and a second client (or
    method routing) that sends searches to the public API;
  - against the mirror: `min_interval = 0`, no `shared_lane`, and the identity stage and artist profiles look up with
    a small thread pool (`MUSICBRAINZ_WORKERS`, say 8); `recordings(batch)` becomes per-ID lookups;
  - keep the public API as a fallback when the mirror is down (connection refused), at the public rate;
  - lag: the mirror is up to a day behind, which doesn't matter for identity.
- *Why it's worth 4 GB:* enrichment goes from 2–17 hours to minutes and finishes before models starts at 03:00, so
  the two no longer overlap. That overlap is what OOM-killed the worker, so the mirror also lowers the worker's peak.
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
    track titles (for matching).
  - *Where (decided 28 Oct):* a separate `discogs` database in audio-brain's postgres on the new node: no second
    server, so it shares postgres's memory, and it stays out of the backtest clones, which copy only `audio_brain`.
    The `lookups` lane on the old node reads it over the LAN.
  - *Size:* the 1 Oct 2026 dumps are releases 10.5 GB gzipped, masters 600 MB, artists 476 MB. Stream the gzipped
    download straight into the parser, so nothing is staged on disk. The kept columns should come to about 15–25 GB
    of tables and indexes (an estimate: measure it on the first import; postgres's 80 Gi allows for it).
  - A monthly job (`Monthly`, or `Weekly` with a check for a new dump), on the first Saturday of the month, outside
    the nightly window: downloads and loads the newest dump into new tables, then swaps them in.
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
  - **Score Tidal's own mixes too.** The `tidal_recommendations` job already snapshots them (My Mix, My Daily
    Discovery, My New Arrivals). Score them with the same "played rate" in each report, so every change reads as
    "ours versus Tidal's", not only "ours versus ours". Beating Tidal is the goal, so that's the number to watch.
  - **Branch experiments.** Today a backtest can only run deployed code. Add a way to run a branch's image as a
    one-off Kubernetes Job on the new node (a template in `helm/` or `deploy/`, started with `kubectl`; CI has to
    push branch images): a low `PriorityClass` so the nightly jobs win, a small memory request with a limit of about
    10 Gi, 4 threads, the backtest clones, and its report written to the same table. Then an idea can be tried from a
    branch and compared with the default through the API or the MCP, without touching what's live.
- *Compute:* the scorer and context ranker are scikit-learn boosted trees, and the taste and sequence models are
  small torch models: on one listener's history they train in seconds to minutes on the i5. Outside 00:15–07:00 the
  node is mostly idle, enough for well over a hundred `--reuse` variants a day, two or three side by side. What it
  can't do quickly: re-embedding the whole catalogue with a new audio model (hours to days on CPU, a weekend
  one-off), or fine-tuning audio models and large sequence transformers (a GPU: rent one by the hour if it comes
  to that).
- *Done when:* two weekly reports exist, each with Tidal's mixes scored beside ours, they can be compared through the
  API, and one branch experiment has run as a Job.

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
- *Why:* backups sit on the same node as the database. The off-box copy is a manual `kubectl cp` to the Mac. After
  I5 the dumps live on the old node and the database on the new one, which covers a disk or node failure but not
  the house: S3 is still the off-site copy.
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

**I12. Tailscale Kubernetes operator, so the tailnet doesn't depend on the old node.**
- *Why:* since I5 the API runs on the new node, but the tailnet reaches it only through the old node's Tailscale
  (its NodePort, forwarded across). If the old node is down, the app and the MCP lose the API though it's still
  running. The MCP's Funnel is also tied to the old node's host Tailscale, which is why the MCP pod stays there.
- *What:*
  - **(you)** an OAuth client in the Tailscale admin console (scopes `devices:core` and `auth_keys`, tag
    `tag:k8s-operator`, owning `tag:k8s`; check the operator's docs for the current scopes), with the tags added
    to the tailnet policy file; its ID and secret go in AWS
    Secrets Manager, synced as a Secret by external-secrets like the others.
  - The operator's Helm chart (`tailscale/tailscale-operator`) as its own Argo CD app in the `George9Waller/helm`
    app-of-apps, in a `tailscale` namespace.
  - The API Service annotated `tailscale.com/expose: "true"` and `tailscale.com/hostname: audio-brain-api` (values in
    the chart, off by default). The operator runs a small proxy pod (about 50 MB), and the API answers as
    `audio-brain-api.<tailnet>.ts.net` from whichever node it's on. `ALLOWED_HOSTS` already allows `.ts.net`.
  - The MCP behind a Tailscale Ingress with `tailscale.com/funnel: "true"`, which replaces the host Funnel. Its public
    URL changes, so `MCP_PUBLIC_URL` in the AWS secret and the claude.ai connector change with it, and every OAuth
    token has to be issued again. The MCP pod is then free to run on either node: its only tie is its 100 Mi claim
    on the old node, so leave it there unless that node goes.
  - The app: the new API name in its settings (the emulator's IP entry in `ALLOWED_HOSTS` stays until it's moved too).
  - Keep the NodePorts until the app and the connector have moved, then drop the host Funnel and the NodePorts.
- *Alternative, if the operator is too much:* join the new node to the tailnet as a second way in. That needs no
  cluster change, but the app has to try both names.
- *Done when:* with the old node's Tailscale stopped (`sudo tailscale down` there, briefly), the app and the claude.ai
  connector still work.

---

## Order

1. I0 and I1 (George), with I2, I3 and I4 in parallel sessions. I2 alone should stop the integrity failures.
2. I5a, then I5 once I1's facts are in, ideally after a night of I3's numbers, so the limits are measured rather
   than guessed. I5 needn't wait for I5a: the `lookups` lane moves to the old node when it lands. I5 turns on I6b.
3. I6 (a mirror without search) and I7 in parallel.
4. I8, I9 and I10 in parallel; I11 after I6. I12 whenever: it's independent of the rest.

## Cluster facts

(Filled in by I1. Placeholders only: this repo is public.)

Checked 28 Oct with `kubectl` from the Mac. Both nodes run k3s v1.36.5. Updated after the move (I5), 28 Oct evening.

- Old node: Lenovo Tiny, 8 GB, 4 CPUs, `<node>`; control plane. Its root volume was 57 GiB of a 128 GB drive; George
  extended it to the whole volume group (116 GiB, none left free) on 28 Oct, and `local-path` volumes, which live on
  the root filesystem, get the room. Label `size=small`, untainted. Runs the MCP (Tailscale Funnel), the `lookups`
  lane, the backup CronJob and every other app. Before the move memory in use was 83% (6.5 GiB) at idle, of which
  audio-brain's api, worker and postgres were about 3.7 GiB. It's the only node on the tailnet (I12).
- New node: `<new-node>`, joined 27 Oct. 32 GB, Intel Core i5-10500T (6 cores, 12 threads, 2.3 GHz), 231 GiB
  root disk, plus a separate NVMe partition at `/mnt/data` (234 GB, 222 GB free on 28 Oct). Label `size=large`, and
  tainted `size=large:NoSchedule` since 28 Oct, so only pods with the toleration run there: audio-brain's api,
  `main` and `sync` lanes and postgres. Not on the tailnet.
- StorageClasses:
  - `local-path` (default): provisions on any node, under its root disk. It can't expand volumes (nor does it
    enforce their size). On the old node: backups 10 Gi, mcp 100 Mi, and the pre-move data 5 Gi and postgres 10 Gi
    (unused, kept for rollback). On the new node: `data-root` 10 Gi (models, HF cache).
  - `data-nvme` (added 27 Oct): provisioner `rancher.io/local-path-data` (in the `local-path-data` namespace),
    which writes only to `/mnt/data/k3s-storage` on the new node, with `WaitForFirstConsumer` and expansion allowed.
    Holds `postgres-nvme` (80 Gi).
- Room for I6: postgres's 80 Gi and the mirror's 100 Gi leave about 40 GB of `/mnt/data`. The new node's root disk
  (256 GB, about 200 GB unused) takes the `data` volume and anything else that doesn't need the NVMe: the default
  `local-path` class provisions on every node, under `/var/lib/rancher/k3s/storage`.
- Taints and provisioning: both provisioners bind their helper pods to the node directly (`nodeName`), so the
  scheduler, and a `NoSchedule` taint, don't apply to them. The runbook checks it with a throwaway claim anyway.
- Access: kubectl from the Mac uses k3s's kubeconfig with `server: https://<node>:6443`. The API certificate
  covers the short host name and the old node's LAN IP, not the `<node>.<tailnet>.ts.net` name. The Mac has no SSH
  key on the nodes yet.

## Status

- [ ] I0 SQL tool password: diagnosed (pod predates the key); resolves with I5's fresh API pod
- [x] I1 Cluster access and new node facts (28 Oct; SSH keys from the Mac still optional)
- [x] I2 Merges vs models lock (28 Oct; check a night that overlaps after deploy)
- [ ] I3 Memory and rate-limit measurement
- [x] I4 Requeue after a worker death (28 Oct)
- [x] I5a Rate-limit lanes in the database (28 Oct)
- [x] I5 Move to the new node (moved 28 Oct; old claims kept until a good night, then runbook step 8)
- [x] I6b Worker per lane (28 Oct; on since I5)
- [ ] I6 MusicBrainz mirror (decided 28 Oct: without search; searches go to the public API)
- [ ] I7 Discogs dumps
- [ ] I8 Scheduled backtests
- [ ] I9 Health view and MCP tool
- [ ] I10 Off-box backups
- [ ] I11 More from the faster pipeline
- [ ] I12 Tailscale operator, so the tailnet doesn't depend on the old node
