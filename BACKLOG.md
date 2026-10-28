# audio-brain backlog

Every open task, written so one agent can take one task cold. Read `NEXT.md` first for where things stand, then
`spec.md` for intent; the Android app's own plans are `android/PLAN.md` and `android/CATALOGUE.md`.

Tasks are grouped into **waves**. Tasks in the same wave can run in parallel: they touch different files, or only
append to shared ones. A task lists what it depends on. Tasks marked **(you)** need George: devices, accounts or
decisions.

Server and cluster work (the move to the new node, the nightly failures, local MusicBrainz and Discogs) is planned
separately in [INFRA.md](INFRA.md), with the same rules.

## Rules for every agent

- **Start from a clean commit** on a branch named after the task (`b12-edges-mix`). Work in your own worktree.
- **Migrations:** apps get new migrations in parallel, so expect number clashes in `recommend/migrations`. Before
  merging, rebase on `main` and rerun `makemigrations` (or `makemigrations --merge`). Never edit an applied migration.
- **Hot files.** Edit `recommend/retrieve.py`, `recommend/lists.py` and `recommend/models.py` in small, additive
  hunks: append new functions or models, and add new signals behind a weight or flag that's off by default. Don't
  reformat or reorder these files.
- **Model or list changes must be backtested.** Add a variant to `recommend.backtest.VARIANTS` and run
  `manage.py backtest --reuse --variant default --variant <yours>` (after B1, use its multi-cutoff mode). Report the
  table in the PR. Turn a feature on by default only if it wins across cutoffs.
- **Tests:** `cd server && uv run python manage.py test recommend api` must pass. Add tests for new logic. The
  Android build is `cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug`.
- **No live side effects** in tests or backtests: no Tidal writes, no LLM calls (mock them). The backtest clone
  refuses to run against the live database. Keep it that way.
- **Style:** match the surrounding code: plain-English docstrings that say why, module docstrings that explain the
  design, little else in comments. Use `uv add` for new dependencies, and avoid ones that need system libraries
  (LightGBM was dropped for needing `libomp`).
- When done, update the task's line in **Status** at the bottom, and `NEXT.md` if the state of things changed.

---

## Wave 0: prerequisite

**A0. Commit the modelling round.** Everything from the 18 Oct second session is uncommitted (backtest, labels,
taste vectors, scorer and context-ranker rewrites, read-back, draft outcomes, tests). Commit it in sensible chunks on
`main`, so every agent branches from it. *Done when:* `git status` is clean and the tests pass.

---

## Wave 1: measurement and the set tracklist feature (all parallel)

**B1. Backtest across several cutoffs.**
- *Why:* one six-week window is too noisy to choose between variants (played rates are about ±0.8 points, new-track rates about ±1.6).
- *What:*
  - `manage.py backtest --cutoffs 42,84,126,168` prepares one clone per cutoff, scoring each against the following 6 weeks only (equal horizons), and reports each variant's mean and spread across cutoffs.
  - Keep `--reuse`: clones are named `audio_brain_backtest_<date>`, and `drop_database`'s suffix check must still hold.
  - Add a paired comparison of variant against default, with a bootstrap over tracks, so the report says whether a difference is real.
- *Files:* `recommend/backtest.py`, `recommend/management/commands/backtest.py`.
- *Done when:* a four-cutoff run of `default` and `taste` prints means, spreads and a "real or noise" verdict.

**B2. Set tracklists you provide.**
- *Why:* sets you hear (at a gig, on the radio, from a friend) often have no YouTube or MixesDB page to find.
- *What:* give a set's tracklist as pasted text, a file, or a link (1001Tracklists, MixesDB, a YouTube video or SoundCloud page), and it gets processed like a found one:
  - **Parse.** Reuse `sets.youtube.parse_lines` and `sets.tracklists.parse_tracklist` for timestamped "Artist - Title" lines, numbered lines, `[label]` suffixes and "ID - ID". For messy text, an optional small-LLM clean-up (`recommend.llm.parse` into a pydantic list of entries), with the rule-based parse as the fallback.
  - **Match.** Use `catalogue.track_for`. Unknown tracks are looked up on Tidal (ISRC, then search, as in `enrichment.tidal.match_track`) and queued for audio analysis.
  - **Save** as a `Set` with `source="provided"`: title, DJ, date, optional URL and `SetTrack` rows, with `tracklist_source="provided"`. Attach it to an existing `Set` when one is named.
  - **Use it:**
    - a "From this set" track list (a `Mix` with kind `set`), made of the matched tracks in order, playable and syncable;
    - its unheard tracks join the discovery queue and New to you candidates;
    - the set counts as DJ endorsement in the scorer (`dj_sets`), as found sets already do.
  - **API:** `POST /api/v1/sets/tracklist` (text or URL, optional title, DJ, date and set id), returning the set with match status per line. Add `PATCH` to fix a line's match.
  - **App:** a "Paste tracklist" entry on the Sets screen and the share target (Android share sheet with text or a URL), showing matched, unmatched and ID lines with a fix-match picker.
  - **Command:** `manage.py add_tracklist <file|url> --title --dj`.
- *Files:* new `sets/provided.py`, `api/sets.py` (new router), `sets/models.py` (a source choice), app `feature/sets`. Touches `recommend/lists.py` only to register the `set` kind in `ORDER`.
- *Depends on:* none. The app part can follow the server part in the same task.
- *Done when:* pasting a 1001Tracklists page's text produces a set with 80% or more of its known lines matched, a playable list, and unknown tracks queued for analysis; there are tests for parsing a dozen real formats.

**B3. Check Tidal's own recommendations.**
- *What:*
  - Check whether the official API's user recommendations (My Mixes, Discovery Mix, New Arrivals) are readable with our user token (`clients/tidal.py`).
  - If yes: a `tidal_recommendations` snapshot job that weekly stores each Tidal mix's track list (new model `ExternalMix`: source, name, taken_at, tracks).
  - If no: write down why, and what's available instead.
- *Files:* `clients/tidal.py` (one method), new `recommend/external.py`, `jobs/registry.py` (append one job).
- *Done when:* a week of snapshots is stored, or the finding is in NEXT.md.

**B4. ListenBrainz as an outside baseline. Skipped for now (George, 18 Oct).** Kept here for later.
- *What:*
  - Script the export of scrobbles to ListenBrainz (their import takes Last.fm history). This needs **(you)** for the account and token.
  - Fetch its collaborative-filtering track recommendations (the `recommendations/cf/recording` endpoint) and match them to the catalogue by MBID.
  - Store them as `ExternalMix(source="listenbrainz")`, shared with B3 (whichever lands first creates the model).
- *Files:* new `clients/listenbrainz.py`, `recommend/external.py`.
- *Done when:* ListenBrainz recommendations are in the database and can be scored by C3.

**B5. Production settings and a fast rebuild.**
- *What:*
  - `SECRET_KEY` and `DEBUG` from `.env`, with safe defaults for production.
  - A gunicorn or uvicorn entry point and launchd plists in `deploy/`, for the home box **(you)** to install.
  - `run_jobs` with schedules on.
  - Cut the 25-minute Mixes rebuild to a few minutes: run suggestions in parallel (a thread pool), cache Tidal lookups, and persist "Tidal doesn't have it" results (they're already `EnrichmentAttempt`s; check `playable` respects them).
  - Restore useful log lines for the lists job.
- *Files:* `server/settings.py`, `deploy/`, `recommend/lists.py` (`playable` and the suggestions loop only), `recommend/actions.py` (`suggestions`).
- *Done when:* `manage.py run_job mixes` finishes in under 5 minutes with the same lists.

**B6. MusicBrainz 503s and the enrichment backfill.**
- *Why:* `API_CONTACT` is set in `.env` and sent in the User-Agent header (`clients/base.py`), yet MusicBrainz answered 503 and the artist-profile backfill stopped after 65 artists.
- *What:*
  - Find out why: MusicBrainz returns 503 when a client goes over 1 request a second, including bursts from parallel jobs.
  - Make `clients/musicbrainz.py` hold to 1 a second across processes (a shared lock or a single lane), and back off on 503 using `Retry-After`.
  - Rerun the backfill until most artists with audio are profiled, and report coverage with `manage.py coverage`.
- *Files:* `clients/musicbrainz.py`, `clients/base.py` (rate limit), `enrichment/artists.py` (resume logic).
- *Done when:* more than 70% of artists with analysed tracks have a profile, or the blocker is documented.

---

## Wave 2: models (parallel with each other; all need B1)

**C1. Rank new and familiar tracks separately.**
- *Why:* the backtest says the new scorer helps unplayed tracks but costs familiar hits ("lift vs recent" is below 1 everywhere).
- *What:* in `retrieve.shape`, rank the familiar slots by recency and canon (and plays in the last months), and the new slots by the scorer and fit. Add it as a variant first.
- *Files:* `recommend/retrieve.py` (`shape` and a new helper), `recommend/backtest.py` (a variant).
- *Done when:* across B1's cutoffs, played % and new played % both match or beat the old-scorer baseline (10.6 and 6.5 at the 6 Sep cutoff).

**C2. Taste vectors beyond retrieval.**
- *What:* each of these is a separate variant:
  - (a) `contexts.session_components` from taste vectors instead of word2vec;
  - (b) Explore and suggestions seeded in taste space;
  - (c) sequencing (`editorial.sequence`) using taste similarity in its `follows` score;
  - (d) the taste weight in `SIGNAL_WEIGHTS`, retuned after C1.
- *Files:* `recommend/contexts.py`, `recommend/explore.py`, `recommend/editorial.py`, `recommend/backtest.py` (variants).
- *Depends on:* B1. Coordinate with C1 on `SIGNAL_WEIGHTS`.
- *Done when:* each has a backtest verdict. Turn on only the winners.

**C3. Score outside recommendations the same way.**
- *What:* score `ExternalMix` rows (Tidal's mixes) with `backtest.score`, from the day each was taken to 6 weeks later. Print them beside our lists in a weekly `manage.py compare_external` report.
- *Files:* `recommend/external.py`, a new command.
- *Depends on:* B3.
- *Done when:* the report has run on at least one snapshot week.

**C4. A blind interleaved playlist (the fair head-to-head).**
- *What:*
  - A synced "Blind" list each week: tracks from our best list and from Tidal's Discovery Mix, interleaved by team draft, with no labels and the source stored per track.
  - Score each source by plays, Keeps, app swipes and Tidal deletions (via read-back) within 2 weeks.
- *Files:* `recommend/external.py`, `recommend/lists.py` (register the list, append-only).
- *Depends on:* B3, and D7 if the read-back needs fixing.
- *Done when:* two weeks of interleaved results are reported.

**C5. Taste model variants.**
- *What:* measured on `taste.evaluate`'s held-out retrieval, then the backtest:
  - pairs only from tracks you came back to;
  - a per-track adjustment for played tracks;
  - hard negatives: similar-sounding tracks skipped past in the same session;
  - more epochs with a learning-rate schedule;
  - an ensemble of seeds.
- *Files:* `recommend/taste.py` only.
- *Done when:* recall@10 beats 5% on held-out sessions without hurting the backtest.

**C6. Newer audio embeddings, on a sample.**
- *What:*
  - Extract a newer music model's embeddings (MERT- or MuQ-style, or a music-specific CLAP) for a 2,000-track sample from Tidal previews (`clients/previews.py`).
  - Feed them to the taste model in place of Essentia, and compare held-out recall on the sample.
  - Recommend whether a full re-analysis is worth it, with the time cost per track on this Mac.
- *Files:* new `enrichment/embeddings_v2.py`, experiment script. No schema change unless it wins.
- *Done when:* a written comparison is in NEXT.md.

**C7. Train on labels.**
- *What:*
  - Once `recommend.labels` has 200 or more explicit labels, add them to the scorer's training: verdicts and list edits as labels on top of "durable affinity", with sample weights from their strength.
  - Also train a list-level ranker for which list you open and play through (needs D3's per-mix ratings and play logging).
- *Files:* `recommend/scorer.py`, `recommend/labels.py`.
- *Depends on:* real use of the app for a few weeks.
- *Done when:* test AUC on recent labelled tracks beats the current scorer.

**C8. List-shaping sweeps.** Use B1's reusable clones to sweep novelty share, `editorial.COHERENCE`,
`MAX_PER_ARTIST` and the dormant-context rotation. *Files:* `recommend/backtest.py` (variants only). *Done when:*
the best settings are found and, if they win across cutoffs, made the defaults.

---

## Wave 3: discovery and product (mostly parallel; server and app parts noted)

**D1. The LLM suggests, the models decide: discovery beyond your data.**
- *What:* a nightly step:
  - Build a compact profile: each active context's top artists and styles, new interests, recent Keeps, and recent Nos and discards from `recommend.labels`.
  - Ask a small model (`OPENAI_MODEL`) for artists, tracks and labels not in the catalogue, each with a one-line reason. Ask it to prefer deep cuts and to stay within the context.
  - Resolve each on Tidal and ingest it (`explore.ingest`), analyse it, then score it with the taste vectors and the scorer. Drop invented tracks, ones Tidal can't find, and poor fits.
  - The survivors feed New to you, Explore and an "LLM picks" pool, logged as `source="llm"`, so C3 and C4 can measure them.
  - Cap it at about 20 calls a night. Cache answers per context per week.
- *Files:* new `recommend/llm_discovery.py`, `jobs/registry.py` (append a step), `recommend/lists.py` (feed New to you; append-only).
- *Done when:* a nightly run adds candidates with reasons, at least half of what the LLM proposes resolves to playable tracks, and none of them come from your Never list.

**D2. Explore by genre or place, and the Edges mix.**
- *Explore:* "Icelandic", "UK garage": the LLM proposes seed artists (reusing D1's resolver) and artist profiles filter by country or language. Entry points in the app's Explore.
- *Edges* (from the spec, and the missing piece of phase 5): a weekly list of tracks just outside your clusters:
  - graph and taste neighbours of each top context that aren't in any context;
  - artists the LLM proposes as bridges between two contexts.
  - Reasons like "Edges: next to Slow Sunday".
- *Files:* `recommend/explore.py`, new `recommend/edges.py`, `recommend/lists.py` (register `edges`), app Explore entry.
- *Depends on:* D1's resolver.
- *Done when:* both work from the app, and Edges is in the backtest.

**D3. Feedback from the spec's design.**
- *What:*
  - Per-mix rating (1–5 and an optional line) and occasional "which of these two fits better?" comparisons, through the outbox (`POST /sync`) into new `MixRating` and `Comparison` models.
  - Both feed `recommend.labels`: comparisons as pairwise labels.
  - App: a rating row on the mix sheet, and a comparison card asked rarely.
- *Files:* `recommend/models.py` (append), `api/sync.py`, `recommend/labels.py`, app `feature/feedback`.
- *Done when:* ratings and comparisons sync offline-first and show up in `labels.summary()`.

**D4. Context controls in the app.**
- *What:* rename, merge, split, and move a track to another context. The server respects `named_by_user`, and needs merge and split endpoints and stable ids across rebuilds (`contexts.closest_context`).
- *Files:* `api/music.py` (context endpoints), `recommend/contexts.py` (merge, split and pinned members), app contexts screens.
- *Done when:* edits survive a nightly rebuild.

**D5. Offline for list edits, Explore and Ask.**
- *What:* extend the Room outbox to list edits, sync toggles, Explore requests and Ask prompts. Requests queue offline, then run on reconnect and notify.
- *Files:* app `:core:data`, `api/sync.py`.
- *Done when:* a list edit made in flight mode lands after reconnecting, exactly once (with a test).

**D6. Map improvements.**
- *What:*
  - colour by country, language, era or energy (the server adds those fields to `/api/v1/map`);
  - lists drawn as routes;
  - a "you are here" pulse for the track playing;
  - Explore from any point;
  - performance on the DAPs (check with **(you)**).
- *Files:* `recommend/atlas.py`, `api/atlas.py`, app map module.
- *Done when:* each option renders at 60 fps on the emulator; DAP numbers come from you.

**D7. Two-way Tidal sync, finished.**
- *What:*
  - Check the read-back (`playlists.read_back`) against real Tidal **(you)**: delete a track in a synced playlist and confirm it's removed after the next rebuild.
  - Add **Pin**: a list that stops refreshing (a `ListSync.pinned` field, honoured in `build_lists`).
  - Confirm the UNLISTED playlists appear in your Tidal library.
- *Files:* `recommend/playlists.py`, `recommend/lists.py` (the pin check), app list menu.
- *Done when:* checked against real Tidal.

**D8. The Discover tab for releases from labels and remixers.** Split the release queue by source (artist, remixer
or label) into its own tab, with label pages. *Files:* `recommend/lists.py` (`release_items`), app Discover. *Done
when:* the tab shows each source separately.

**D9. A weekly discovery summary** (from the spec). Counts per artist and label, and the five best fits with
reasons, as a notification and a Discover card. *Files:* new `discovery/summary.py`, `jobs/registry.py`, app.
*Done when:* it arrives weekly.

---

## Wave 4: playback, Claude and context polish

**E1. MCP server for Claude** (spec phase 3).
- *Plan:* **[MCP.md](MCP.md)** supersedes the outline below: remote (claude.ai on the phone), MCP Apps components, draft refining, stats and insights.
- *What:*
  - Tools: `search_by_vibe`, `play_context`, `whats_new`, `time_travel` ("what was I into when we moved to France?"), `explain_pick` and `add_tracklist` (B2). They sit over the existing query functions.
  - It talks to the API with the bearer token, over Tailscale.
- *Files:* new `mcp/` package.
- *Done when:* "play something for cooking" works from Claude, writing the Now playlist.

**E2. Sonos.**
- *What:* SoCo control at home (play a list's Tidal playlist, pause, volume), from the app and E1. In the other house, use the Sonos cloud API or Claude's Sonos connector.
- *Files:* new `playback/sonos.py`, API endpoints, app route picker.
- *Done when:* a tap in the app plays on a Sonos at home.

**E3. Phase 6: calendar, location and weather.**
- *What:*
  - Calendar: **Google Calendar** (decided 18 Oct). Read events with the Google Calendar API, using OAuth for George's account, stored like the Tidal token in `library.OAuthToken`. Features: in an event now or soon, its kind (work, social, travel, gym) from title keywords or a small-LLM label, and free time left today.
  - Location from the app (which house), and weather.
  - Device (DAP or phone) and headphones or speaker, sent by the app with each `/now` call.
  - All become context-ranker features (`context_ranker.FEATURES`), backtested by its own held-out top-3.
- *Files:* `recommend/context_ranker.py`, `api/music.py` (`/now` params), app.
- *Done when:* top-3 improves on 34.6% with the new inputs.

**E4. Set prep mix** (spec), without Beatport (decided 18 Oct: not used). Candidates in a chosen BPM and key range
(compatible keys on the Camelot wheel, `editorial.CAMELOT`) that aren't yet in the set library, from your catalogue
and Tidal's similar tracks, using the Essentia BPM and key. *Files:* new `recommend/setprep.py`, and a recipe in
`recommend/actions.run_recipe`. *Done when:* it's runnable from Ask ("set prep 124–128, 8A") as a recipe.

**E5. YouTube Music likes** (phase 1 remainder) via ytmusicapi into `Favourite`. *Files:* `library/`. *Done when:*
your likes are imported and matched.

---

## Wave 5: hardening

**F1. Tests to fill gaps:**
- server: list building, edits surviving a rebuild, Explore with a mocked Tidal, `understand.py`, artist profiles;
- app: screenshot tests for the design system, and a test that sync sends an outbox item once.

*Files:* tests only.

**F2. Device check (you).** Install on the JM21 and M23 and run Settings › Device check with Tidal, then UAPP,
playing. The report decides whether play-from-search and the media browser can start specific tracks. Then a
follow-up agent task implements whichever route works.

---

## Status

| Task | Wave | State |
|---|---|---|
| A0 commit | 0 | done (a14b5c2) |
| B1 multi-cutoff backtest | 1 | done: taste is noise on played, worse on new tracks; see NEXT.md |
| B2 set tracklists you provide | 1 | done (needs `migrate sets`; app not yet tried on a device) |
| B3 Tidal recommendations | 1 | done: readable via official API; daily `tidal_recommendations` job into `ExternalMix`; snapshots accrue after migrate (see NEXT.md) |
| B4 ListenBrainz baseline | 1 | skipped for now |
| B5 production settings and fast rebuild | 1 | done (branch b5-prod-fast-rebuild): rebuild 612–705 s → 245–260 s; deploy/ ready to install (you) |
| B6 MusicBrainz 503s and backfill | 1 | fixed on b6-musicbrainz-backfill; backfill at 67% profiled (49% found), still running |
| C1 new versus familiar ranking | 2 | done: `familiar-slots` wins across all B1 cutoffs; enabled. Ask now exposes a Familiar ↔ Exploratory slider that selects the two expert pools and their quota; outcomes retain the selected setting |
| C2 taste vectors beyond retrieval | 2 | variants implemented (`taste-contexts`, `taste-explore`, `taste-sequence`); awaiting B1 verdict |
| C3 score outside recommendations | 2 | blocked on B3 ExternalMix snapshots |
| C4 blind interleaved playlist | 2 | blocked on B3 ExternalMix snapshots |
| C5 taste model variants | 2 | open |
| C6 newer audio embeddings | 2 | open |
| C7 train on labels | 2 | waiting for labels |
| C8 list-shaping sweeps | 2 | sweep variants implemented; awaiting B1 multi-cutoff verdict |
| D1 LLM discovery | 3 | implemented: weekly cached candidates are Tidal/audio verified, scored, Never-filtered, and logged as `llm`; needs a live run and outcome data |
| D2 Explore by genre or place, and Edges | 3 | Edges implemented and test-covered; genre/place Explore entry points remain |
| D3 ratings and comparisons | 3 | done: API, labels, offline outbox, rating row and comparison controls |
| D4 context controls | 3 | server rename/move/merge/split with rebuild-persistent overrides implemented; Android management UI remains |
| D5 offline edits and requests | 3 | list edits are queued and replayed through `/sync`; Ask now has an online Familiar ↔ Exploratory mood control; offline Ask, Explore and sync-toggle persistence remain |
| D6 map improvements | 3 | implemented: modes, routes, now-playing pulse and Explore; needs DAP performance check |
| D7 two-way Tidal sync | 3 | pin and read-back implementation complete; needs real Tidal validation |
| D8 labels and remixers tab | 3 | done: release queue split by artist, remixer and label |
| D9 weekly summary | 3 | done: weekly Discover card/job; device notification remains optional polish |
| E1 MCP server | 4 | open |
| E2 Sonos | 4 | open |
| E3 calendar (Google), location, weather | 4 | open |
| E4 set prep (no Beatport) | 4 | open |
| E5 YouTube Music likes | 4 | open |
| F1 tests | 5 | open |
| F2 device check | 5 | you |
