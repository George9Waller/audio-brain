# Running audio-brain on the home box

Two launchd agents keep the server running for your user, starting at login and restarting if they stop:

| Agent | Runs | Log |
|---|---|---|
| `com.audiobrain.server` | the API under gunicorn (`run-server.sh`, settings in `gunicorn.conf.py`), on port 8000 | `server/logs/server.log` |
| `com.audiobrain.worker` | `manage.py run_jobs` with schedules on (`run-worker.sh`): Last.fm every 15 min, enrichment, models and mixes nightly | `server/logs/worker.log` |

Schedules are in `server/jobs/registry.py`; `uv run python manage.py run_job` lists them and when each is next due.

## Install

Needs Postgres with pgvector running, `uv` in `~/.local/bin` (or Homebrew), and the repo checked out (the paths below
assume `~/code/audio-brain`).

1. **Dependencies and database:**
   ```sh
   cd ~/code/audio-brain/server
   uv sync
   uv run python manage.py migrate
   ```
2. **`.env`** (in `server/`, never committed). On top of the database and API keys you already have, set:
   ```sh
   SECRET_KEY=...        # uv run python -c "import secrets; print(secrets.token_urlsafe(50))"
   ALLOWED_HOSTS=localhost,127.0.0.1,.ts.net
   ```
   `DEBUG` is off unless `.env` says `DEBUG=1`; leave it off here. Without `SECRET_KEY`, one is generated into
   `server/.secret_key` on first start, which also works. See `server/.env.example` for every key.
3. **The app's token:**
   ```sh
   uv run python manage.py api_token --url http://<this machine>.<tailnet>.ts.net:8000
   ```
   This creates `API_TOKEN` in `.env` if it's missing and prints the `audiobrain://connect?...` link (and an `adb`
   command) that sets the app up. Run it before loading the agents, or restart the server afterwards.
4. **Tidal login** (for syncing lists and reading favourites), if this machine hasn't got one yet:
   `uv run python manage.py tidal_login`.
5. **Load the agents.** The plists use `REPO` as a placeholder for the repo's path:
   ```sh
   cd ~/code/audio-brain
   mkdir -p server/logs
   for name in server worker; do
     sed "s|REPO|$HOME/code/audio-brain|g" deploy/com.audiobrain.$name.plist > ~/Library/LaunchAgents/com.audiobrain.$name.plist
     launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.audiobrain.$name.plist
   done
   ```
   Stop anything still running by hand first (`runserver`, `run_jobs`): only one worker should run, or jobs start
   twice.
6. **Check:**
   ```sh
   curl -H "Authorization: Bearer $(grep ^API_TOKEN server/.env | cut -d= -f2)" http://localhost:8000/api/v1/health
   tail -f server/logs/worker.log
   ```
   `/health` says whether the worker is running.

## Tailscale

The phone and DAPs reach the server over Tailscale, so nothing is opened to the internet:

- Install Tailscale on the home box and the devices, signed in to the same tailnet, and turn on MagicDNS.
- The app's URL is `http://<machine name>.<tailnet>.ts.net:8000` (`tailscale status` shows the name). `.ts.net` is
  in `ALLOWED_HOSTS` by default.
- gunicorn listens on every interface (`0.0.0.0:8000`) so Tailscale can reach it; every API call still needs the
  bearer token. To listen on Tailscale only, set `GUNICORN_BIND=<tailscale ip>:8000` in the server plist's
  environment (`tailscale ip -4`), keeping in mind the address must exist when the agent starts.
- On macOS, allow incoming connections for Python if the firewall asks.

## Day to day

```sh
launchctl kickstart -k gui/$(id -u)/com.audiobrain.server   # restart after pulling new code (same for worker)
launchctl bootout gui/$(id -u)/com.audiobrain.worker        # stop one
uv run python manage.py migrate                             # after pulling, before restarting
```

A worker restarted mid-job marks that run failed when it comes back; start it again from the app or with
`uv run python manage.py run_job <key>`.
