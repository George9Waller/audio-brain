# audio-brain server

Django on Postgres + pgvector. The plan is in `../spec.md`; the companion app's in `../android/PLAN.md`.

## Jobs

Every scheduled job is in `jobs/registry.py`: its steps, in order, and when it runs.

```sh
uv run python manage.py run_job                 # list jobs, their schedules and when each is next due
uv run python manage.py run_job lastfm          # run one now, in the foreground
uv run python manage.py discover                # the whole pipeline (same as `run_job discover`)
uv run python manage.py run_jobs                # the worker: runs schedules and anything started from the app
uv run python manage.py run_jobs --no-schedule  # only what's started from the app or API
```

The worker runs jobs in two lanes, `sync` (Last.fm, favourites) and `main` (everything else). Only one job runs in
each lane at a time, so a long enrichment never holds up the 15-minute Last.fm sync. Each run is a `JobRun` row
holding its steps, the tail of its log and its first failure; the app reads these. Keep the worker running with
launchd, systemd or tmux. If it stops while a job is running, that run is marked failed the next time it starts.

## API for the companion app

`/api/v1/` (Django Ninja; interactive docs at `/api/v1/docs` once you have a token). Every call needs
`Authorization: Bearer $API_TOKEN`.

```sh
uv run python manage.py api_token --url http://<this machine>.<tailnet>.ts.net:8000
uv run python manage.py runserver 0.0.0.0:8000  # reachable over Tailscale
```

`api_token` creates the token (and saves it to `.env`) if there isn't one. It then prints an
`audiobrain://connect?...` link, plus an `adb` command that opens the link on a device plugged in over USB; either
sets the app up in one step. `ALLOWED_HOSTS` defaults to `localhost`, `127.0.0.1` and `.ts.net`; set it in `.env`
if you use a different address.

| Endpoint | What |
|---|---|
| `GET /health` | Checks the token; says whether the worker is running |
| `GET /jobs`, `POST /jobs/{key}/run`, `GET /runs/{id}`, `POST /runs/{id}/cancel` | Schedules, recent runs, starting a run |
| `GET /now` | Contexts ranked for right now, each with its colour (`hue`) and Tidal playlist |
| `GET /contexts` | Every active context |
| `POST /tracks/resolve`, `GET /tracks/{id}` | The catalogue track for what a player reports: sound, context, plays, verdict |
| `POST /sync` | The app's outbox. Feedback is stored once per `client_id`, so resending is safe |

Feedback from the app is stored as `Feedback` rows with `source="app"`. Each keeps what the player reported in
`observed`, and is matched to a track by Tidal ID, then by names (`api/matching.py`). A track that isn't in the
catalogue yet is created, as a scrobble would create it.

```sh
uv run python manage.py test api
```
