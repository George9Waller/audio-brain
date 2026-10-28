# audio-brain: where it stands and what's next

Handoff notes, 18 Oct 2026. Start a new session by reading this, then `spec.md`, `android/PLAN.md` and
`android/CATALOGUE.md`. Releasing the app (signing key, tags, Obtainium) is `android/RELEASING.md`.

**Everything left to do is in [BACKLOG.md](BACKLOG.md)**, as self-contained tasks in waves for parallel agents. The
next-steps sections below are the history behind it.

Wave 2 groundwork: the new-vs-familiar ranking and taste/list-shaping experiments are available as off-by-default
backtest variants (`familiar-slots`, `taste-contexts`, `taste-explore`, `taste-sequence`, `novelty-*`,
`coherence-*`, `artist-cap-*`, `resurface-10`, `dormant-*`). They must not be enabled until B1 supplies the multi-cutoff verdict.

**C1 result (19 Oct):** `familiar-slots` is enabled. Across B1's four cutoffs it improved played rate by **3.24
points** (95% CI 2.56–3.93), came-back rate by **1.55 points** (1.13–1.98), lift versus recent by **0.25**
(0.20–0.30), and plays covered by **3.26 points** (1.54–5.14); all four cutoffs agreed. New played rate changed
−0.50 points (−1.81 to 0.78), so there is no reliable new-track cost.

## State

Everything up to the first session is in the initial commit; the second session (18 Oct, below under "Modelling
round") is in a14b5c2.

### Modelling round (18 Oct, second session)

- **Backtest** (`manage.py backtest`, `recommend/backtest.py`):
  - clones the database and deletes everything from the cutoff on: plays, verdicts, edits, the log, unreleased releases, and tracks known only through later plays;
  - reruns the real pipeline with the clock stopped at the cutoff (time-machine), with no Tidal or LLM calls;
  - scores every list against the plays after the cutoff: played, came back (2+ days), new played, lift against chance, lift against recent plays, discoveries caught;
  - `--reuse` keeps a prepared clone (`audio_brain_backtest`, about 1.1 GB; `dropdb audio_brain_backtest` to free it), and `--variant` compares build variants (`VARIANTS`);
  - reports go to `server/models/backtests/<cutoff>/`.
- **Labels** (`recommend/labels.py`) gather everything, from −1 to +1:
  - verdicts, list edits, draft outcomes, played or passed-over suggestions, and favourites;
  - drafts now record what became of them (`DraftOutcome`: kept, played, discarded, expired, and tracks swiped out).
- **Tidal read-back** (`recommend/playlists.read_back`): before a synced playlist is rewritten, tracks you deleted or added in Tidal become list edits. `Playlist.pushed` records what was last written. Built and tested, but not tried against real Tidal yet; sync work is paused for modelling.
- **Taste vectors** (`recommend/taste.py`, `TasteVector`, in the nightly models job):
  - a small PyTorch network over sound, CLAP, styles, artist tags, audio numbers and an artist ID, trained contrastively on pairs played within 3 tracks of each other, weighted by session intent, with a logQ correction;
  - on the most recent tenth of sessions, held out: partner track in the top 10 of 33k, 4.1–5% against 1.1% for sound and 3.9% for word2vec (which can only place 64% of pairs); median rank 1.7k against 4.3k for sound;
  - trains in about a minute on CPU.
- **Track scorer:**
  - five taste features added; for its test score they come from a taste model trained only on sessions before the test period;
  - test AUC 0.635 → **0.689**; `taste_recent` and `taste_liked` are the top two features;
  - LightGBM was tried and dropped, because it needs Homebrew `libomp`.
- **Context ranker** rewritten as a pairwise model, each (moment, context) scored by boosted trees:
  - features: share of the last two hours, last track's context, taste closeness to what's playing, hour, weekday and country fit, plays in the last day, week, month and year, days since last played;
  - top-3 accuracy on the last 180 days **34.6%** against 23.7% for the old logistic regression on the same split (top-1 16.2%).
- **Backtest results** (cutoff 6 Sep, scored on 1,419 plays to 18 Oct):

  | Variant | Played % | New played % | New in mixes % | Lift vs recent | Plays covered % |
  |---|---|---|---|---|---|
  | old scorer | 10.6 | 6.5 | 7.1 | 0.48 | 25.1 |
  | new scorer, taste off (live) | 8.6 | 7.6 | 9.9 | 0.38 | 21.0 |
  | taste signal on | 9.4 | 7.0 | 7.6 | 0.43 | 20.1 |
  | taste replaces session signal | 10.4 | 6.2 | 6.3 | 0.49 | 20.1 |

  - New-to-you picks were barely above chance before (1.2×).
  - The new scorer helps new tracks and costs familiar hits.
  - The taste signal does the opposite.
  - One window is too noisy to choose on: played rates are about ±0.8 points and new-track rates about ±1.6. So the taste weight in `retrieve.SIGNAL_WEIGHTS` stays 0.
- **Multi-cutoff backtest (B1):** `manage.py backtest --cutoffs 42,84,126,168 --variant taste --reuse [--jobs 2]`.
  - One clone per cutoff (`audio_brain_backtest_<yyyymmdd>`, about 1.2 GB each; `--reuse` keeps them, `dropdb` frees them), every cutoff scored on the 42 days after it (`--horizon`).
  - Reports each variant's mean and spread across cutoffs, and a paired bootstrap over tracks against `default` (95% interval of the mean difference; "real" when it leaves out zero, with how many cutoffs agree). Saved to `server/models/backtests/multi/`.
  - A cold run of four cutoffs of two variants took about 32 minutes with `--jobs 2` (cloning by dump and restore is about 3.5 minutes each, as the live database is usually in use); with `--reuse`, each further variant is a few minutes per cutoff.
  - First result, `taste` (weight 1.0) against `default`, cutoffs 6 Sep, 26 Jul, 14 Jun and 3 May (mean ± sd across cutoffs):

    | Measure | default | taste | taste − default (95% interval) | Verdict |
    |---|---|---|---|---|
    | Played % | 9.65 ± 2.63 | 9.68 ± 2.53 | +0.03 (−0.50 to 0.57) | noise |
    | Came back % | 4.17 ± 1.00 | 4.12 ± 1.11 | −0.02 (−0.34 to 0.31) | noise |
    | New played % | 13.6 ± 10.5 | 10.2 ± 6.9 | −3.36 (−5.39 to −1.44), 3/4 cutoffs | **worse** |
    | New in mixes % | 17.0 ± 13.3 | 12.0 ± 8.2 | −5.05 (−7.59 to −2.65), 3/4 cutoffs | **worse** |
    | Lift vs recent | 0.68 ± 0.27 | 0.67 ± 0.23 | +0.00 (−0.04 to 0.04) | noise |
    | Discoveries in a list % | 13.1 ± 3.8 | 12.6 ± 5.8 | −0.52 (−2.99 to 2.01) | noise |
    | Plays covered % | 21.4 ± 2.6 | 20.3 ± 2.8 | −1.14 (−2.80 to 0.47) | noise |

    - The taste signal buys nothing on familiar tracks and costs new-to-you hits, so its weight stays 0.
    - **The scorer wasn't in use at any of the four cutoffs:** its test AUC (0.667–0.694) didn't beat "artist only" (0.665–0.693) by `scorer.MIN_GAIN` (0.02), so `default` here is the hand-weighted score. The 6 Sep clone of the first round passed (0.715). Whether the new scorer stays in lists is still open, and close: C1 should look at it.
    - The 3 May window is unusual (new played 28.7% against 6–13% elsewhere, with 2,311 plays), which is most of the spread in the new-track measures.

### Tidal's own recommendations (B3, 18 Oct)

- **Readable through the official API** (openapi.tidal.com v2, spec 1.10.184) with our user token and its `recommendations.read` scope. Read-only; nothing is written.
  - The `userRecommendations` resource (with myMixes / discoveryMixes / newArrivalMixes) is **gone**: every form of it answers 404. It's been split into one resource per group, each addressed as `me`:
    - `GET userDailyMixes/me/relationships/items?include=items`: My Mix 1–8;
    - `GET userDiscoveryMixes/me/relationships/items?include=items`: My Daily Discovery ("updates every morning");
    - `GET userNewReleaseMixes/me/relationships/items?include=items`: My New Arrivals;
    - `GET userOfflineMixes/me/relationships/items`: empty for us (`userOfflineMixes/me` itself is 404).
  - Each mix is a `playlists` resource with `playlistType: MIX` and `accessType: UNLISTED`. Its tracks come from `GET playlists/{id}/relationships/items?include=items.artists,items.albums`, 20 a page with `links.next` cursors. They include the ISRC, title, version and the Tidal IDs of the artists and albums.
  - Not useful: `userFeedItems` (`filter[owners.id]=me&filter[surface]=INBOX|FEED`) is empty for us. It's where monthly and yearly "history mixes" would be announced. `userRecommendationBlocks/me` is 404.
- So the unofficial API (api.tidal.com/v1 `pages/...`, as used by `tidalapi`) isn't needed. It uses the Tidal app's own client ID, which is against the terms and breaks without warning.
- **Built:**
  - `ExternalMix` (`recommend/models.py`, migration `recommend 0018_external_mix`): source, kind, external_id, name, description, taken_at, seen_at, and tracks in order. Each track is `{external_id, isrc, title, artists, album, track_id}`.
  - The `tidal_recommendations` job (`recommend/external.py`, `TidalClient.recommendation_mixes`) runs **daily at 10:00**, not weekly, on the sync lane, because Daily Discovery changes every morning. A new row is stored only when a mix's tracks change; an unchanged mix just moves `seen_at` on.
  - Tracks are matched to the catalogue by Tidal ID, then ISRC, then artist and title. Nothing is added to the catalogue. `external.rematch()` (run by the job, and to be called by C3 before scoring) links tracks you go on to play. `external.latest(as_of=…)` gives the version of each mix that was live at a date.
  - B4 can store ListenBrainz as `source="listenbrainz"`, with recording MBIDs as `external_id`.
- **First snapshot**, on a scratch copy (10 mixes, about 13 s):
  - My Mix 1–8: 40 tracks each, 82–98% already in the catalogue, and almost all of those already played. They're mostly familiar tracks.
  - Daily Discovery: 10 tracks, 2 known.
  - New Arrivals: 29 tracks, 6 known.
  - So for C3 and C4 the fair comparison is Daily Discovery and New Arrivals against our New to you and Fresh, and My Mix against our context mixes. Unmatched outside tracks aren't analysed, so `backtest.score`'s chance rate for new tracks doesn't strictly cover them.
- Not applied to the live database yet. Run `migrate` after merging. The job then starts at the next 10:00.

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
- **Tracklists you provide (B2, `sets/provided.py`):** paste text, a file or a link (1001Tracklists, MixesDB, YouTube, SoundCloud, any page) with `POST /api/v1/sets/tracklist`, the app's Paste tracklist and share target, or `manage.py add_tracklist <file|url>`.
  - Lines match the catalogue, then Tidal (new tracks are added and analysed by the `tracklist` job); a DJ's own edit nobody has falls back to the original, marked `original`.
  - Each set gets a "From this set" list (`Mix` kind `set`, key `set:<id>`), kept through rebuilds; `PATCH /sets/{id}/tracklist/{position}` fixes a line.
  - On a real 1001Tracklists page (Calvin Harris @ Head Trip, 41 lines): 34 of 40 known lines matched (85%), 19 of them the exact version.
  - Needs `migrate sets` (0005).

### Android (`android/`, Kotlin + Compose, minSdk 29)

- Modules: `:core:design` (design system), `:core:data` (API, Room outbox, sync), `:core:player` (media sessions, feedback notification, device check) and `:app`.
- Five pages: Lists ← Home → Discover → Catalogue (Map or Library) → More. Pages change by swiping or tapping the page names at the top.
- Home: the sigil, focus mode, the ask box, quick actions and top lists.
- Now playing: the chip and its drawer.
- Track rows: swipe for verdicts; in a list, swipe left removes the track.
- Ask flow: Building, then a Draft to review.
- Radio and Explore.
- Tracklists: Paste tracklist on the Catalogue's Sets tab and on each set, and a share target for text and links; each line shows its match, with a fix-match picker. Not yet tried on the emulator.
- Map: drawn on a native canvas, with double-tap zoom and lists shown as their own tracks.
- Offline: verdicts and release decisions go through the outbox. Lists, the map and Now are cached.

### Running it now (dev, on this Mac)

- **Server:** `cd server && API_TOKEN=devtest123 ALLOWED_HOSTS=localhost,127.0.0.1,10.0.2.2 uv run python manage.py runserver 0.0.0.0:8000`
- **Worker:** `API_TOKEN=devtest123 uv run python manage.py run_jobs --no-schedule`. Drop `--no-schedule` on the real server.
- **Emulator:** AVD `Pixel_10_Pro`. Set the app up with the link `audiobrain://connect?url=http://10.0.2.2:8000&token=devtest123`.
- **Build:** `cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug`
- **Tests:** `cd server && uv run python manage.py test api` (7 tests) and `uv run python manage.py eval_prompts`.
- **Settings (B5):** `DEBUG` is now off unless `.env` or the command line says `DEBUG=1`; add it to the dev server command above if you want Django's debug pages. `SECRET_KEY` is required from the environment (`.env` on the Mac, the AWS secret on the cluster). Every key is in `server/.env.example`.
- **On the home box (B5):** gunicorn and the `run_jobs` worker (schedules on) under launchd; install steps, `api_token` and Tailscale are in `deploy/README.md`. Not installed yet **(you)**.
- **Mixes rebuild (B5):** builds on `MIXES_THREADS` threads (default 6). Its log now has a line per list (tracks, Tidal lookups, time) and suggestion progress.

## Known issues

- **MusicBrainz 503s (B6, fixed on `b6-musicbrainz-backfill`):** the User-Agent was fine. MusicBrainz sheds load globally (503 with `Retry-After: 0` when its shared bucket runs dry, about 1 in 6 requests even at one every 5 s), and our per-IP rate went over 1 a second when the identity stage and the profiles ran side by side. urllib3 then retried at once, in bursts. Now every process shares one MusicBrainz lane (`clients.base.SharedLane`, a flock'd file in `RATE_LIMIT_DIR` then; a database row since I5a, 28 Oct) and backs off exponentially on 429/503. Profiles cost one request per artist and include analysed artists without plays; `manage.py coverage` shows them. Backfill on 19 Oct: 67% of artists with analysed tracks profiled, 49% found on MusicBrainz (the rest have no MBID and no track MusicBrainz knows; Last.fm tags fill in for them).
- **The context ranker** was down to 24.6% top-3 after contexts became sound-led; the pairwise rewrite brings it to 34.6% (see Modelling round).
- **Fresh has only 15 tracks:** just 30 of 93 releases have analysed tracks so far. The enrichment backfill is still running.
- **List edits, sync, Explore, Ask and suggestions are online-only.** Only verdicts and release decisions queue offline.
- ~~**The full Mixes rebuild takes about 25 minutes**~~ (B5): on a copy of the live database it now takes about 4–4.5 minutes (245–260 s), down from 11–12 minutes (612–705 s), with the same lists. What's left is Tidal: lookups for tracks without a Tidal ID are held to the client's 2 requests a second, and the lists have to be shaped one after another.
- ~~**Settings aren't production-ready**~~ (B5): `SECRET_KEY` and `DEBUG` come from `.env`, and `deploy/` has gunicorn and launchd. Installing it on the home box is still to do **(you)**.
- **Context mixes vary a little from build to build, even on the same data** (found in B5): `retrieve.add_graph_neighbours` takes 400 of 1,000–2,800 graph neighbours with no `ORDER BY`, so which 400 depends on Postgres's plan and the rows a build's Tidal lookups add; the scorer's recency features also move with the clock. An `order_by` there would make builds repeatable, but it changes the lists, so it needs a backtest.
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
4. ~~Set `API_CONTACT`~~: already set; the 503s are BACKLOG B6.

### 2a. Next in modelling (picks up from the modelling round)
1. **Backtest over several cutoffs** (for example 6, 12, 18 and 24 weeks ago) and average them, so variants can be told apart. Then decide on the taste weight and whether the new scorer stays in lists.
2. **Split the scorer's job:** use it for new tracks, where it helps, and rank familiar tracks by recent plays and canon, where the backtest says that's stronger ("lift vs recent" is below 1 for every variant).
3. **Taste vectors in contexts:** use them in place of word2vec in `contexts.session_components`, and for Explore and suggestions.
4. **Context ranker inputs from the app:** device, headphones or speaker, and calendar.
5. **Train on `recommend.labels`** once verdicts and edits accumulate.

### 2. Measure before changing models (2–3 days)

The offline evaluation, labels from everything you do, and the Tidal read-back are now built (see "Modelling round" above).

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
