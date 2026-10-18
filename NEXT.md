# audio-brain: where it stands and what's next

Handoff notes, 18 Oct 2026. Start a new session by reading this, then `spec.md`, `android/PLAN.md` and
`android/CATALOGUE.md`.

## State

**Nothing is committed to git yet.** Commit before anything else.

### Server (`server/`, Django + Postgres/pgvector)

Built in this round:
- `jobs/`: a job registry with schedules, a worker with two lanes, and job runs the app can see and trigger.
- `api/`: Django Ninja at `/api/v1`, bearer-token auth.
- Lists (`recommend/lists.py`), stored whole in the `Mix` table and rebuilt by the Mixes job. 32 track lists of 50 tracks each, plus 5 discovery lists:
  - 12 context mixes and 4 past eras;
  - Fresh, New to you, On repeat and Rediscovery;
  - "Your 2016" to "Your 2025";
  - Explore and New interest lists.
- How lists build:
  - only tracks Tidal can play, with missing Tidal IDs looked up during the build;
  - no repeats across mixes;
  - edits (`ListEdit`) survive rebuilds;
  - per-list Tidal sync (`ListSync`);
  - 20 suggestions per list, built ahead of time.
- Asking (`recommend/actions.py`): drafts that are kept, played or discarded; quick actions; radio that extends itself.
- The LLM layer (`recommend/llm.py`) uses OpenAI (`OPENAI_API_KEY` is set) or Claude.
  - Prompt parsing passes 24/24 in `manage.py eval_prompts`.
  - Without a key, `recommend/understand.py` matches meaning (CLAP, activities, artists, genres, origins).
- Explore and new interests (`recommend/explore.py`): an unknown artist is brought in from Tidal and analysed on demand.
- Artist profiles (`enrichment/artists.py`): MusicBrainz country, area, type, active years, genres and language, with Last.fm tag fallback. They feed clustering, prompt filters (country, language, decade) and naming.
- Contexts (`recommend/contexts.py`):
  - the number per family is chosen by silhouette, taking the finest split nearly as clean as the best;
  - era and session pulls are weakened, and an artist-profile signal is added;
  - all 84 are named by the AI in batches of 15.
- The map (`recommend/atlas.py` and `/api/v1/map`):
  - a nightly UMAP of the fused vectors, aligned night to night;
  - tracks with no listening history have their listening half filled from their sound neighbours, so there's no separate island;
  - family, context and artist labels by zoom.
- The library (`/api/v1/library/*`): tracks, artists, albums and sets, with search, sorting and paging.

### Android (`android/`, Kotlin + Compose, minSdk 30)

- Modules: `:core:design` (design system), `:core:data` (API, Room outbox, sync), `:core:player` (media sessions, feedback notification, device check) and `:app`.
- Five pages: Lists ← Home → Discover → Catalogue (Map or Library) → More. Pages change by swiping or tapping the page names at the top.
- Home: the sigil, focus mode, the ask box, quick actions and top lists.
- Now playing: the chip and its drawer.
- Track rows: swipe for verdicts; in a list, swipe left removes the track.
- Ask flow: Building, then a Draft to review.
- Radio and Explore.
- Map: drawn on a native canvas, with double-tap zoom and lists shown as their own tracks.
- Offline: verdicts and release decisions go through the outbox. Lists, the map and Now are cached.

### Running it now (dev, on this Mac)

- **Server:** `cd server && API_TOKEN=devtest123 ALLOWED_HOSTS=localhost,127.0.0.1,10.0.2.2 uv run python manage.py runserver 0.0.0.0:8000`
- **Worker:** `API_TOKEN=devtest123 uv run python manage.py run_jobs --no-schedule`. Drop `--no-schedule` on the real server.
- **Emulator:** AVD `Pixel_10_Pro`. Set the app up with the link `audiobrain://connect?url=http://10.0.2.2:8000&token=devtest123`.
- **Build:** `cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug`
- **Tests:** `cd server && uv run python manage.py test api` (7 tests) and `uv run python manage.py eval_prompts`.

## Known issues

- **MusicBrainz is answering 503 to this machine,** so the artist-profile backfill stopped after 65 artists. It resumes nightly in the Enrichment job and stops after 5 failures in a row. Setting `API_CONTACT` in `.env` should help.
- **The context ranker's top-3 accuracy is down to 24.6%,** from 39.8%, since contexts became sound-led. For now makes up for some of it with hour and day fit, but the ranker needs work (see the model section).
- **Fresh has only 15 tracks:** just 30 of 93 releases have analysed tracks so far. The enrichment backfill is still running.
- **List edits, sync, Explore, Ask and suggestions are online-only.** Only verdicts and release decisions queue offline.
- **The full Mixes rebuild takes about 25 minutes:** suggestions and Tidal lookups run one at a time.
- **Settings aren't production-ready:** `SECRET_KEY` is hard-coded, `DEBUG=True`, and it runs on `runserver`.
- **On the app side, not yet tested:** Explore entry points on screen, the JM21 and M23 themselves (phase 0 device check), map performance on the DAPs, pinch-zoom feel.
- **Synced playlists are created UNLISTED in Tidal.** Check they show up in your Tidal library.

## Next steps, in order

### 1. Make it real (a day)
1. Commit everything, in sensible chunks.
2. Run the server on the home box properly:
   - gunicorn or uvicorn behind launchd;
   - `run_jobs` with schedules on;
   - `DEBUG=False`, a secret key from `.env`;
   - a real `API_TOKEN` via `manage.py api_token`;
   - reached over Tailscale.
3. Install the app on the JM21 and M23 and run the device check (Settings › Device check) with Tidal, then UAPP, playing. The report decides whether play-from-search and the media browser can start specific tracks.
4. Set `API_CONTACT` so MusicBrainz backfills profiles.

### 2. Measure before changing models (2–3 days)

There's no reliable way yet to tell whether a change makes recommendations better. Build one first.
- **Offline evaluation:** rebuild lists as of a past date (say 6 weeks ago), then score them against what happened next: tracks played through, Keeps, list edits, and later favourites. Report it per list type and per context.
- **Labels from everything you do:**
  - verdicts;
  - list removals (a soft No) and additions (a strong Yes);
  - draft keeps versus discards;
  - Tidal favourites;
  - plays after a list was pushed.
- **Tidal read-back (planned in `CATALOGUE.md`):** before each push, compare a synced playlist with what was last pushed. Tracks you deleted in Tidal become No, tracks you added become Yes.

### 3. Better models: where a bigger or better model would help, and where it wouldn't

| Model | Now | Will a better or bigger model help? | Suggested |
|---|---|---|---|
| **Track scorer** (does a track stick?) | logistic / LightGBM, test AUC 0.635 | Somewhat. The limit is labels and features, not model size. | LightGBM **ranking** (LambdaRank) trained on the step-2 labels, with features for session position, list context, artist profile, and the track's audio against the list's centre. Retrain nightly. |
| **Context ranker** (what fits now) | multi-class logistic regression, top-3 24.6% | Yes, cheaply. | Gradient boosting with more inputs: device (DAP or phone), headphones or speaker, calendar, recent energy trend, day type. Also, rank **lists** directly, learning from which list you open and play through, rather than contexts. |
| **Track embedding** (sound + how you play it) | Essentia embedding PCA'd, joined with word2vec session vectors | Yes. This is the biggest lever. | Train a **personal two-tower or contrastive model** on your 7.9k sessions: positive pairs are tracks played close together in deliberate sessions, inputs are audio embedding + artist profile + tags. It learns one space that respects sound and your habits, and would improve contexts, radio, Explore, suggestions and the map at once. Small enough to train on CPU in minutes, and doesn't need to be big. |
| **Audio models** | Essentia discogs-effnet, LAION CLAP | Maybe. | Try a newer music model (MERT or MuQ-style embeddings, a music-specific CLAP) on a sample, and judge it with the step-2 evaluation before re-running the whole library. |
| **Text understanding** | gpt-4.1-mini (`OPENAI_MODEL`) | Little; parsing is already 24/24. | Keep it small. Use a stronger model only for naming and for explaining "why this track". |

The order matters. Without the step-2 evaluation, a bigger model can't be shown to be better, so build the evaluation, then the session-trained embedding, then the ranking scorer.

### 4. Product
- **Two-way Tidal sync:** the read-back above, plus a "Pin" option so a list stops refreshing.
- **Context controls in the app:** rename, merge, split, and move a track to another context. The server already respects `named_by_user`.
- **Offline for list edits and Explore requests:** extend the outbox.
- **Map:**
  - colour by country, language, era or energy;
  - draw lists as routes;
  - a "you are here" pulse for what's playing;
  - Explore from any point;
  - check performance on the DAPs.
- **Explore further:**
  - Explore a genre or a place ("Icelandic", "UK garage"), not just an artist;
  - bring in similar artists' top tracks from Tidal, not only ones already in the catalogue.
- **Discover:** Fresh fills as enrichment catches up. Add "releases from labels and remixers" as their own tab.

### 5. Engineering
- Rebuild lists in parallel with cached lookups, so the 25-minute rebuild drops to a few minutes. Persist Tidal "not found" results.
- Tests: list building, edits surviving a rebuild, Explore with a mocked Tidal, `understand.py`, artist profiles. App: screenshot tests for the design system, and a test that sync sends an outbox item once.
- Restore the job-run log tail for the lists job: suggestions and Tidal lookups currently log nothing useful.
