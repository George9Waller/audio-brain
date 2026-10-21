# Companion app — Android plan

17 Oct 2026 · @George

The phone and DAP side of audio-brain. Its job is to get you what you want to hear in a tap or two, through the apps you already play from (Tidal, UAPP, Sonos), and to make feedback so easy that you give it while listening, even with no network.

## Goals and constraints

- **Feedback works offline.** Rating what's playing, notes on mixes and verdicts on queue items all work with no network and sync when the home server is reachable again. Nothing is lost and nothing is double-counted.
- **It works alongside your players and doesn't replace them.** Tidal and UAPP keep playing the audio. The app reads what they're playing, controls them through Android's media session interface, deep-links into them, and accepts tracks shared from them.
- **Runs on the FiiO JM21 and M23** (and anything from Android 10 up). `minSdk 29`. DAPs have mid-range GPUs, 4–6 GB of RAM, 720p screens and often no Google Play Services. So:
  - no dependency on Google Play Services (no Firebase or FCM, no Play-only libraries);
  - the app can be sideloaded (from GitHub releases, or via Obtainium for updates);
  - the map is drawn on demand, not every frame.
- **Native Kotlin with Jetpack Compose**, and a custom design system in its own module, so new screens are mostly put together from existing parts.
- **The server stays the brain.** The app keeps a cache and an outbox. Ranking, mixes and resolving tracks stay in Django. The one exception is a fallback context ranker that runs on the device when the server can't be reached (phase 6).
- **The server's existing rules still hold.** Skip data isn't collected; plays follow Last.fm's scrobble rule; feedback outranks any signal inferred from plays.

## The four loops

Everything in the app serves one of these.

| Loop | What you do | Offline? |
|---|---|---|
| **React** | A track is playing in Tidal or UAPP; you tap Keep, No, Not now, Never or More like this, from the app, the notification, the lock screen, a Quick Settings tile or the widget | Yes, fully |
| **Get** | Open the app, tap a context or type a request, and it starts playing in Tidal, UAPP or Sonos | Partly: cached mixes and offline Tidal playlists |
| **Explore** | The map, graph walks, the discovery queue, sets and artists to try | Map and cached queue, yes; previews, no |
| **Capture** | Share a track from Tidal, Spotify, YouTube, Shazam or Deezer to get More like this, Add to queue, Which context?, or a verdict | Captured offline, resolved on sync |

## Player integration

What's possible depends on what each player exposes. Three things are settled: Android's media session interface gives read access and transport control for every player; Tidal links open the Tidal app; and the server's "Now" playlist route already works. Everything else is checked on the device in phase 0 before anything depends on it.

### Reading what's playing (all players)

- A `NotificationListenerService` (the user grants notification access once) unlocks `MediaSessionManager.getActiveSessions()`. That gives a `MediaController` for whichever app is playing: Tidal, UAPP, FiiO Music, Poweramp, YouTube Music.
- From the controller the app gets the title, artist, album, duration, playback state and position. Some apps also expose a media ID (Tidal's track ID, if it's there) and artwork.
- The app follows the active session as it changes, so its "now playing" is always the right one, even when you switch from Tidal to UAPP.

### Control (all players)

- Play, pause, next, previous and seek through `MediaController.TransportControls`, so the app's mini player and widget can control any of them.
- If the app also has to start specific music, it uses one of the routes below.

### Starting specific music

| Route | Mechanism | Status |
|---|---|---|
| **"Now" playlist** (primary) | The server rewrites the Tidal playlist for the chosen mix, then the app deep-links into it in Tidal | The writer exists. You still press play in Tidal, but it's always the right playlist |
| **Tidal links** | `https://tidal.com/browse/{track,album,playlist,artist}/{id}`, which Tidal opens as an app link. Check the `tidal://` scheme in phase 0 | Track and album pages, the "Open mix" button |
| **Play from search** | `MEDIA_PLAY_FROM_SEARCH` intent aimed at the Tidal package (the same intent voice assistants use) | Phase 0 test. If it works, "play this exact track" is one tap |
| **Media browser** | Connect to Tidal's or UAPP's `MediaBrowserService` (the Android Auto interface) and call `playFromMediaId` | Phase 0 test. Probably limited to approved apps, so nothing depends on it |
| **UAPP** | UAPP plays Tidal itself. Read and control work through its media session. Phase 0 checks its manifest for Tidal intents or deep links | Read and control are certain; starting specific music is unknown |
| **Sonos** | API call → home server → SoCo queues the tracks directly | Needs the server's Sonos work (phase 3 of the main spec) |
| **Offline on the DAP** | Mix playlists are marked for offline in Tidal; the app deep-links to the cached playlist | Works now if the playlists are set to offline once |

A route picker on every mix ("Play on: Tidal · UAPP · Living room") remembers your last choice for each device. On the DAP it defaults to Tidal offline playlists when the server can't be reached.

### Listening log: not needed

Both DAPs (the JM21 and M23) scrobble to Last.fm themselves, and so does Tidal on the phone, so every play already reaches the server through the 15-minute Last.fm job. The app doesn't record plays. Offline plays reach Last.fm late, when the DAP reconnects, and the Last.fm job picks them up then. This leaves skip data uncollected, as the spec intends.

### Matching what's playing to the catalogue

Media session metadata is messy ("Track (2019 Remaster)", "Artist feat. X"). Matching works in this order:

1. Media ID → `PlatformTrack` (Tidal ID).
2. A local lookup in the cached index, by artist, title, album and duration ±3 s.
3. The server, through `track_for` and ISRC lookup.

Feedback on a track that hasn't been matched is still stored, with the raw metadata, and resolved on sync. It's never dropped.

## Offline design

- **Room** for the local database, **WorkManager** for sync. Sync runs when there's a network and the tailnet host answers a health check. It also runs right away when the app sees the server again.
- **The outbox.** Every action (feedback, a mix rating, a note, a comparison answer, a play, a share, a queue state change) is written first as a local row with a client UUID. The server takes them in batches and ignores UUIDs it has already seen, so retries are safe. The UI updates from the local row, so taps never wait on the network.
- **What's cached**, all refreshed on each sync from a `since` cursor:
  - contexts and their colours;
  - today's ranked buttons;
  - current mixes (tracks, Tidal IDs, reasons);
  - the discovery queue snapshot;
  - a track index (about 36k rows of ID, artist, title, Tidal ID, ISRC, duration; around 5 MB);
  - map points (binary, under 1 MB);
  - artwork, in Coil's disk cache.
- **What needs the server:** requests (`ask`), new mixes, Sonos, previews, resolving share links, and refreshing the ranking. When it's unreachable, the app says so once, quietly, and offers the cached version.
- **Sync status is always visible** as a small dot on the now-playing dock: clear, pending, or offline. Tapping it shows the outbox.

## Feedback design

This follows the spec's feedback design. The aim is for feedback to take one tap from anywhere.

**The verbs:**

- **Keep** (`yes`)
- **No** (`no`)
- **Not now** (`not_now`)
- **Never** (`never`)
- **More like this**: starts a seed mix and also counts as a mild positive.

The labels are the spec's, except "Skip" becomes **No**, so it isn't confused with skipping a track.

**Where you can give it:**

| Surface | Detail |
|---|---|
| **Now-playing dock** | A persistent strip at the bottom of every screen showing the track, its sound sigil and the verdict buttons. Swipe up opens the full now-playing view |
| **Feedback notification** | While a player is active, a low-priority notification with Keep, No and Never actions. Works from the lock screen with the screen off, and offline |
| **Quick Settings tile** | "Keep this": one tap from anywhere |
| **Home screen widget** (Glance) | Now playing, the verdict buttons and the three top contexts |
| **Track rows everywhere** | Swipe right for Keep, left for No; long-press for the full set |
| **Share sheet** | Share from Tidal, then pick a verdict straight from the share preview |

**Rating a mix:** 1–5 stars plus quick chips ("too safe", "great for the car", "too vocal", "wrong energy") and an optional line of text. The prompt appears after a mix has played for a while, or when you leave it.

**Comparisons:** "Which fits *Slow Sunday* better?" with two cards. Shown at most once a day, when you're on the Now screen without playing anything. You can dismiss it.

**Context edits:** rename, merge or split a context, and move a track to another context. These are sent as edits; the server already respects `named_by_user`.

Every piece of feedback records where it was given (`where`: mix key, screen, notification), the context, and the time it was given on the device, not when it synced.

## Screens and navigation

Three spaces and a dock. Everything else opens as a sheet over the space you're in, so you never lose your place.

```
┌───────────────────────────────┐
│  Now  ·  Discover  ·  Map     │  ← top switcher; horizontal swipe between spaces
│                               │
│        (space content)        │
│                               │
├───────────────────────────────┤
│ ◉ sigil  Track — Artist   ✓ ✕ │  ← now-playing dock (all spaces), sync dot
└───────────────────────────────┘
```

### Now: get what you want

- **Ask bar** at the top: "dubby, nothing vocal", "summer 2019". Recent requests appear as chips.
- **Ten context tiles**: 8 ranked plus the 2 wildcards ("Somewhere new", "Adjacent to recent"). Each tile shows its context colour, name, a small energy bar and the era it covers, if any. They reorder with animation when the ranking refreshes.
- **Energy dial and familiar ↔ new slider** under the tiles. Changing them affects the next mix you open and shows a live hint ("→ 40% new").
- **Shelves**: Fresh, Rediscovery, Edges, sets for you, artists to try. Each is a horizontal row.
- **Tapping a tile** grows it into the mix sheet (a shared-element transition): tracks with reasons, the route picker, Play, and rating.

### Discover: the queue as a card deck

- A full-bleed card deck of new releases and sets. Each card shows the artwork, a 30-second preview that starts on its own when online, the reason, the score, and where it would fit on the map.
- Swipe right to Keep, left to dismiss, up for Not now. Tap to open the release.
- Filters: releases, sets, artists to try, or albums to come back to.
- Offline, the deck uses the cached queue without previews; the verdicts are queued.

### Map: taste as a place

This is the centrepiece. The nightly job projects `FusedVector` to 2D with UMAP. The phone draws the points with OpenGL ES 3.0 (AGSL needs Android 13, so it's ruled out on the DAPs).

- **Contexts are regions**: soft hulls tinted in each context's colour and named, with labels that scale as you zoom.
- **Point styles**: favourites and canon are bright; dormant tracks are dim; queue items and new releases glow at the edges.
- **"You are here."** The track playing now pulses on the map. Turn on Follow, and the map follows the session around your taste as it plays.
- **Time scrub.** Drag a year slider, and points fade in and out by each track's peak listening date. Your eras play out as a moving map: the spec's time travel, made visual. Releasing the slider on a year offers "play this era".
- **Lasso to mix.** Draw around a region and it becomes a mix, sequenced by the server's flow rules.
- **Journeys.** Pick two points, and the server builds a path between them through the graph, with an energy arc from A to B. Pin it as a playlist.
- **Tap a point**: the track sheet rises with the sigil, the sound facts, the reason it sits there, and its neighbours. Online, the preview plays.
- **Graph mode**: from any track or artist, the map dims and the neighbours light up (similar tracks, similar artists, labels, remixers). Tap a neighbour to walk to it. The path you've walked can be saved as a playlist.

### Sheets

- **Track**: verdicts, open in Tidal, add to queue, which context, neighbours, history (when you played it most).
- **Artist and label**: your history with them, what's in the queue, similar artists.
- **Mix**: tracks, reasons, route picker, rating.
- **Outbox**: what's waiting to sync.
- **Settings**: server, players, novelty defaults, the design system catalogue (debug builds).

## Design system

The design system lives in a `:core:design` module. It's built on Compose Foundation, not Material 3's components: Material 3 is used only for its non-visual helpers, so the look is entirely ours and nothing in it reads as stock Material.

**Tokens**, through `CompositionLocal`s on an `AbTheme`:

- **Colour.** Dark-first neutrals, near-black but not pure black, which suits the LCD screens on DAPs. One accent colour. **Context colours** are computed rather than chosen: each context's hue comes from its centroid's position on the map, so a context has the same colour on its tile, its map region, its mix sheet and the widget, and contexts that sound alike have similar colours. The server sends these in the context payload.
- **Type.** Two bundled variable fonts: a grotesque for the interface (e.g. Inter Tight or Space Grotesk) and a monospaced face for numbers (BPM, key, Camelot, dates), with tabular figures.
- **Space and shape.** A 4 dp grid, four corner radii, and a minimum touch target of 48 dp. DAP screens are small and often used while walking.
- **Motion.** Named springs (`snappy`, `smooth`, `gentle`) and durations. Every transition uses these tokens, so motion feels the same everywhere. Reduced-motion mode is respected.
- **Elevation.** Tonal layers instead of shadows, which are cheaper to draw on the DAP GPUs.

**Components** (each with Compose previews and a screenshot test):

| Foundations | Music | Feedback | Layout |
|---|---|---|---|
| `AbSurface`, `AbButton` (primary, quiet, icon), `AbChip`, `AbToggle`, `AbSlider`, `AbTextField`, `AbBadge` | `SoundSigil`, `TrackRow`, `ArtworkTile`, `ContextTile`, `EnergyBar`, `CamelotBadge`, `ReasonLine`, `PreviewButton` | `VerdictBar`, `SwipeVerdictRow`, `RatingStars`, `QuickChips`, `CompareCards`, `SyncDot` | `AbSheet` (with detents), `SpaceSwitcher`, `NowPlayingDock`, `CardDeck`, `Shelf`, `EmptyState`, `OfflineBanner` |

**Sound sigil.** Each track gets a small generated glyph built from its features:

- a radial shape whose lobes are its moods;
- a ring weight for energy;
- a tick count for BPM;
- a hue for its context.

It's deterministic, so you learn to recognise a track's sound before you read its name. It's used in rows, the dock, the widget and the map's tooltips, and drawn once and cached as a bitmap.

**Catalogue.** Debug builds include a design system browser with every component, every state, and light and dark. Screenshot tests (Roborazzi) catch visual regressions.

**Transitions:**

- context tile → mix sheet: shared element;
- map point → track sheet: container transform;
- deck swipe: the card flies off with a tint in the verdict's colour;
- ranking refresh: tiles move to their new places with animation;
- dock → full now-playing view: the sigil and artwork move across.

All of this uses `SharedTransitionLayout` and `AnimatedContent`, which work on API 29. Predictive back works where supported and falls back cleanly on Android 10.

## Architecture

```
android/
  app/                    single activity, navigation, DI wiring
  core/design/            tokens, theme, components, sigil renderer
  core/model/             shared Kotlin types (Track, Context, Mix, Verdict…)
  core/data/              Room, API client, outbox, sync worker, repositories
  core/player/            notification listener, session tracker, play log, route launchers, deep links
  core/map/               GL renderer, point buffers, quadtree hit-test, camera
  feature/now/            Now space, ask, mix sheet
  feature/discover/       card deck, previews
  feature/map/            Map space, lasso, journeys, graph mode, time scrub
  feature/capture/        share target, link handling
  feature/feedback/       notification actions, QS tile, Glance widget, compare, ratings
```

**Stack:**

- Kotlin 2.x, Compose (BOM), Navigation Compose with typed routes.
- Hilt for dependency injection.
- Ktor client with kotlinx.serialization.
- Room, WorkManager, DataStore.
- Coil 3 for images.
- Media3 `MediaController`. It connects to other apps' sessions through their compat tokens, with the framework `MediaController` as a fallback.
- ExoPlayer for the 30-second previews only.
- Glance for the widget.

**Performance on the DAP:**

- Baseline profiles and R8.
- `@Immutable` models and stable lambdas, checked with the Compose compiler reports.
- Lists use `key` and `contentType`.
- The map renders only while it's moving and redraws the static points as a cached layer.
- Points come as one packed binary buffer: float16 x/y, context index and flag bits per track.
- Hit-testing uses a quadtree on the CPU.
- Labels are Compose text over the GL surface, culled by zoom.
- Target: 60 fps scrolling and under 1 s cold start on the FiiO, measured with Macrobenchmark.

**Network.** The app talks to the server over Tailscale, using the Android Tailscale app (the F-Droid build works without Google services). There's one bearer token, entered or scanned as a QR code once. The server's address is a setting, defaulting to its MagicDNS name.

## Server work required

These are things the app needs from the backend. They live in `server/`; most are small.

1. **REST API** (Django Ninja) under `/api/v1`, with token auth. Tailscale is the network boundary, and the token is a second lock. Endpoints:
   - `GET /now`: ranked contexts plus the wildcards, for the current situation. It also takes situation hints from the device: location, and "on the DAP".
   - `GET /contexts`, `PATCH /contexts/{id}` (rename, merge, split, novelty share).
   - `GET /mixes/{key}`, `POST /mixes` (from a context, a request, a seed track, a lasso of track IDs, or a journey A → B) → the tracks with reasons, written to a Tidal playlist on request.
   - `POST /play` → a route (Tidal "Now" playlist, Sonos room) → returns a deep link or the Sonos state.
   - `GET /queue`, `PATCH /queue/{id}` (state changes).
   - `GET /tracks/{id}`, `GET /tracks/{id}/neighbours`, `GET /artists/{id}`.
   - `POST /resolve`: a share URL or session metadata → a catalogue track, adding one by ISRC if it's missing.
   - `POST /sync`: one batch endpoint for outbox items (feedback, ratings, comparisons, plays, context edits, queue states), deduplicated by client UUID.
   - `GET /sync?since=`: changed contexts, mixes, queue and track index rows since the cursor.
   - `GET /map/points.bin`, `GET /map/regions` (context hulls, label positions, colours).
2. **Model changes:**
   - `Feedback.client_id` (UUID, unique) and `Feedback.observed` (JSON of the raw metadata, for tracks not yet matched; `track` becomes nullable until it's resolved). Source `"app"`.
   - New `MixRating` (playlist or mix key, stars, chips, note, given_at, client_id).
   - New `Comparison` (context, track A, track B, choice, given_at, client_id).
   - The Listen model accepts `source="companion"`; the existing 10-minute merge applies.
3. **Nightly map job:** UMAP over `FusedVector` (adds `umap-learn`). It aligns each night's layout to the previous one (Procrustes) so the map doesn't jump around, and computes context hulls and colours.
4. **Link resolver:** parses Tidal, Spotify, Deezer, YouTube Music and Shazam URLs; gets the ISRC or title and artist from the platform; then matches through `track_for` and ISRC.
5. **Journeys and lassos:** `mixes.py` gets two new seeds, "these track IDs" and "path from A to B". The path is a shortest path over the fused-vector neighbour graph, weighted to produce an energy arc. Both then go through the existing editorial shaping and flow sequencing.
6. **Ranker export (phase 6):** the context ranker's coefficients as JSON, so the device can rank contexts offline from the hour, weekday and its own recent plays.

## Jobs on the server

The app can see and run the server's scheduled jobs from a **Jobs** space next to Now. The server side lives in `server/jobs`:
- a registry of jobs, each with ordered steps and a schedule (Last.fm every 15 min; favourites, sets, enrichment, models and mixes daily; releases on Fridays; Everything only when asked for);
- a worker (`manage.py run_jobs`) that runs them in two lanes, so a long enrichment never holds up the Last.fm sync;
- `JobRun` rows holding each run's steps, the tail of its log and its first failure.

In the app, each job shows its schedule, when it's next due, and how its last run went. Running jobs show live step progress. Tapping a job opens its steps, its log and any failure, and lets you run it now or cancel a queued run. A banner warns when the worker isn't running. Jobs work only while the server is reachable; they don't queue offline.

## Build phases

Each phase gives you something usable on its own. Phase 2 is the one you asked for most. The catalogue map and mixes (phases 3–5) are planned in detail in [CATALOGUE.md](CATALOGUE.md), which replaces their outline here.

**Progress (17 Oct 2026).** Phase 1 is done, and most of phase 2 too:
- the API with token auth;
- the job runner and the Jobs space;
- the design system module with its catalogue screen;
- the Room outbox and sync worker;
- the session tracker and server matching;
- Now with the context tiles, the dock, and the feedback notification;
- the device check screen.

Tested on an emulator against the real server: Keep and No reached `Feedback` once each, including No given in airplane mode and sent after reconnecting. Phase 0 still needs the JM21 and M23 themselves. Not built yet in phase 2: the Quick Settings tile, the full-screen now-playing view, and a swipe on rows.

0. **Device checks (2–3 days).** On the FiiO:
   - the notification listener reads Tidal's and UAPP's sessions (what metadata, whether there's a media ID);
   - dump both manifests (`adb shell dumpsys package com.aspiro.tidal`, and the same for UAPP's package) for intent filters and URI schemes;
   - try play-from-search and media browser connection on both;
   - check Tailscale without Google services;
   - draw 40k points with GL and measure the frame rate.

   **Done when:** the routes table above is confirmed and marked supported or unsupported for each player.
1. **Foundations.** The API skeleton and token auth on the server; the Android project and modules; design tokens and the first components, with the catalogue screen; the Room schema and the outbox/sync worker against `/sync`. **Done when:** a Keep tapped in a test screen on the DAP in flight mode reaches the `Feedback` table once, after reconnecting, even when tapped twice.
2. **React: now playing and offline feedback.** The session tracker, matching, the now-playing dock and full view, the feedback notification, the Quick Settings tile, the outbox sheet and the sync dot. **Done when:** a day of offline DAP listening produces verdicts that land correctly on the server, and none of them needed the app to be open.
3. **Get: contexts, mixes and playback.** The Now space (tiles, dial, ask), the mix sheet, the route picker, the Tidal "Now" playlist and deep links, Sonos (once the server has it), and the share target with `/resolve`. **Done when:** from the DAP, a tap on a tile has music playing in Tidal within two taps, and sharing a Tidal track into the app opens More like this.
4. **Discover.** The card deck, previews, queue states, mix ratings and comparisons. **Done when:** you'd rather work through Fresh in the app than in the review page.
5. **The map.** The nightly projection, the GL renderer, regions, "you are here", the track sheet, then the time scrub, lasso, journeys and graph mode. **Done when:** you open the map to find music, not only to look at it.
6. **Polish and independence.**
   - the Glance widget;
   - the on-device ranker for offline buttons;
   - baseline profiles and Macrobenchmark targets met;
   - a "steer" control on a playing mix (more energy, fewer vocals, stranger), which rewrites the rest of the queue on Sonos, and the Now playlist on Tidal for the next time it's opened;
   - context edits.

   **Done when:** the app is the first thing you reach for on the DAP and the phone.

## Risks and open questions

| Risk | Impact | Mitigation |
|---|---|---|
| Tidal has no public way to play a specific track from another app | One extra tap (press play in Tidal) | The "Now" playlist route; phase 0 tests play-from-search and the media browser; Sonos is direct |
| UAPP doesn't accept deep links to Tidal items | Starting specific music only works through Tidal itself | Read, control and feedback still work in UAPP; UAPP can play the Tidal playlists we write |
| Media metadata doesn't match the catalogue cleanly | Feedback lands on the wrong track or none | Media ID first, then fuzzy matching with a duration check, then resolving on the server; unmatched feedback is kept and can be matched by hand in the outbox |
| No Google services on some DAPs | Push and some libraries unavailable | No Google services anywhere; sync is pull-based through WorkManager |
| Notification access feels intrusive | Permission declined | Explained in onboarding; the app still works without it, but React needs it |
| The map is slow on the DAP GPU | Janky Explore | Measured in phase 0; draw on demand, cached layers, level-of-detail thinning at low zoom |
| The map jumps between nightly layouts | Disorienting | Each night's projection is aligned to the previous one |

**Open questions:**

- [x] Which DAPs? The FiiO JM21 and M23. Both scrobble to Last.fm, so the listening log is dropped.
- [ ] Do they have Google Play services, and what's UAPP's exact package name? The device check reports both.
- [ ] Settles the spec's open question on the Android app: native Kotlin. A desktop view stays on the web or Electron, sharing the API rather than the code.
