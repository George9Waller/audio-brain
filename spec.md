Music Catalogue — Recommendation Engine Plan
14 Oct 2026 · @George
Goal and principles
Build a recommendation layer into Music Catalogue that picks what to play from a tap or two, plays it on Sonos or Tidal, and keeps a set of self-refreshing mixes — without local audio files.
• Streaming-first. No owned files needed. Tracks are described from metadata, crowd tags and 30-second previews.
• Explore by design. Every surface reserves a share for tracks outside the usual clusters. The objective is taste fit plus novelty, not time spent listening.
• Minimal input. Ten context buttons and an optional energy dial. Free text is optional.
• Platform-agnostic. Tracks are keyed on MusicBrainz IDs (with ISRC as fallback), so moving between Tidal, YouTube Music and others doesn't reset anything.
• Private by default. Calendar and history are reduced to coarse signals on your own server.
Architecture overview
What you play on Sonos and Tidal scrobbles back to Last.fm, so every listen feeds the next recommendation.
Data and jobs
Your home server runs the backend and every scheduled job. Only machine-learning inference leaves the box: audio embeddings and LLM calls.
• Postgres + pgvector on the home server holds tracks, signals, embeddings, contexts, mix history and feedback. Around 100 MB in total.
• A small API (e.g. FastAPI) sits in front of it. The Electron app and the MCP server talk only to this.
• Tailscale puts the app and the server on one private network, so the API is reachable from either house without opening ports.
• A scheduler (cron or APScheduler) on the server runs the jobs below.
• Hosted ML. The enrichment job sends preview URLs to a hosted function (Modal, Replicate, or a GitHub Actions job) and gets numbers back. Cluster naming, free-text parsing and playlist reasons call the Claude API.
• Light enough for any box. SQL, vector search and the small scoring models (logistic regression, gradient boosting) train and run in seconds on an ordinary CPU.
• Existing set data. Set recordings stay on WebDAV. Set metadata CSVs can stay in GitHub until you want to join sets to the catalogue.
• Not Databricks. It's built for terabyte-scale data and bills compute by the hour.
One job pattern. Every job syncs everything since its last checkpoint. The first run has no checkpoint, so it pulls everything; that's the backfill. The same code then pulls only what's new. Jobs are idempotent — safe to re-run.
Job
Checkpoint
First run
Then
Runs on
Last.fm sync
Last scrobble timestamp
Full history
Every 15 min
Home server
Library sync
Hash of favourites and playlists
Everything
Daily
Home server
Enrichment
Tracks without a sound profile
Whole library (hours)
New tracks only
Home server → hosted inference
Release ingest
Last check date
Recent weeks
Weekly
Home server
Retrain contexts and scorers
—
Once data exists
Nightly
Home server (+ Claude API for naming)
Rebuild mixes
—
—
Daily / weekly
Home server
Calendar features
Next 24 h window
—
Every 15 min
Home server
Data ingest
Three sources feed one track table: Last.fm for behaviour, platform favourites for long-term taste, and your playlists for context labels.
Source
How
What it gives
Refresh
Last.fm scrobbles
user.getRecentTracks, full backfill then incremental
Play counts, recency, time-of-day and day-of-week patterns
Every 15 min
Tidal favourites
tidalapi favourites
Explicit "yes" signals, including taste from before scrobbling
Daily
YouTube Music likes
ytmusicapi get_liked_songs
Same as above
Daily
Your playlists
tidalapi / ytmusicapi
Playlist names as context labels; co-membership as similarity
Daily
Followed artists and labels
Tidal, plus a manual list
Seeds for new-release discovery
Weekly
Favourites carry no date. Dates added are ignored because platform migrations overwrite them. A favourite is a binary, undated positive. Its weight comes from how it combines with play history:
• Favourited and heavily scrobbled → core taste, strongest weight.
• Favourited, rarely or never scrobbled → dormant taste. Moderate weight; prime Rediscovery source.
• Heavily scrobbled, never favourited → implicit love. Treated as positive.
Cross-platform identity. Every track resolves to a MusicBrainz recording ID, with ISRC as the fallback key. Favourites from different platforms merge onto the same row, and duplicates from past migrations collapse.
Track enrichment
Each track gets a sound profile built from 30-second previews and metadata. No owned audio is needed.
Layer
Source
Fields
Identity
MusicBrainz
Recording ID, ISRC, label, release date, remixer credits
Tempo and key
Tidal / Beatport metadata, else preview analysis
BPM, key
Sound embedding
Preview (Deezer or iTunes Search API) → Essentia models
Embedding vector, mood, danceability, energy, voice/instrumental
Text-searchable sound
Preview → CLAP-style model
Vector that matches phrases like "hypnotic, rolling, no vocals"
Crowd descriptors
Last.fm track.getTopTags, artist.getTopTags
Weighted tags
Similarity graph
Last.fm track.getSimilar, playlist co-membership, shared label/remixer
Edges between tracks and artists
Older catalogue
AcousticBrainz data dump (frozen 2022)
Pre-computed high-level features
Process. Fetch a preview, compute features, store the numbers, discard the audio. Queue it as a background job with rate limits. Mark each field with its source so better data can replace weaker data later.
Gaps. Some tracks have no preview anywhere. They fall back to tags plus graph neighbours, and are flagged so you can see coverage.
Taste model and contexts
Taste is held as 20–30 named contexts rather than one average profile, so peak-time techno and Sunday mornings never blend.
1. Cluster. Cluster positively signalled tracks on embedding, tags and energy. Scrobble timestamps add when each cluster gets played.
2. Name. An LLM names each cluster once from its top tracks and tags, e.g. "Deep work", "Slow Sunday", "Pre-night-out". You can rename or merge any of them.
3. Seed from playlists. Where an existing playlist name clearly matches a cluster, it becomes that cluster's label.
4. Store a recipe per context:
    ◦ centroid and spread in embedding space
    ◦ energy and BPM range
    ◦ vocal tolerance
    ◦ default novelty share (e.g. 20%)
    ◦ typical times and days
5. Score candidates by distance to the context, track-level positive and negative signals, and a novelty bonus. Anything recommended recently is penalised.
Exploration. Each recipe keeps a novelty budget. It is filled from graph neighbours that sit outside the cluster, such as same label, remixer or playlist co-membership but a different sound. Accepts widen the cluster; skips mark its edges.
Context buttons and calendar signals
When you open the app, it shows the 10 contexts most likely to fit right now, ranked by a small model trained nightly on the home server and served by its API. It's a small classical model, so no ML hardware is needed.
Ranker inputs
• Time of day, day of week
• Location: Brighton or Saint-Victurnien
• Calendar state (below)
• What's played in the last hour, and its energy
• Weather, optional
Model. Start with logistic regression or a small gradient-boosted model, retrained nightly. Positive labels are the contexts you tap and play through; contexts that get skipped are negatives. Until there's enough data, it falls back to rules derived from scrobble time patterns.
Layout. Eight ranked contexts plus two fixed wildcards: "Somewhere new" (high novelty) and "Adjacent to recent" (neighbours of the last hour's plays). Under them sits one energy dial and one familiar ↔ new toggle.
Free text, optional. A hosted model (e.g. Claude Haiku via the API, pennies a month) turns phrases like "dubby, nothing vocal" into the same filters.
Calendar signals. A scheduled job reads Google Calendar (or CalDAV) and turns each event into coarse features with keyword rules. Only those features are stored; event titles are discarded.
Signal
Derived from
Effect
Busy / focus
Meeting or focus-block events
Instrumental only; optionally pause or turn down Sonos
Working hours vs. free
Event density, working-hours setting
Re-ranks buttons
Hosting
"dinner", "friends", "party" keywords
Hosting mix ranked first
Travel
Travel events, event location
Switches location context
Upcoming gig or festival
Event matched to an artist or lineup
Builds a lineup mix in the run-up
Playback
A button tap ends in sound by one of two routes. Sonos is direct; everything else plays through a playlist the app rewrites.
Target
Mechanism
Notes
Sonos
API call to the home server, which queues Tidal tracks via SoCo's ShareLinkPlugin
The home server controls Sonos with SoCo, so a tap from any device on the tailnet plays in that house. Elsewhere, Claude's Sonos connector or the Sonos cloud API
Phone, laptop
App rewrites a "Now" playlist in Tidal (tidalapi or the official Tidal API)
Open the playlist once; it's always current
FiiO DAPs
Same playlists marked for offline in the Tidal app
Syncs automatically
YouTube Music
ytmusicapi playlist edits
Unofficial API; expect occasional breakage
Claude
MCP server exposing search_by_vibe, play_context, whats_new
Ask in plain language; queues to Sonos
No public API drives playback inside the Tidal app itself, so the playlist route is the portable option.
Auto-refreshing mixes and feedback
A scheduled job rebuilds a fixed set of Tidal playlists. Each one has its own recipe and novelty share.
Mix
Contents
Refresh
Novelty
Fresh
New releases from followed artists, labels and remixers, filtered by taste fit
Weekly (Fri)
High
One per top context
e.g. Morning Work, Slow Sunday: stable core plus rotating additions
Daily
20%
Edges
Graph neighbours just outside your clusters
Weekly
Very high
Rediscovery
Favourited or once-heavy tracks not played in 6+ months
Weekly
None (all known)
Set prep
Candidates in a chosen BPM/key range not yet in the set library
On demand
High
Better than the apps' own mixes
• No repeats. A track suggested and ignored is held back for several weeks.
• Reasons shown. Each playlist description lists why tracks are there ("same label as X", "Edges: adjacent to Slow Sunday").
• The novelty share is a setting you control, not a hidden objective.
Feedback signals, no extra effort
• Play-through. Last.fm only scrobbles a track after half its length or 4 minutes. A queued track that never scrobbles counts as a skip.
• Favouriting on Tidal or YouTube Music during a mix is a strong positive.
• Button taps and dial positions train the context ranker.
• Explicit veto. A "never again" action in the app hard-excludes a track.
How the models use the data
No model reads the whole database. Each output runs the same steps: SQL and vector search narrow the catalogue to a few hundred tracks, a small scoring model ranks them, and rules shape the result. An LLM only names, parses and explains.
What you play flows back as plays and skips, which become the next night's training data.
Part
What it is
Runs on
Reads
Retrieval
pgvector nearest-neighbour search plus SQL filters
Home server (Postgres)
Embeddings, BPM/key, tags, graph edges, vetoes
Context ranker
Multi-class logistic regression: P(context | situation)
Home server CPU, retrained nightly
Situation features, past sessions
Track scorer
Logistic regression or LightGBM: P(played through)
Home server CPU, retrained nightly
One feature row per candidate track
LLM
Claude API
Hosted
Small summaries or tool results, never raw tables
Suggesting contexts (the 10 buttons)
1. Build a feature row for now: hour, weekday, location, calendar flags, last hour's energy and context, weather.
2. The context ranker scores all ~30 contexts and returns the top 8. The API adds the 2 wildcards.
3. Training data comes from history. Each past listening session in your scrobbles is matched to its nearest context, giving (situation, context) pairs. Button taps add cleaner labels over time.
Building a playlist
1. Load the context's recipe: centroid, energy and BPM range, novelty share.
2. Retrieve ~500 candidates: nearest neighbours to the centroid, plus graph neighbours for the novelty slots. Filter by energy/BPM; drop vetoes and anything suggested recently.
3. Build a feature row per candidate: distance to centroid, play count, favourited, days since last play, days since last suggested, novelty distance, tag overlap.
4. The track scorer predicts the chance you'll play each one through. Training labels: queued and then scrobbled = 1, queued and not scrobbled = 0.
5. Rules shape the list: fill the novelty quota, at most 2 tracks per artist, order by energy arc, cap the length.
6. Optionally Claude writes one-line reasons from the feature rows. Then queue to Sonos or write to Tidal, and log every suggestion.
Where Claude fits. Claude calls MCP tools like search_by_vibe and get_context, which run the same queries and return a few dozen rows. Jobs pass it compact summaries — a cluster's top tracks and tags to name it, a playlist's feature rows to explain it.
Catalogue views
Music Catalogue gets a set of views over everything feeding the model. They double as the way to check each ingest job is working.
View
Shows
Why it matters
Library explorer
Every track with source, favourited, context, BPM, key, tags; filterable
Find anything without remembering names
Taste map
2D projection of track embeddings, dots coloured by context, favourites highlighted
See your clusters, their edges, and where Edges mixes draw from
Listening heatmap
Plays by hour × weekday, filterable by context
Shows the time patterns the button ranker learns
History timeline
Plays per week, stacked by context
How your listening shifts over months
Favourites vs plays
The four groups: core, dormant, implicit love, rest
Checks the favourites weighting and feeds Rediscovery
Playlists
Each playlist's mapped context and overlap with others
Confirms playlist names are seeding the right contexts
Enrichment coverage
Share of tracks with a sound profile, BPM/key, tags
Shows where recommendations are flying blind
Recommendation log
What was suggested, the reason, and the outcome (played, skipped, favourited)
Debugs the model and tunes the novelty share
Build. Postgres views on the home server do the aggregation, exposed through the API; the renderer just draws. Observable Plot or ECharts covers most charts; the taste map needs a WebGL scatter (e.g. regl-scatterplot) for tens of thousands of points. The 2D projection is computed by the nightly job, not in the app.
Before building UI. Metabase on the home server lets you explore the data from phase 1 onward.
Build phases
Six phases, each usable on its own. Phase 3 is where it starts paying off day to day.
1. Unified catalogue. Last.fm backfill, Tidal and YouTube Music favourites and playlists, MusicBrainz/ISRC matching, de-duplication. Done when: Postgres holds every track with merged signals, and the library explorer and listening heatmap show it.
2. Enrichment. Preview pipeline, embeddings, BPM/key, tags, similarity graph. Done when: most of the active library has a sound profile, with gaps flagged.
3. Playback plus MCP. SoCo for Sonos, "Now" playlist writer, MCP server for Claude. Done when: "play something for cooking" works from Claude or one button.
4. Contexts and buttons. Clustering, LLM naming, context recipes, rule-based ranking, then the trained ranker. Done when: the 10-button screen picks well most of the time.
5. Mixes and discovery. Release ingest, the five mix types, the scheduler, the feedback loop. Done when: Fresh and Edges surface tracks you keep.
6. Calendar and context polish. Calendar features, location switching, weather, gig/lineup mixes. Done when: buttons reflect what's going on without input.
Where it stands (16 Oct 2026, evening)
The backend is Django on Postgres + pgvector (not FastAPI). Everything runs from `manage.py discover`, which isn't scheduled yet.
• Phase 1, done except YouTube Music likes. Last.fm history; Spotify (2012–2025) and Deezer exports, filtered by Last.fm's scrobble rule so every source means the same thing (138k plays, 36k tracks); Tidal, Spotify and Deezer favourites; your 64 Spotify playlists; MusicBrainz matching with duplicate merging.
• Phase 2, mostly done. Identity, Tidal IDs, labels, Discogs styles, Last.fm artist tags and similar artists, Essentia audio analysis (BPM with half/double-tempo correction, key, moods, styles, embedding) and CLAP text-audio embeddings. The backfill of the imported tracks is still running.
• Phase 3, partly done. The Tidal playlist writer works. Sonos and the MCP server not started.
• Phase 4, mostly done. Adaptive contexts: 14 families across the whole range, split into 37 contexts where you listen most (recent plays weighted, a year's half-life), siblings merged when they sound alike. The context ranker (hour, weekday, month, last two hours of plays, optional country) gets the right context in its top three 30% of the time against 13% for "always the most played". Requests (`ask`) with time travel ("summer 2019", "from 2014 I haven't played since"). A 24-case test set for request parsing (`eval_prompts`): keywords 23/24; Claude naming and parsing wait on an API key. No button screen yet.
• Phase 5, mostly done. Discovery queue: releases from 335 watched artists, remixers on tracks you like and the independent labels you play (majors excluded), each with a score, a reason and a state (new, heard, saved, dismissed). Fresh and context mixes with a 35% new-to-you share; candidates from Tidal's similar tracks. Sets from the YouTube playlist and watched channels with YouTube/MixesDB tracklists. Recommendation log matched against later scrobbles. Your wider history is kept in play without being blended into today's mixes: contexts are era-aware (each track's peak listening date is a clustering input, so a 2016–2018 period gets its own context, named with its years), two past-era or quiet contexts rotate into the mixes each week, and a Rediscovery mix brings back tracks with high peak plays you haven't touched for six months, spread across years. Beyond tracks: artists to try (close to several you play, by average link strength so hub artists don't dominate), artists to come back to, albums (new in the queue, saved but unplayed for a year, studio albums by artists you love you've never played) and sets ranked on tracklist overlap, DJ and context fit. Platform popularity is not used as a signal anywhere, to avoid drifting towards what everyone plays.
• Modelling: what you want, not what you put on next. Session intent: each session gets an intent score (0 passive, 1 chosen); the Spotify export's record of shuffle and self-picked tracks labels its sessions, and a model trained on patterns any scrobble history shows (length, artist spread, one-off tracks, album and artist continuity, sound similarity between consecutive tracks) scores the rest. Every play is weighted 0.3 + 0.7 × intent, so passive listening counts less without being discarded: liked tracks, contexts, artist affinity, Rediscovery peaks and session vectors all use the weights. Sessions: history split at 30-minute gaps (about 7,900); session track vectors (word2vec, deliberate sessions counted more); combined sound-and-session vectors, close only when both agree, used for flow and for the future map. Track scorer, target "durable affinity": coming back to a track deliberately in at least two separate months within a year of first hearing it, or a favourite, or a yes; a no overrides plays. Signals that exist going forward: sound and style fit, artist, label and remixer track records, artist-graph distance, session placement, DJ endorsement, audio features, release age, time. Popularity and skip data excluded on purpose; trained only on tracks enrichment has reached, to avoid leaking play counts. Test AUC 0.635 against 0.599 for the hand-weighted score, so the mixes use it. Context ranker: the right one of 54 contexts in its top three 35% of the time, against 5% for "always the most played".
• Editorial shaping. Mixes drop candidates that don't fit their intent, prefer your canon (tracks you've come back to across three or more years, favourites, yeses) in familiar slots, take up to four tracks per artist and three per album, avoid long runs of one style, apply your verdicts (never, no, not now, yes), and are sequenced for flow: an energy arc, with each next track chosen for combined similarity, a compatible key on the Camelot wheel and a nearby tempo.
• Phase 6 not started.
Added to the plan
1. Spotify and Deezer history imports (phase 1)
Your exports add listening from before Last.fm and from plays it never saw, plus each platform's favourites.
• Spotify extended streaming history (Streaming_History_Audio_*.json): timestamp, track, artist, album, Spotify URI, ms played, why it started and ended, skipped. YourLibrary.json gives saved tracks, albums and artists.
• Deezer personal-data export: listening history and favourite tracks, both with ISRCs, so they match exactly.
• Each listen records its source. A Last.fm scrobble of the same track within 10 minutes is the same play and is kept once.
• Favourites become Favourite rows for the "spotify" and "deezer" platforms. They feed taste weighting and Rediscovery like Tidal's.
• Skip data is deliberately not used: it isn't collected going forward. Plays are filtered by Last.fm's scrobble rule (half the track or four minutes) instead, so imported history means what a scrobble means.
• Raw exports go in server/imports/, kept out of git: they include IP addresses and devices.
2. Listening time travel (phase 4, requests)
Ask for music from a time in your own history: "what I was playing in summer 2019", "my 2016 indie phase", "what I had on repeat last January".
• The request is parsed into a date window: by Claude, or by a keyword fallback for years, months, seasons and "last …".
• Candidates are tracks played in that window, ranked by how distinctive they were then (plays in the window against plays overall), so it returns that period's sound and not your all-time favourites.
• It combines with the usual filters and sound descriptions ("upbeat stuff from summer 2019").
• "Then, not since" picks tracks heavy in that window and unplayed since, a time-boxed Rediscovery mix.
• The same query backs an MCP tool, so Claude can answer "what was I into when we moved to France?".
3. New-release tracking as a discovery queue (phase 5)
Turns release checks into a queue you can work through in a playlist or an app.
• Watch more than artists. Labels (from Tidal copyright lines and MusicBrainz) and remixers on tracks you like; artists Last.fm calls similar to several you play.
• Every new release becomes a queue item with a score, a reason and a state: new, heard, saved or dismissed. State changes come from scrobbles, Tidal favourites or a tap in the app.
• Fresh draws its tracks from the queue, best first, at most two per artist, never repeating dismissed items.
• Sets join the same queue: new uploads on watched YouTube channels, scored by how much of the tracklist you play.
• A weekly summary: counts per artist and label, and the five best fits with reasons.
Later: bigger ideas
Android companion app
A phone app to explore the catalogue and recommendations directly, built on an API the home server exposes over Tailscale.
• Browse the catalogue: tracks, artists, labels, contexts, sets and the discovery queue, with the sound facts and the reason for each recommendation.
• Share target. Share a track from Spotify, Tidal, YouTube, Deezer or Shazam into the app. It resolves the link to a catalogue track (or adds one by ISRC) and offers "more like this", "add to queue" and "which context is this?".
• Open in Tidal. Tracks, albums and the app's playlists open in the Tidal app through its links; an "Open mix" button for each context playlist.
• The map. A zoomable 2D map of the catalogue from the sound embeddings (UMAP, computed nightly). Contexts are regions, your favourites are bright, new releases and queue items glow at the edges. Tap a point to hear its 30-second preview and see why it sits there.
• Graph mode. From any track or artist, walk its neighbours (similar tracks, similar artists, shared labels and remixers) and pin a path into a playlist.
• Native Kotlin with Jetpack Compose, the map drawn on an OpenGL canvas (or a WebView running deck.gl if that proves faster to build); a small offline cache of the catalogue and map tiles.
• Needs first: a REST API (Django Ninja) with auth, and the nightly 2D projection, which the web taste map in Catalogue views also uses.
Feedback design (for the companion app and the API)
Plays say what you put on; feedback says what you want, so it outranks every inferred signal. The API and app should make giving it effortless and keep it specific.
• Per track, wherever it appears: Keep (yes), Skip (no), Not now (hold back a month), Never (keep out for good), More like this (a seed for a new mix). One tap, no forms.
• Per mix: a 1–5 rating and an optional line ("too safe", "great for the car"), so mix-level judgements train the shaping, not only track picks.
• Occasional comparisons: "which of these two fits this mix better?". Asked rarely, in passing; this is what teaches editorial taste, which yes/no can't.
• Context edits: rename, merge or split a context, and move a track to another context; named contexts are never overwritten.
• Every piece of feedback is stored with where it was given (mix, context, list) and when, as Feedback rows; the scorer treats yes and favourites as positives and no and never as negatives, overriding plays. The review page's verdicts already import through `manage.py import_feedback`.
Discovery interface
The same queue and map as a web page (or an Electron view) for the desktop: swipe-style keep/skip on new releases and sets, previews inline, verdicts written back as feedback. The review page built for the first mixes is a prototype of this.
Risks and open questions
Risk
Impact
Mitigation
Unofficial APIs break (tidalapi, ytmusicapi, SoCo share links)
Ingest or playback stops
Adapter layer per platform; official Tidal API where it covers the need
Preview API terms or availability change
Enrichment gaps
Store features, not audio; fall back to tags and graph; check each API's terms
Poor preview coverage for underground releases
Weak profiles for exactly the music you care about
Beatport/Tidal metadata, tags, graph neighbours; flag coverage
Cold start for the context ranker
Generic buttons at first
Rules from scrobble time patterns until there's enough tap data
Sonos control is local-only
The home server only reaches Sonos in its own house
Claude's Sonos connector or the Sonos cloud API in the other house
Open questions
[ ] Hosted inference for preview embeddings: Modal, Replicate or GitHub Actions?
[ ] Which calendar: Google or iCloud?
[ ] Does Beatport stay in scope for metadata and the Set prep mix?
[x] Default novelty share per mix? 35% for context mixes, settable per context.
[ ] Spotify and Deezer exports: which files you have (Spotify's extended history or the basic one?) and where to drop them.
[ ] Android app: native Kotlin, or a cross-platform toolkit to share code with a desktop app later?