# audio-brain for Claude: a remote MCP server

The plan for letting Claude reason over the catalogue and listening history and act on it, from claude.ai on the web,
desktop and phone. Written 22 Oct 2026 so any assistant or person can pick it up: read this, then [NEXT.md](NEXT.md)
and [deploy/KUBERNETES.md](deploy/KUBERNETES.md). It expands BACKLOG item **E1** and replaces its tool list.

Progress is tracked with the checkboxes. Tick them as you go.

## Goals

- **Remote.** Works as a claude.ai custom connector, so it's usable from the Claude app on the phone, not only Claude Code.
- **Visual.** Tools answer with interactive components (MCP Apps), not walls of text.
- **Reasoning.** Low-level stats (plays by anything, over any period) and meta stats (trends, eras, taste drift,
  discovery funnel), plus read-only SQL as the escape hatch.
- **Actions.** Make mixes, **refine drafts** step by step, keep and play them, give verdicts, work the discovery queue.
- **Playback.** "Play something for cooking" ends with music on a Sonos, through Claude's Sonos connector.

## Shape

```
phone / web / desktop Claude
        │  HTTPS + OAuth 2.1
        ▼
Tailscale Funnel (only this port is public) ──► claude-mcp pod (Python, ~80 MB)
                                                  │  MCP_TOKEN, cluster DNS
                                                  ▼
                                               audio-brain API ──► Postgres
Claude also calls the Sonos connector ──► play_sonos_favorite("Audio Brain · Now")
```

- **`claude_mcp/`** at the repo root: its own `pyproject.toml` and image. Python, using the `mcp` SDK (FastMCP,
  streamable HTTP, its auth helpers) and `httpx`. It never imports Django or torch: every tool calls the API, so the
  API pod's memory is unchanged on the 8 GB node, and Claude's actions take the same write path as the app's (drafts,
  `DraftOutcome`, the `Recommendation` log, feedback).
- **Don't name the package `mcp/`.** It would shadow the `mcp` library.
- **UI bundle** in `claude_mcp/ui/`: Vite + Preact + `@modelcontextprotocol/ext-apps`, built to single HTML files
  (`vite-plugin-singlefile`) that the server serves as `ui://` resources. Built in the image's first stage.

### Hosting

- New `helm/templates/mcp.yaml`: a Deployment (`replicas: 1`, request 64 Mi, limit 256 Mi) and a NodePort Service
  (30801). It reaches the API at its cluster Service name.
- A small RWO PVC for the OAuth SQLite file.
- Expose it with Funnel on the Lenovo: `tailscale funnel --bg --https=443 http://localhost:30801`. Only this port is
  public; the API stays on the tailnet at :30800. Not Traefik (broken, and not needed).
- Secrets in AWS Secrets Manager key `audio-brain`: `MCP_TOKEN`, `MCP_PASSPHRASE_HASH`, `MCP_TOTP_SECRET`,
  `MCP_SIGNING_KEY` and `MCP_PUBLIC_URL` (the Funnel URL, kept out of this public repo). The pod gets them through
  its own ExternalSecret (`audio-brain-mcp-secrets`) listing just those five, so the one public pod never holds the
  TIDAL, Last.fm or database secrets. The API picks up `MCP_TOKEN` through the existing one.
- CI: `.github/workflows/deploy.yaml` builds each image only when its code changed, and tests `claude_mcp` first. The
  MCP image goes in the same ECR repository as `audio-brain:mcp-<sha>` (no new repository or IAM change), and
  `env.MCP_IMAGE_TAG` in `helm/values.yaml` points at it.

### Auth

- **Claude → MCP pod:** a single-user OAuth 2.1 authorization server inside the pod.
  - Protected Resource Metadata (RFC 9728) on the MCP endpoint, Authorization Server Metadata, Dynamic Client
    Registration (RFC 7591), and Client ID Metadata Documents if the SDK supports them. PKCE required.
  - Redirect URIs allow-listed to Claude's OAuth callback; registration refuses anything else.
  - The authorize page asks for a passphrase and a TOTP code, rate-limited per IP.
  - Access tokens 1 hour, refresh tokens 30 days, in SQLite on the PVC. Wiping the file revokes everything.
- **MCP pod → API:** a second bearer token, `MCP_TOKEN`. `api/auth.TokenAuth` returns the principal `"claude"` for
  it (and `"app"` for `API_TOKEN`), so it can be revoked on its own and everything Claude does is attributed.

### Attribution, and keeping the models honest

- Feedback Claude records is stored with `where="claude"`; recommendations it causes with `source="claude"`.
  `recommend/labels.py` can weight or drop them, and backtests can leave them out.
- Claude records a verdict only when you state one. Never infer a no from something not being played. The tool
  descriptions say so.
- Tools that destroy or retrain (`delete_list`, verdict `never`, `merge_context`, `split_context`, `run_job`) carry
  `destructiveHint`, so clients ask first.
- Whatever the tools return goes to Anthropic as conversation context. That is a deliberate exception to the spec's
  "only ML inference leaves the box".

## Playback through Sonos

Claude's Sonos connector can't ask for a TIDAL playlist by name, but it can play any **Sonos favourite**, TIDAL ones
included (the household already has TIDAL favourites). audio-brain rewrites the "Now" playlist in place, so its ID
never changes: add it to Sonos favourites once and `play_sonos_favorite` plays whatever was written last.

- The flow: `play(key)` writes Now and returns its TIDAL URL; Claude then calls the Sonos connector's
  `play_sonos_favorite` with the room you name. The draft editor's Play button sends Claude a message to do this
  (an MCP App can't call another connector's tools).
- Only the S2 household (Living Room, Dining Room, Bedroom, Living Room Lamp) works with the connector. The other
  household runs S1, which it doesn't support.
- **Tested 23 Oct: Sonos fetches the playlist at play time.** The favourite is "Audio Brain · Now". Played in the
  Bedroom it started the list then in Now (Zero 7 – Salt Water Sound); after the app wrote a different list to Now,
  playing the favourite again started that one (Ólafur Arnalds – saman). Nothing is cached, so this flow works as is.
- If one-tap play without Claude is wanted (the Android app), audio-brain calls the Sonos Control API
  itself (`loadFavorite` / playlist loading). That is the remote half of BACKLOG E2.

## Visual components (MCP Apps)

### Style brief

Agreed over Phase 0, for every component in every phase:
- **Simple and descriptive:** lots of information without overload. How important a piece of information is decides
  how much attention it gets, so it stays simple and elegant and still gives everything that matters.
- **Claude gets the detail, you get the calm summary.** The text and `structuredContent` carry everything Claude
  needs to reason and act; what's drawn is the summary.
- **Booking.com's connector is the gold standard.**
- **Echo the app.** Not a visual clone: it can lean on Claude's own MCP rendering, but it should click as audio-brain
  (a look of its own, unlike the app, didn't).
- **A compact inline card that expands to full screen** (option C). Inline: the name, a short summary of the
  artists (not "Starts with…") and the actions, **on one line**: **Open in Tidal** (primary), then **Keep it**, then
  **Play on Sonos**. Expanded: context colours, no BPM or key, and each track's sigil where a track number would be.

Each tool that has a component sets `_meta.ui.resourceUri`. Buttons in a component call server tools through the host
bridge (`callServerTool`), so refining a draft is taps or words, whichever is easier.

| Component | Shows | Used by |
|---|---|---|
| Draft editor | Rows with the track's sigil, artist – title, a "new" badge and the reason it was picked. Lock, remove and swap per row; steer chips (more energy, fewer vocals, more new, darker); Keep, Play, Open in TIDAL, Undo | `make_mix`, `refine_mix`, `show_list` |
| Charts | Ranked bars, time series with sparklines, hour × weekday heatmap, stream graph | `stats`, `trends`, `rhythms`, `discovery_funnel` |
| Timeline | Eras, milestones and play volume on one axis | `eras`, `time_travel`, `digest` |
| Track card / artist card | The story: first heard, plays per month, contexts, verdicts, lists it's in | `track_story`, `artist` |
| Discovery cards | Releases and sets with score and reason; save or dismiss | `whats_new`, `beyond` |
| Period comparison | Two periods side by side: artists, styles, sound, modes | `compare_periods` |

- Album art comes from TIDAL's image CDN: declare it in the resource's CSP (`resourceDomains`).
- Colours follow the app: context hues from `recommend/lists.hue`, light and dark from the host's theme.
- **Fallback.** Every tool also returns `structuredContent` and a short markdown summary (a top-10 table, not a dump).
  Where components don't render, Claude still has the data and can make an artifact from it.
- **Tested 23 Oct: components render in the Claude desktop app and the phone app,** and tapping through them
  works (the list component opening a list through `callServerTool`). They look rough, so styling needs a pass
  before Phase 1's components are built on them.

## Server work in the API

### History and stats endpoints

| Endpoint | Purpose |
|---|---|
| `GET /history/stats?measure&group&from&to&granularity&filters` | Plays or intent-weighted plays (0.3 + 0.7 × intent) grouped by artist, track, album, label, style, context, country, source, hour or weekday, optionally per week or month. Reads the rollup. |
| `GET /history/listens?from&to&artist&track&limit` | Raw plays with source, country, session and intent |
| `GET /history/sessions?from&to` | Sessions with size, intent, top artists and dominant context |
| `GET /tracks/{id}/story` | First heard, plays per month, last played, verdicts, contexts, lists, recommendation outcomes |
| `GET /artists/{id}/story` | The same for an artist, plus labels, styles and similar artists |
| `GET /recommendations?source&from` | What was suggested, and whether it was played or came back |
| `GET /insights/{kind}?from&to` | The precomputed meta stats below |
| `GET/POST/DELETE /milestones` | Life events: a date or range and a label |
| `POST /query` | Read-only SQL (below) |

### Read-only SQL

- A Postgres role `audio_brain_ro` with `SELECT` on every table except `library_oauthtoken` (and Django's `auth_*`
  and `django_*`). It's created, and its grants refreshed, after every `migrate` (a `post_migrate` handler in
  `api/apps.py`), so tables added later are readable without a migration of their own. Password:
  `POSTGRES_RO_PASSWORD` in the AWS secret. It's a separate login rather than `SET ROLE`, which SQL can undo.
- Each query runs on its own connection as that role, as one prepared statement (Postgres refuses a second statement,
  so a query can't `SET` its way out), in a `READ ONLY` transaction with `statement_timeout = 10s` and a watchdog
  that cancels it at 12 s whatever it set, at most 500 rows. Vector columns are
  shown as `<vector 512>`, but pgvector operators (`<=>`) work in queries.
- A schema guide served as an MCP resource (`schema://audio-brain`), generated from the models' help texts plus a
  hand-written page on what things mean: intent and the 0.3 + 0.7 × intent weighting, the Last.fm dedup rule
  (same track within 10 minutes is one play), sources, favourites per platform, the four verdicts, contexts and
  families, durable affinity. How well Claude reasons depends mostly on this page.

### Rollup and insights jobs (worker, nightly)

- **`history_rollup`:** plays and intent-weighted plays per week and month × each dimension above; monthly sound
  averages (BPM, energy, danceability, vocal share, moods) from `AudioFeatures`. One table (`history.Rollup`), with
  sound stored as intent-weighted sums, so averages over any span still add up.
  - **Built 24 Oct:** rebuilt in full each night, after sessions, rather than from a checkpoint. Rescoring sessions
    changes every play's weight, and a full rebuild takes about 6 s. It holds every week and month that had ended
    when it ran (track and album by month only, since by week they were most of the table), about 430k rows and
    45 MB, truncated and refilled in one transaction. `history.rollup.aggregate` reads whole periods from it and counts the edges and anything since live, so
    answers are always current. Anything filtered is counted live (30–350 ms over 138k plays).
- **`insights`:** writes JSON facts to an `Insight` table (kind, period, computed_at, data):
  - **Trends:** rising and falling artists, styles, labels and contexts, the last 90 days against the baseline, with
    minimum support. Builds on `recommend/explore.new_interests`.
  - **Eras:** change points in the monthly style and context mix split history into phases, each with top artists,
    styles and sound. Reuses the contexts' era detection; one LLM call names each new era.
  - **Taste drift:** the monthly intent-weighted centroid of `TasteVector`, distance from the month before and from
    the all-time centre, giving an exploring-versus-settling index.
  - **Diversity:** artist and style entropy, and the share of plays going to the top 10.
  - **Discovery funnel:** the share of plays that are first listens; how many new tracks become durable affinity;
    which sources (labels, remixers, TIDAL similar, sets, Claude's lists) produce the keepers.
  - **Lifecycle:** canon across years, the typical fade of a new track, dormant favourites, longest artist streaks.
  - **Rhythms and modes:** hour × weekday by context, summer against winter sound, countries, the
    deliberate-versus-passive trend, session length.

  Claude does the interpreting in chat. The server only computes facts, so no narrative LLM calls run nightly.

### Making and refining drafts

- `/make` also takes `request: MusicRequest` (Claude fills it, skipping the server's parse) and a recipe
  `{"type": "tracks", "track_ids": [...]}` (a list Claude chose, from SQL or conversation). The server still applies
  verdicts and sequences it for flow.
- **`POST /drafts/{key}/refine`** takes a list of operations and saves a new version each call:
  - `remove`, `add` (at a position), `move`, `lock` / `unlock`;
  - `swap(track, n)`: alternatives near that track, not already in the draft and not against a verdict (from
    `recommend()` seeds and `actions.suggestions`);
  - `steer`: a change to the `MusicRequest` (energy, BPM, vocals, styles to drop, exploration) or free text;
    rebuilds only the unlocked slots;
  - `length` (trim or extend), `resequence` (energy arc, or keep order), `undo`.
- Versions are kept on the draft (`Mix.versions`, JSON, last 20) until it's kept, played or discarded.
- Refining feeds the labels: removals already count as swiped-out tracks; tracks added, or kept after a swap, count as
  weak positives; all tagged `where="claude"`.

## Tools

About 30, grouped so the descriptions stay short. Every list-like tool defaults to 25–50 rows. Tracks are shown as
`Artist – Title (#id)`.

**Reasoning (read-only)**
- `sql`, and the `schema` resource
- `stats`, `listens`, `sessions`, `track_story`, `artist`
- `trends`, `eras`, `taste_drift`, `rhythms`, `discovery_funnel`, `compare_periods`, `digest(period)`
- `search` (words), `search_by_vibe` (sound), `time_travel(period, mode)`
- `now` (the context ranker's picks), `contexts`, `lists`, `explain_pick(list_key, track)`
- `whats_new` (discovery queue and weekly summary), `beyond` (artists to try or revisit, albums, sets)
- `server_health`: the last week's nightly runs and what failed them, warnings, backlog and sizes (INFRA.md, I9)

**Mixes**
- `make_mix(prompt | request | track_ids, length, exploration)`: a draft, in the draft editor
- `refine_mix(key, operations)`, `show_list(key)`
- `keep_draft`, `discard_draft`, `play(key)` (writes Now; then Claude plays the Sonos favourite)
- `start_radio`, `extend_radio`, `explore_artist`
- `edit_list`, `rename_list`, `delete_list`, `set_sync`

**Feedback and housekeeping**
- `give_verdict(track, yes | no | not_now | never, note)` and `queue_decision(release, heard | saved | dismissed)`, through `/sync`
- `add_milestone`, `remove_milestone`
- `rename_context`, `merge_context`, `split_context`, `add_tracklist`, `run_job`

**Prompts**
- "Weekly review": what's new, which lists got played, what to prune.
- "Dig into a period": `digest` + `eras` + `time_travel` for a span you name.

## Phases

### Phase 0: test the unknowns (about a day)

Built 22 Oct: `claude_mcp/` (MCP SDK 2.3, which has MCP Apps built in as `mcp.server.apps`), with `show_list` (every
list, or one list's tracks, in the list component; tapping a list opens it through `callServerTool`) and `play`
(writes Now; the component's Play button then asks Claude, through `sendMessage`, to play the Sonos favourite). The
OAuth server, `MCP_TOKEN` and the `"claude"` principal in `TokenAuth`, the chart and CI are in place as well. Tests:
`cd claude_mcp && uv run pytest` (OAuth metadata, registration refusing other redirect URIs, wrong passphrase or code
refused and rate-limited, a tampered login request refused, PKCE checked, refresh tokens rotated, and the tools over
MCP against a mocked API). The image is 57 MB and idles at about 84 MiB.
No Client ID Metadata Document support in the SDK, so Claude registers through Dynamic Client Registration.

Steps left for you (I can't reach the cluster, AWS or the Sonos app from here):

1. `cd claude_mcp && uv run python -m claude_mcp.setup`: choose the passphrase; it prints the four keys and an
   `otpauth://` link for the authenticator. Add the four keys to the AWS secret `audio-brain`, plus
   `MCP_PUBLIC_URL` = `https://<node>.<tailnet>.ts.net`.
2. Push. CI builds `audio-brain:mcp-<sha>`; Argo starts `audio-brain-mcp` and restarts the API (which needs
   `MCP_TOKEN`). If the keys weren't in AWS yet, `kubectl annotate externalsecret audio-brain-mcp-secrets
   force-sync=$(date +%s) --overwrite` fetches them now rather than within the hour.
3. On the node: `sudo tailscale funnel --bg --https=443 http://localhost:30801`. The first time, it gives a link to
   allow Funnel in the tailnet policy. Check from outside the tailnet (phone on mobile data):
   `curl https://<node>.<tailnet>.ts.net/.well-known/oauth-authorization-server`.
4. claude.ai → Settings → Connectors → Add custom connector, URL `https://<node>.<tailnet>.ts.net/mcp`. Connect;
   the sign-in page asks for the passphrase and the code.

- [x] Hello-world `claude_mcp` with one tool and one MCP App component, OAuth and Funnel, deployed to the cluster.
      (The API needed `audio-brain-api` in `ALLOWED_HOSTS`, the Host the MCP pod calls it by.)
- [x] Add it as a custom connector on claude.ai. Check the component renders on web, desktop and **the phone app**.
      **Renders on desktop and the phone** (see "Visual components"); not checked separately on the web.
- [x] Add TIDAL "Now" to Sonos favourites (done in the Sonos app 23 Oct, as "Audio Brain · Now"; the connector
      can't add favourites). Rewrite it from audio-brain, play the favourite through the connector,
      and check the new tracks play. **Works:** see "Playback through Sonos".
- [x] Write down both results here; adjust the fallback and playback sections if either fails. Both work, so
      neither section changes.

**Funnel took a while (23 Oct), now working.** The deployed server answers on the tailnet, and Funnel is on with the node
holding the `funnel` capability, but Tailscale never publishes the public A/AAAA record for the node's name (the
`ts.net` nameservers have only the ACME TXT record), so claude.ai can't reach it. This is
[tailscale/tailscale#21698](https://github.com/tailscale/tailscale/issues/21698) /
[#21829](https://github.com/tailscale/tailscale/issues/21829), open with many reports this week. Removing and
re-adding the `funnel` node attribute (a workaround reported there) is what fixed it: the first time, put straight
back, nothing happened; the second time, left off for a full minute, the record appeared some minutes later. A bug
report ID is on #21829. Check with `dig @ns1.dnsimple.com <node>.<tailnet>.ts.net A +short`. On the new cluster,
consider giving the MCP pod its own Tailscale device (a sidecar with its own name and auth key, Funnelling to the
server), so the node stays private and needs no host setup.

### Phase 1: data and stats

- [x] `MCP_TOKEN` and the `"claude"` principal in `TokenAuth` (done in Phase 0).
- [x] History endpoints, `history_rollup` job, `/query` with `audio_brain_ro`, the schema guide.
- [x] Read tools; chart, track card and artist card components.

**Built 24 Oct.** Server (`api/history.py`, `api/query.py`, `api/schema.py`, `history/rollup.py`):
`/history/stats` (any dimension, over a span or per week, month, quarter or year, by plays or weighted plays, with
filters by ID or exact name; `group=all` adds the average sound per period), `/history/listens`, `/history/sessions`,
`/tracks/{id}/story`, `/artists/{id}/story`, `/recommendations`, `POST /query`, and `/schema` (the guide, served
through a `schema` tool as well as being described here: claude.ai can't read MCP resources on its own). MCP tools
(`claude_mcp/reasoning.py`): `stats` (chart), `track_story` and `artist` (story), `sql`, `schema`, `listens`,
`sessions`, `search`, `search_by_vibe`, `contexts`, `now` and `recommendations`. The time-series and period tools
(`trends`, `eras`, `time_travel`, `digest`...) stay in Phase 3.

Components: `chart.html` draws ranked bars, lines over time (legend, crosshair, count or share; full screen adds a
sparkline per series and a table), columns for hours and weekdays, the hour × weekday heatmap, and sound over time.
Its Weighted/Plays switch calls `stats` again. `story.html` draws the track and artist cards; tapping an artist, a
top track or a similar artist opens theirs, with Back. Colours are the dataviz reference palette's first six slots
(validated against both themes). Series keep their colour while the component is open, and contexts keep their
own hues. To look: `npm run dev`, then `/harness/?page=chart&view=lines|bars|columns|heatmap|sound` or
`?page=story&view=track|artist`, with `&mode=fullscreen` and `&theme=dark`. Real results saved to
`ui/harness/local/<view>.json` (git-ignored, as the repo is public) show with `&local=1`.

Tests: `api.tests.QueryTests` (refuses writes, the token table, a second statement and switching role; stops at
the timeout; caps rows; summarises vectors) and `HistoryTests` (stats against hand counts, live and from the rollup,
including a play that is in May in London but April in UTC); `claude_mcp` tests each new tool against a mocked API.

Tried against the real database: the countries per quarter show the time in France (2023–24, up to a third of
plays), then Last.fm's unknown country from 2025. Labels per year show Sony fading after 2022, Universal after 2024,
and Armada rising to 2024. Track and artist stories, SQL and sound search all answer sensibly.

Steps left for you:
1. Add `POSTGRES_RO_PASSWORD` (any long random string) to the AWS secret `audio-brain`, then push. The API's
   migrate initContainer creates the role with it. If the key goes in after the deploy, restart the API pod once
   the secret has synced, so `migrate` sets the password.
2. The first nightly models run builds the rollup; until then everything is counted live, which is fine.
3. From the phone, ask the two questions below.
- *Done when:* from the phone, "what was I into when we moved to France?" and "which labels have I drifted away
  from since 2022?" come back as charts with sensible answers.

### Phase 2: making and refining mixes

- [x] `/make` with `request` and `tracks` recipes; `/drafts/{key}/refine` with versions.
- [x] Mix tools, draft editor, the Play → Sonos favourite handoff; verdict and queue tools.

**Built 24 Oct.** Server:
- `/make` takes `request` (any `MusicRequest` fields; the rest are left open, and the prompt isn't parsed), a
  `{"type": "tracks", "track_ids", "keep_order"}` recipe (tracks your verdicts rule out are dropped; sequenced for
  flow unless `keep_order`) and `title`. `recommend.query.ask` is split into parsing and `run(request)`, so both
  paths search the same way.
- `POST /drafts/{key}/refine` (`recommend/drafts.py`): `remove`, `add`, `move`, `lock`/`unlock`, `swap` (the
  closest track to it, within the draft's request, that the draft hasn't shown and no verdict rules out), `steer`
  (`changes` to the request, with `energy: up|down` as a step; `exploration`; or `text`, which the LLM folds into
  the request, falling back to adding the words to the sound), `length`, `resequence` and `undo`. Each call saves a
  version first (`Mix.versions`, last 20), so one undo takes back the whole call. What a draft was made from is kept
  in `Mix.spec`, so a steer changes the request rather than starting again. Locked tracks keep their place through a
  steer; when fewer tracks fit than there are open slots, the draft keeps its length with its own earlier tracks,
  and says so.
- Attribution: `Mix.made_by` and `DraftOutcome.by` record the principal (`"claude"` or `"app"`); a mix Claude made is
  logged with `Recommendation.source="claude"`, and a verdict sent with `MCP_TOKEN` gets `Feedback.source="claude"`
  (`where` stays the list or draft it came up in). `labels.CLAUDE` weighs all of these (1.0 now; 0 drops them).
- Labels from refining: a track removed or swapped out counts as swiped out (−0.5); one added or swapped in and still
  there when the draft is kept or played, +0.5 (`DraftOutcome.added`). Migration `recommend 0023_draft_refining`.

MCP (`claude_mcp/mixes.py`; the list helpers moved to `lists.py`): `make_mix` and `refine_mix` in the draft editor;
`start_radio`, `extend_radio`, `explore_artist` and `edit_list` in the list component; `discard_draft`,
`give_verdict` (yes, no, not now), `never_play` (on its own so it can carry `destructiveHint`), `queue_decision`,
`rename_list`, `delete_list` (destructive) and `set_sync`. `keep` and `play` from Phase 0 cover keeping and playing
drafts. Verdicts and release decisions go through `/sync`, as the app's outbox does.

Draft editor (`ui/draft.html`), following the style brief: inline it's the same compact card as a list (name, size,
colour strip, who's in it, and Open in Tidal, Keep it, Play on Sonos on one line), with Undo by the note once
something has changed. Full screen is the editor: steer chips (More energy, Calmer, Fewer vocals, More new, Darker;
one already in effect shows a ✓) and every track, its sigil first, with lock, swap and remove. Each tap calls
`refine_mix` and passes the new draft to Claude with `updateModelContext`, so Claude knows what changed when the
user next speaks. Harness: `/harness/?page=draft&width=390`; its `refine_mix` is a rough local stand-in.

Tested: `recommend.tests.DraftRefineTests` (a lock survives a steer, undo restores the whole call, swap avoids
verdicts and what's been shown, refining feeds the labels, bad operations refused) and `api.tests.MakeAndRefineTests`
(chosen tracks without vetoed ones, a structured request skipping the parser, verdicts from Claude attributed); the
`claude_mcp` tests cover the new tools against a mocked API. On the local database: a 20-track "warm, groovy, sunny,
instrumental, medium energy" draft took 21 s in a cold shell (most of it loading CLAP, which the API pod keeps
loaded); locking two and steering energy up took 1 s and rebuilt the other 18; a swap plus a "darker, moodier" steer
took 3.4 s.

Steps left for you:
1. Push. The API's migrate initContainer applies `recommend 0023`; CI builds the new MCP image.
2. In claude.ai, reconnect the connector or start a new chat, so Claude picks up the new tools.
3. From the phone: "something for cooking, less vocal", two refinements (one by tapping, one in words), then "play
   it in the kitchen". The S2 household has no Kitchen: Claude should ask for the room or use the Dining Room.
- *Done when:* from the phone, "something for cooking, less vocal", two refinements, then "play it in the
  kitchen" ends with the right music playing.

### Phase 3: meta insights

- [ ] `insights` job and endpoints; milestones.
- [ ] `trends`, `eras`, `taste_drift`, `rhythms`, `discovery_funnel`, `compare_periods`, `digest`; timeline and
      comparison components.
- *Done when:* "what was this year like compared to last?" gets a timeline, the main shifts, and lists made from
  them.

### Phase 4: polish

- [ ] Prompts; discovery cards.
- [ ] Optional: the Sonos Control API called directly, for one-tap play from the Android app.

## Tests

- `api/tests.py`: `/query` refuses writes, refuses `library_oauthtoken`, stops at the timeout and caps rows; the
  history aggregates match counts done by hand on a fixture; refine operations (lock survives a steer, undo restores,
  swap avoids verdicts).
- `claude_mcp`: each tool against a mocked `httpx`; OAuth flow (bad redirect URI refused, PKCE checked, refresh
  works, wrong TOTP refused).
- Components: build in CI; a smoke test renders each with fixture data.
- To look at a component: `cd claude_mcp/ui && npm run dev`, then `/harness/?view=list|index|releases&theme=dark&width=390`.
  The harness plays Claude's part (`AppBridge`) with sample data in `ui/harness/fixtures.ts`.

## Sources

- [Claude blog: interactive tools in Claude](https://claude.com/it/blog/interactive-tools-in-claude)
- [The Register: Claude MCP Apps arrives](https://www.theregister.com/2026/01/26/claude_mcp_apps_arrives)
- [MCP Apps API](https://apps.extensions.modelcontextprotocol.io/api/)
- [WorkOS: building MCP Apps](https://workos.com/blog/building-mcp-apps-inside-claude-chatgpt)
- [sunpeak: Claude connector OAuth](https://sunpeak.ai/blogs/claude-connector-oauth-authentication/)
- [Zuplo: Claude connectors compatibility](https://zuplo.com/learn/mcp/compatibility/clients/claude-connectors.md)
