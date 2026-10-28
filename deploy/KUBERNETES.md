# Moving audio-brain onto the home Kubernetes cluster

The plan for moving the server off the Mac (launchd, [README.md](README.md)) and onto the cluster, then getting the
Android app onto every device. Written 20 Oct 2026 so any assistant or person can pick it up: read this, then
[NEXT.md](../NEXT.md) for the state of the app itself.

Progress is tracked with the checkboxes. Tick them as you go.

## The target

- **Cluster:** one Lenovo Tiny (i5, amd64, **8 GB RAM**), managed by Argo CD.
- **GitOps repo:** [George9Waller/helm](https://github.com/George9Waller/helm), an app-of-apps. `charts/apps/values.yaml`
  lists each app's repo and chart path; `charts/apps/templates/application.yaml` turns each one into an Argo
  `Application` (namespace `default`, auto-sync, prune, self-heal, no `valueFiles`, so the chart's own `values.yaml`
  is used).
- **The pattern to copy:** [George9Waller/daily-planner](https://github.com/George9Waller/daily-planner):
  - its chart lives in its own repo under `helm/`;
  - images go to ECR (`810574566145.dkr.ecr.eu-west-2.amazonaws.com/<app>`), pulled with `ecr-registry-secret`, which
    a CronJob in the helm repo refreshes every 6 hours;
  - CI (`.github/workflows/deploy.yaml`) assumes `arn:aws:iam::810574566145:role/GHActionsECRRole` over OIDC, builds
    and pushes `<app>:<sha>`, then commits `IMAGE_TAG: '<sha>'` into `helm/values.yaml` with a `PERSONAL_TOKEN`
    secret; Argo syncs that (audio-brain uses the built-in `GITHUB_TOKEN` instead, so it needs no token secret);
  - secrets come from AWS Secrets Manager through External Secrets: an `ExternalSecret` against the
    `aws-secrets-store` `SecretStore` (namespace `default`) makes `<app>-aws-secrets`;
  - Postgres is a plain StatefulSet + PVC + Service in the chart;
  - migrations are a Job with `helm.sh/hook: pre-install,pre-upgrade`.
- **Ingress:** Traefik with the Gateway API ([traefik-reverse-proxy](https://github.com/George9Waller/traefik-reverse-proxy),
  `HTTPRoute`s on `*.georgewaller.com`), **which doesn't work at the moment**. audio-brain doesn't need it: the spec
  keeps the API private on Tailscale, so it's a NodePort reached over the tailnet. Don't add an `HTTPRoute`.

## What the app needs (and the gotchas found)

| Thing | Notes |
|---|---|
| API | Django + Django Ninja under gunicorn ([gunicorn.conf.py](gunicorn.conf.py)), bearer token auth. Idle RSS about 260 MB. |
| Worker | `manage.py run_jobs`: Last.fm every 15 min; enrichment, models and mixes nightly. **Exactly one** may run, or jobs start twice. Heavy: torch, CLAP (`laion/larger_clap_general`, from Hugging Face), Essentia, UMAP, yt-dlp + ffmpeg. Peak memory not yet measured. |
| Database | Postgres 18 + pgvector, about 1.2 GB (Postgres.app on the Mac). The Tidal OAuth token lives in it, so it moves with the dump. |
| Files in `server/models/` | The Essentia `.pb`/`.json` graphs (23 MB, gitignored) are **baked into the image** at `/app/essentia` (`ESSENTIA_MODELS_DIR`): the build downloads them from essentia.upf.edu and checks them against `server/enrichment/essentia_models.sha256`. `scorer.joblib` + `context_ranker.joblib` are rewritten nightly by the worker and read by the API, on the data volume. |
| Other on-disk state | Hugging Face cache (CLAP weights, ~1 GB), `server/imports/` (Spotify/Tidal exports, gitignored, only needed for re-imports). |
| Settings | All from environment variables (`server/server/settings.py`, every key in `server/.env.example`). `SECRET_KEY` is required from env; the server refuses to start without it. |
| **Python 3.13** | The image runs 3.13 (the newest Python with wheels for everything: gensim has none for 3.14). The Mac's `.python-version` is still 3.12; both resolve from the same `uv.lock`. |
| **amd64 only** | `essentia-tensorflow` has Linux wheels for x86_64 only. The Lenovo is fine; never schedule the worker on an arm64 node. |
| **CUDA torch** | `uv.lock` resolves Linux torch from PyPI, which drags in 15 `nvidia-*` packages (several GB). Point torch at the CPU index on Linux before building (below). |
| **Postgres 18 image** | From 18 the official image keeps data under `/var/lib/postgresql/18/docker`. Mount the PVC at `/var/lib/postgresql` (not `/var/lib/postgresql/data` as daily-planner does on 17). Use `pgvector/pgvector:pg18`. |
| Migrate hook | With a pre-install hook, the first sync runs the migrate Job before the Postgres StatefulSet exists. Run `manage.py migrate` as an **initContainer of the API** instead (it waits for the DB, then migrates). |
| Backtests | Shell out to `pg_dump`/`createdb` and need about 1.2 GB per clone. They stay on the Mac, run against a dump of the cluster DB. |
| App networking | The app already allows cleartext (`android/app/src/main/res/xml/network_security_config.xml`) because Tailscale encrypts the link, so `http://<lenovo>.<tailnet>.ts.net:<nodePort>` works as is. |

### Memory budget (8 GB node)

| | Request | Limit |
|---|---|---|
| Kubernetes, Argo CD, Traefik, daily-planner, External Secrets | ~2–2.5 GB | |
| Postgres (`shared_buffers=256MB`) | 512 Mi | 1 Gi |
| API: **1 gunicorn worker × 8 threads** (not 2 × 4: each process loads models) | 384 Mi | 1 Gi |
| Worker: `MIXES_THREADS=2` | 1 Gi | 3.5 Gi |

Check what's free first (`kubectl top node`; needs metrics-server). Measure the worker's peak on the Mac during one
nightly run (`/usr/bin/time -l uv run python manage.py run_job <key>`, "maximum resident set size") before
setting its limit. If it goes past ~3.5 GB, trim the jobs rather than overcommit the node.

## Decisions

- Image on **ECR**, CI on GitHub Actions, the same as daily-planner.
- Chart at **`helm/`** in the audio-brain repo; one line added to `charts/apps/values.yaml` in the helm repo.
- Secrets in **AWS Secrets Manager**, key `audio-brain`, through an `ExternalSecret`.
- **A new `API_TOKEN` and `SECRET_KEY` for the cluster.** The Mac runs with the dev token `devtest123`, which is in the public docs, so the cluster gets a real one. The app is set up again with the connect link (Phase 4).
- Postgres as a StatefulSet in the chart (no operator: one node, no HA to gain). Nightly `pg_dump` CronJob to a
  backup PVC, keeping 7.
- One RWO data PVC mounted by both API and worker at `/app/server/models` (fine on one node: RWO is per node). That
  means no code changes for model paths: seed it once with the Mac's `server/models/` (minus `backtests/`). Set
  `HF_HOME=/app/server/models/hf`. Shared rate limits are rows in the database (`clients.RateLimitLane`), not files.
- Exposure: the API Service is a **NodePort** (e.g. 30800), and the Lenovo itself joins the tailnet. No Tailscale
  operator needed. Probes use `tcpSocket` on 8000 (`/api/v1/health` needs the bearer token).
- Worker Deployment: `replicas: 1`, `strategy: Recreate`, so two never overlap.
- Android: signed release APKs on GitHub Releases, installed and updated with Obtainium. No Play Store.

## Phase 0: prerequisites (mostly clicks, ~20 min)

- [x] Create a GitHub repo `George9Waller/audio-brain` and push (no remote exists yet). `.env`, `models/`,
      `imports/` and `.secret_key` are already gitignored; check `git ls-files` shows no secrets first.
- [x] **Public**, like the other app repos, so Argo needs no credentials. Checked 20 Oct: no keys, tokens or personal
      data in the tracked files or the history (the only token in it is the dev placeholder `devtest123`).
      `.claude/` and `__pycache__/` are now ignored, and a stray `.pyc` is untracked.
- [x] ECR repository `audio-brain` in eu-west-2.
- [x] Allow the repo in `GHActionsECRRole`'s trust policy. New repos use GitHub's immutable OIDC subject, so the
      pattern is `repo:George9Waller@54817483/audio-brain@1427544714:*`, not `repo:George9Waller/audio-brain:*`
      (`gh api repos/George9Waller/audio-brain/actions/oidc/customization/sub` shows it).
- [x] AWS Secrets Manager secret `audio-brain` with every key the app needs: `POSTGRES_PASSWORD` (new), plus from the
      Mac's `server/.env`: `SECRET_KEY`, `API_TOKEN`, `LASTFM_API_KEY`, `LASTFM_USER`, `TIDAL_CLIENT_ID`,
      `TIDAL_CLIENT_SECRET`, `DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
      `API_CONTACT`.
- [x] Tailscale on the Lenovo host (`tailscale up`), MagicDNS on. Note its name.
- [ ] Check free memory and the default StorageClass (`kubectl get sc`).

## Phase 1: the image (written 20 Oct)

- [x] CPU-only torch on Linux: `[[tool.uv.index]] pytorch-cpu` + `[tool.uv.sources] torch` (Linux marker) in
      `server/pyproject.toml`, relocked. Linux gets `torch 2.14.1+cpu` and no `nvidia-*` packages; the Mac's
      environment is unchanged (`uv sync --frozen --dry-run`: no changes).
- [x] [server/Dockerfile](../server/Dockerfile), built from the repo root: `python:3.13-slim` (3.13 is the newest
      Python with wheels for every dependency; gensim has none for 3.14. `UV_PYTHON` overrides `.python-version`,
      which stays 3.12 on the Mac), uv 0.9.24, `uv sync --frozen --no-dev` in a build stage, ffmpeg and the Postgres
      client, user `app` (uid 1000), default command gunicorn with [gunicorn.conf.py](gunicorn.conf.py).
      [.dockerignore](../.dockerignore) keeps out `.venv`, `.env`, `models`, `imports`, `android` and `helm`.
- [x] Smoke-tested as `linux/amd64` on the Mac (1.04 GB): Python 3.13.16, `torch 2.14.1+cpu`, Essentia's `Analyser`
      loads the real model files, `manage.py check` is clean, gunicorn boots.
  ```sh
  docker buildx build --platform linux/amd64 -f server/Dockerfile -t audio-brain:test --load .
  ```
- [x] [.github/workflows/deploy.yaml](../.github/workflows/deploy.yaml): daily-planner's workflow, with
      `docker/build-push-action` and the GHA layer cache, triggered by changes under `server/`,
      `deploy/gunicorn.conf.py` or the workflow itself (or by hand), then committing `IMAGE_TAG` into
      `helm/values.yaml`.
- [x] First green run in GitHub Actions (needs Phase 0).

## Phase 2: the chart (written 20 Oct)

[helm/](../helm/), linted and rendered (`helm lint`, `helm template`, kubeconform: 10 valid, the ExternalSecret
skipped as a CRD). Everything that changes is in [helm/values.yaml](../helm/values.yaml).

| Template | What it makes |
|---|---|
| `configmap.yaml` | `audio-brain-config`: where Postgres is, plus everything under `config:` in values |
| `externalsecret.yaml` | `audio-brain-aws-secrets`, with **every property** of the AWS secret `audio-brain` (`dataFrom.extract`), so new keys need no chart change |
| `pvc.yaml` | `audio-brain-data` (5 Gi), `audio-brain-postgres` (10 Gi), `audio-brain-backups` (10 Gi); all marked `Delete=false` for Argo and `resource-policy: keep` for Helm |
| `postgres.yaml` | StatefulSet `audio-brain-postgres` on `pgvector/pgvector:pg18`, mounted at `/var/lib/postgresql`, `shared_buffers=256MB`, `pg_isready` probe |
| `api.yaml` | Deployment `audio-brain-api` + NodePort Service on **30800**. An initContainer waits for Postgres and runs `migrate`. TCP probes. Data volume at `/app/server/models` |
| `worker.yaml` | Deployment `audio-brain-worker`, `Recreate`, **`replicas: 0` until the cut-over**. With `worker.perLane`, one per lane instead (`audio-brain-worker-main`, `-lookups`, `-sync`, each `run_jobs --lane <lane>`), all on the data PVC's node |
| `backup.yaml` | CronJob at 00:15: `pg_dump -Fc` into the backups volume, keeping 7 days |

Pods have their own selectors (`app: audio-brain-api` and so on), unlike daily-planner where the worker shares the
main Deployment's labels, so its Service would also send traffic to the worker. Pods are pinned to amd64 and use
`fsGroup: 1000`, so the volumes are writable. `config.ALLOWED_HOSTS` needs extending if you reach the API by IP rather
than the `ts.net` name.

- [x] In the **helm repo**, add to `charts/apps/values.yaml` (not done: it's a different repo):
  ```yaml
    - name: audio-brain
      repoURL: https://github.com/George9Waller/audio-brain.git
      path: helm
  ```

## Phase 3: moving the data

Mac stays the primary until the cut-over at the end. Do a dry run first with the worker at `replicas: 0`.

- [x] Restore a copy:
  ```sh
  pg_dump -Fc audio_brain > /tmp/audio_brain.dump      # on the Mac
  kubectl cp /tmp/audio_brain.dump default/audio-brain-postgres-0:/tmp/audio_brain.dump
  kubectl exec -it audio-brain-postgres-0 -- pg_restore --no-owner --role=audio_brain -U audio_brain -d audio_brain --clean --if-exists /tmp/audio_brain.dump
  ```
  If the API's migrate initContainer already created the schema, `--clean` replaces it. Check the pgvector version
  in the image is at least the Mac's (`select extversion from pg_extension where extname='vector'`), and compare a
  few row counts.
- [x] Seed the data volume (skip `backtests/`), then restart. Since the Essentia graphs moved into the image, only
      `scorer.joblib` and `context_ranker.joblib` need copying, and the nightly models job rebuilds both anyway:
  ```sh
  pod=$(kubectl get pod -l app=audio-brain-api -o name | head -1)
  for f in server/models/*.pb server/models/*.json server/models/*.joblib; do kubectl cp "$f" "${pod#pod/}:/app/server/models/"; done
  kubectl rollout restart deploy/audio-brain-api
  ```
- [x] From the phone over Tailscale:
      `curl -H "Authorization: Bearer <token>" http://<lenovo>.<tailnet>.ts.net:30800/api/v1/health`.
- [ ] **Cut-over** (another evening): stop the Mac worker (`launchctl bootout gui/$(id -u)/com.audiobrain.worker`) and
      server, take a fresh dump and restore it, set `worker.replicas: 1`, watch `kubectl logs -f deploy/audio-brain-worker`
      through a Last.fm run and one nightly run. Keep the Mac database untouched for a week as the fallback.
- [ ] Run `python manage.py tidal_login` in the worker pod only if the token didn't come across or has expired.
      Its redirect is `localhost:8765`, so it needs `kubectl port-forward`; doing it on the Mac against the
      cluster DB also works.

## Phase 4: the app on every device

The step-by-step for making the key, releasing and setting up a device is [android/RELEASING.md](../android/RELEASING.md).

Built and tested 21 Oct: a release build (R8 on, signed with a test key) installed on the emulator, connected to the
cluster, and loaded lists, a list and the map with nothing in logcat. Debug builds are now
`com.georgewaller.audiobrain.debug`, labelled "Audio Brain dev", so a dev build and the release sit side by side.

- [ ] Tailscale on the phone and the DAPs (the F-Droid build has no Google dependency), same tailnet.
- [x] Release signing: `android/app/build.gradle.kts` reads the keystore from env vars (`ANDROID_KEYSTORE_FILE`,
      `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`) or from `android/keystore.properties`
      (gitignored; keys `storeFile`, `storePassword`, `keyAlias`, `keyPassword`). Without one, a local release build
      falls back to the debug key; in CI it fails.
- [ ] **(you)** Make the keystore, outside the repo, and back it up somewhere safe (losing it means uninstalling to
      update):
  ```sh
  keytool -genkeypair -v -keystore ~/keys/audiobrain-release.jks -alias audiobrain -keyalg RSA -keysize 4096 -validity 10000
  ```
- [ ] **(you)** Add it to the repo's Actions secrets:
  ```sh
  base64 -i ~/keys/audiobrain-release.jks | gh secret set ANDROID_KEYSTORE_BASE64
  gh secret set ANDROID_KEYSTORE_PASSWORD
  gh secret set ANDROID_KEY_ALIAS --body audiobrain
  gh secret set ANDROID_KEY_PASSWORD          # the store password again, unless you gave the key its own
  ```
- [x] R8 for release: it was already on (`isMinifyEnabled`, `isShrinkResources`). No keep rules needed: serialization
      uses the compiler plugin's serializers, and Room, WorkManager and OkHttp ship their own rules.
- [x] Version from the tag: `v1.2.3` gives versionName `1.2.3` (what Obtainium compares) and versionCode `1002003`.
      CI passes the tag as `VERSION_NAME`; locally it's the latest `v*` tag, or `0.1.0`.
- [x] [.github/workflows/android-release.yaml](../.github/workflows/android-release.yaml): on a `v*` tag, decodes the
      keystore, builds `:app:assembleRelease` on JDK 21 and attaches `audio-brain-<tag>.apk` to a GitHub Release.
      Not run yet: it needs the secrets above.
- [ ] **(you)** First release: `git tag v0.1.0 && git push origin main v0.1.0`, then check the Release has the APK.
- [ ] **(you)** On each device, **uninstall any older Audio Brain first**: older debug builds used the release's package
      name with the debug key, and Android refuses to update across keys (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`).
      Anything still in its outbox is lost, so check Settings › Outbox says "all sent" first.
- [ ] **(you)** Obtainium on each device, adding `https://github.com/George9Waller/audio-brain` (the repo is public, so
      no token). The first install by `adb install` is fine too.
- [ ] **(you)** Point each device at the cluster with the connect link. The cluster's token is in its own environment, so
      print the link from the API pod:
  ```sh
  kubectl exec deploy/audio-brain-api -c api -- python manage.py api_token --url http://ubuntuserver.tail1604aa.ts.net:30800
  ```
      Copy the link on the device and tap Settings › Paste connect link, or use the `adb shell am start` line it prints. (The emulator
      has no MagicDNS: use `http://100.122.82.67:30800`.)

## Phase 5: tidying

- [ ] Alert if the Last.fm job hasn't run for an hour (`/api/v1/health` reports the worker).
- [ ] Every so often, `kubectl cp` the latest backup dump to the Mac. That's the off-box backup and the backtest
      source.
- [ ] Update [README.md](README.md) and [NEXT.md](../NEXT.md); keep the launchd files as the fallback until the
      cluster has run a couple of weeks.
- [ ] Later: yt-dlp may need a JavaScript runtime (deno) for YouTube. Check the sets and audio jobs' logs after the
      cut-over.

## Moving to another node (I5, written 28 Oct)

The layout is INFRA.md's "The layout": the api, the `main` and `sync` lanes and postgres on the 32 GB node (label
`size=large`, tainted so only pods with the toleration run there), and the `lookups` lane, mcp and backups on the
old one (`size=small`). `local-path` volumes can't follow a pod to another node, so the database and the data volume
move by copy into new claims (`audio-brain-postgres-nvme` on `data-nvme`, `audio-brain-data-root` on the new
node's root disk). The old claims stay in `legacyClaims`, untouched, as the rollback. Argo CD syncs `main` with
self-heal on, so `kubectl scale` is undone within minutes: the chart's `paused` value is what stops the API and the
workers.

Run it after 10:00 and before 00:15, when no nightly job runs, and with nothing running in `curl -s -H "$H" $B/jobs`
(INFRA.md has `$B` and `$H`). The API and the MCP are down from step 2 to step 6, about half an hour.

1. **Before the window.** With I5a deployed, so the `lookups` lane needs no files:
   - Taint the new node: `kubectl taint node <new-node> size=large:NoSchedule`. Running pods stay. The provisioners'
     helper pods are bound to their node directly (`nodeName`), which a `NoSchedule` taint doesn't stop; check it
     with a throwaway claim on each class:
     ```sh
     for class in data-nvme local-path; do kubectl apply -f - <<YAML
     apiVersion: v1
     kind: PersistentVolumeClaim
     metadata: {name: taint-test-$class}
     spec: {storageClassName: $class, accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Mi}}}
     ---
     apiVersion: v1
     kind: Pod
     metadata: {name: taint-test-$class}
     spec:
       nodeSelector: {size: large}
       tolerations: [{key: size, operator: Equal, value: large, effect: NoSchedule}]
       containers: [{name: c, image: busybox, command: [sh, -c, "touch /v/ok && sleep 5"], volumeMounts: [{name: v, mountPath: /v}]}]
       restartPolicy: Never
       volumes: [{name: v, persistentVolumeClaim: {claimName: taint-test-$class}}]
     YAML
     done
     kubectl get pod taint-test-data-nvme taint-test-local-path   # both Completed, then:
     kubectl delete pod,pvc taint-test-data-nvme taint-test-local-path
     ```
     If a claim stays Pending, remove the taint (`kubectl taint node <new-node> size-`) and don't go on.
2. **Pause, and switch the chart.** Merge the I5 PR, which has `paused: true`. Argo CD scales the API and the
   workers to 0, replaces the single worker with one per lane, and starts postgres on the new node with an empty
   database in its new claim. The old postgres stops cleanly, so its claim holds a consistent copy. Check:
   `kubectl get pods -o wide` shows `audio-brain-postgres-0` on the new node and no api or worker pods. If the old
   `audio-brain-worker` Deployment is still there (the app's `SkipPrune` option), `kubectl delete deploy audio-brain-worker`.
3. **Dump the old database.** Start postgres on the old claim in a pod of its own (it lands on the old node, where the
   claim is), and dump it to the Mac:
   ```sh
   kubectl run pg-old --image=pgvector/pgvector:pg18 --restart=Never --overrides='{"spec":{"containers":[{"name":"pg-old","image":"pgvector/pgvector:pg18","volumeMounts":[{"name":"v","mountPath":"/var/lib/postgresql"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"audio-brain-postgres"}}]}}'
   kubectl wait --for=condition=Ready pod/pg-old --timeout=120s
   kubectl exec pg-old -- pg_dump -Fc -U audio_brain audio_brain > ~/audio-brain-move.dump
   ls -lh ~/audio-brain-move.dump   # about 1–2 GB
   ```
4. **Restore it on the new node.** `--no-privileges`, because the read-only role doesn't exist there yet: the API's
   `migrate` creates it and grants its tables in step 6.
   ```sh
   kubectl exec -i audio-brain-postgres-0 -- pg_restore --no-owner --no-privileges --role=audio_brain -U audio_brain -d audio_brain < ~/audio-brain-move.dump
   for db in pg-old audio-brain-postgres-0; do kubectl exec $db -- psql -U audio_brain -d audio_brain -Atc "select count(*) from catalogue_track"; done   # the same
   kubectl delete pod pg-old
   ```
5. **Copy the data volume.** One pod on each claim. The new claim is bound to whichever node first mounts it, so its
   pod must have the `size=large` selector, or the volume lands on the wrong node:
   ```sh
   kubectl run data-old --image=busybox --restart=Never --overrides='{"spec":{"containers":[{"name":"data-old","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"v","mountPath":"/data"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"audio-brain-data"}}]}}'
   kubectl run data-new --image=busybox --restart=Never --overrides='{"spec":{"nodeSelector":{"size":"large"},"tolerations":[{"key":"size","operator":"Equal","value":"large","effect":"NoSchedule"}],"containers":[{"name":"data-new","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"v","mountPath":"/data"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"audio-brain-data-root"}}]}}'
   kubectl wait --for=condition=Ready pod/data-old pod/data-new --timeout=120s
   kubectl exec data-old -- tar cf - -C /data . > ~/audio-brain-data.tar        # a copy on the Mac too
   kubectl exec -i data-new -- tar xf - -C /data < ~/audio-brain-data.tar
   kubectl exec data-new -- du -sh /data; kubectl exec data-old -- du -sh /data  # the same
   kubectl delete pod data-old data-new
   ```
6. **Unpause.** Commit `paused: false` to `main`. The API's `migrate` runs (I5a's table among anything new), then the
   API and the three lanes start. Check:
   - `kubectl get pods -o wide`: api, `worker-main`, `worker-sync` and postgres on the new node; `worker-lookups`,
     mcp on the old one; nothing else of anyone's on the new node.
   - `curl -s -H "$H" $B/health` and `$B/jobs`: three lanes alive.
   - The MCP `sql` tool answers `SELECT count(*) FROM catalogue_track` (I0).
   - `kubectl top pods`, and after the night, each step's peak in `/runs/{id}` (I3).
7. **Rollback**, if it goes wrong before a night has run: `git revert` the I5 merge (and the unpause), so the chart
   points at the old claims again, with the old layout. Anything written on the new node since step 6 is lost unless
   dumped back the same way.
8. **Tidy, after a full night's run on the new node:** with George's go-ahead, remove `legacyClaims` from the values,
   then `kubectl delete pvc audio-brain-data audio-brain-postgres` (local-path deletes their data with them), and
   delete the two files on the Mac once the next backup has run.

## The MusicBrainz mirror (I6, written 28 Oct)

The chart's `musicbrainz` section runs a mirror without search on the new node: `musicbrainz-db` (100 Gi on the
NVMe), `musicbrainz-valkey` and the `musicbrainz` server, whose web service is `http://musicbrainz:5000/ws/2/`. The
server starts at `serverReplicas: 0`, because its database doesn't exist until the import has run. audio-brain keeps
asking musicbrainz.org until `config.MUSICBRAINZ_BASE_URL` is set, and even then sends it searches, which the mirror
can't answer.

1. **(you) The replication token.** Sign in at metabrainz.org, take the access token from your profile (free for
   non-commercial use), and add it to the AWS secret `audio-brain` as `METABRAINZ_ACCESS_TOKEN`. The
   ExternalSecret picks it up within the hour (`kubectl annotate externalsecret audio-brain-aws-secrets
   force-sync=$(date +%s) --overwrite` for now). The import doesn't need it; replication does.
2. **Import** (hours: it downloads the latest full export, about 7 GB, and loads and indexes it). Any time but best
   started in the day, so the indexes are built before the nightly jobs:
   ```sh
   kubectl create job --from=cronjob/musicbrainz-import musicbrainz-import-1
   kubectl logs -f job/musicbrainz-import-1
   ```
   It ends with the database loaded and the job `Complete`. If it fails part way, read the log, then drop the
   half-made database before retrying: `kubectl exec musicbrainz-db-0 -- dropdb -U musicbrainz musicbrainz_db`,
   `kubectl delete job musicbrainz-import-1`, and create it again.
3. **Start the server:** `musicbrainz.serverReplicas: 1` in `helm/values.yaml`, committed. It takes a minute or two
   to start (it starts a renderer before the web server). Then check that lookups work without search, from the API
   pod:
   ```sh
   ISRC=$(kubectl exec audio-brain-postgres-0 -- psql -U audio_brain -d audio_brain -Atc "select isrc from catalogue_track where isrc <> '' and mbid <> '' limit 1")
   kubectl exec deploy/audio-brain-api -- python -c "import urllib.request as u
   for path in ['isrc/$ISRC?', 'artist/5b11f4ce-a62d-471e-81fc-a69a8278c7da?inc=genres&', 'release?artist=5b11f4ce-a62d-471e-81fc-a69a8278c7da&limit=1&']:
       print(path, u.urlopen('http://musicbrainz:5000/ws/2/' + path + 'fmt=json', timeout=10).status)"
   ```
   All three should answer 200. A search (`recording?query=...`) is expected to fail.
4. **Replication:** the server's cron runs `replication.sh` daily at 11:00 UTC. To catch up at once after the import
   (the export is up to a few days old): `kubectl exec deploy/musicbrainz -- bash -c 'replication.sh'`, and
   `kubectl exec deploy/musicbrainz -- tail /tmp/replication.log` after a cron run. Without the token it fails and
   the mirror just stays at the export's date.
5. **Switch audio-brain over:** `config.MUSICBRAINZ_BASE_URL: http://musicbrainz:5000/ws/2/`, committed. Lookups (by
   ISRC, by ID, an artist's releases) then go to the mirror with no rate limit; searches stay on musicbrainz.org;
   and if the mirror fails or is unreachable, the client uses musicbrainz.org for five minutes before trying it again
   (`clients/musicbrainz.py`).
6. **The download volume** (`musicbrainz-dbdump`, on the root disk, which has room) can stay: a re-import reuses it.
   Argo CD would recreate the claim if it were deleted, so to free the space, empty it from a pod that mounts it.

## Status, 21 Oct

The server runs on the cluster. The app side of Phase 4 is built: release signing, versions from tags and the release
workflow, with the R8 build tested against the cluster on the emulator. Next **(you)**: the keystore and its secrets,
tag `v0.1.0`, then uninstall the old builds and install from the Release on each device.

## Status, 20 Oct (evening)

The API and Postgres run on the cluster with a copy of the Mac's data, and `/api/v1/health` answers over Tailscale. The
worker is at 0 and the Mac is still the primary. Next: point the app at the cluster (Phase 4, connect link), then the
cut-over.

## Tonight (about two hours)

**Goal:** the API and Postgres running on the cluster with a copy of your data, deployed by Argo, and the app on your
phone loading lists from it over Tailscale. The worker stays at 0 and the Mac stays the primary.

The image, the workflow and the chart are already written and tested locally, so tonight is mostly wiring:

1. **Phase 0** (~30 min): GitHub repo and push, the ECR repo, the IAM trust policy, the AWS secret,
   and Tailscale on the Lenovo. Leave out Android and Obtainium.
2. **The first CI run** (~15 min, mostly the build): check the image lands in ECR and the bot commits `IMAGE_TAG`.
3. **Argo** (~15 min): add the entry to the helm repo's `charts/apps/values.yaml` and watch it sync. Postgres and the
   API should come up; the API's initContainer runs the migrations on the empty database.
4. **Phase 3** dry-run restore and data volume seeding (~30 min), then the `curl` from the phone, and the app via the
   connect link.

Anything you do in the app tonight (verdicts, edits) lands in the cluster copy, not the Mac. Switch the app back to
the Mac's URL afterwards, or treat tonight as browse-only. The cut-over is a separate evening.

If time is short, stop after step 2 with an image in ECR. That's a clean point to pick up from.
