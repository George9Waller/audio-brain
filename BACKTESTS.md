# Backtest plan: finding the best model

The plan for running backtests, until the best model is found and confirmed. It covers BACKLOG's C2, C5, C6 and C8,
the scorer's on/off decision, and the start of INFRA's I8. The backtest itself is `recommend/backtest.py` (B1); its
docstring says what it measures and how.

## Where things stand (31 Oct 2026)

- The last backtest ran on the Mac on 18–19 Oct, at 33k tracks. The catalogue is now 76k, and the live data is on
  the cluster, so the Mac's clones are stale.
- Decided so far:
  - `familiar-slots` (C1) won at every cutoff and is on;
  - `taste` in retrieval hurt new tracks and is off;
  - `taste-contexts` was within noise.
- Built but never run: `taste-explore`, `taste-sequence`, `novelty-*`, `coherence-*`, `artist-cap-*`,
  `resurface-10` and `dormant-*`.
- The track scorer turns itself on and off each night through its AUC check (`scorer.model_is_useful`):
  - on, 30 Oct: 0.692 against 0.609;
  - off, 31 Oct: 0.673 against 0.61.

  It has never been backtested in its current form.

## Ground rules

**Runner reproducibility and isolation.**
- A multi-cutoff run takes one temporary clone of the source database, then derives every cutoff clone from it. The
  report records that snapshot's ID and capture time, the cutoff dates and horizon, and the source database identity
  and size. In a Git checkout it records the commit and a fingerprint of server-side working-tree changes; in a
  container it records the actual image tag, with working-tree details marked unknown.
- Each variant builds in its own disposable clone of the prepared cutoff database and gets its own scorer model file.
  This keeps list/context writes and model loading from one variant out of the next.
- A prepared clone is reusable only when its code identity still matches. `--reuse` also requires all requested
  cutoffs to share the same snapshot ID; otherwise the run refreshes them together from one snapshot.
- Compare against the current winner with `--baseline <variant>`; the baseline is included automatically. For example,
  `--baseline familiar-slots --variant coherence-tight` compares the candidate with both the incumbent and default.
- `scorer-on` forces the trained scorer on, alongside `no-scorer` and the default's existing gate, for Phase 1.

**One measure to choose by.**
- **Primary: "came back %",** the share of a list's tracks played on two or more different days. It rewards
  lasting hits over one-off plays.
- **Guards:** a variant wins only if new played % and plays covered % don't fall significantly. Without them,
  "recommend old favourites" would win.
- Lift against recent plays is reported, but isn't used to decide.
- If discovery matters more than lasting hits, make "new played %" the primary measure instead. That changes which
  variants win in phases 2 and 4.

**Data held back.**
- **Tuning cutoffs:** 6, 12, 18 and 24 weeks back (42, 84, 126 and 168 days).
- **Held-back cutoffs:** 30 and 36 weeks back (210 and 252 days). Nothing is looked at there until phase 7.
- About 80 variants at a 95% bar means about four "win" by chance. The held-back cutoffs are what catch them.

**Tuned in layers.** Each phase starts from the winners of the one before, so nothing is tuned against settings
that are about to change. The order is list shaping, then retrieval, then the models.

**A win means:** the paired bootstrap's 95% interval clears zero across the cutoffs, and the difference points the
same way at most of them.

## Phase 0: run it at this scale (about a day)

**Backtests on the cluster.**
- A `backtest` Kubernetes Job on the large node, run outside 00:15–07:00.
- Clones are about 3 GB each at 76k tracks, so six cutoffs is about 18 GB. That fits in postgres's 80 Gi volume.
- Reports go to the data volume, and summaries go to a table, so the API, the app and the MCP can show them. This is
  the start of I8.
- A disabled-by-default one-off Job template is now in `helm/templates/backtest.yaml`; its settings are under
  `backtest` in `helm/values.yaml`. It uses the large node, the shared models volume, four threads, a 10 Gi memory
  limit and a non-preempting low priority. Set a unique `runId` and image tag when rendering it.
- **Pilot, 31 Oct 2026:** Helm lint and render passed. Two one-cutoff Jobs using the deployed image failed while
  creating their database clones. Both times PostgreSQL logged an untracked child exiting with code 2 at the exact
  second Kubernetes reported the in-container `pg_isready` readiness probe timing out. `pg_isready` returns 2 when
  the server does not respond; with PostgreSQL running as PID 1, the timed-out probe is the likely untracked child
  that made the postmaster restart and recover. The cgroup recorded no OOM kills. Both failed Jobs and their low
  priority class were removed; no clone database remained. No baseline result was produced. A local fix now builds
  PostgreSQL under `tini`, keeps the readiness check but raises its timeout to five seconds, and routes the image
  through ECR/Argo. That fix was then deployed for the retry below.
- **Retry after deployment:** PostgreSQL remained healthy under `tini`, with no crash recovery, but the pilot still
  failed. The source database had active connections, so cloning fell back to `pg_dump`; the application image had
  PostgreSQL client 17, which cannot dump the PostgreSQL 18 server. The failed Job and priority class were removed,
  and no clone remained. PostgreSQL client 18 was then deployed in the application image.
- **Second retry:** The dump and restore completed, and preparation finished, but recording the code identity failed:
  the application image has neither Git nor a Git checkout. PostgreSQL remained healthy, and the clone and temporary
  Job were removed. The backtest needs the actual image tag as its identity when running outside a Git checkout.
- **Successful pilot, 31 Oct:** With image identity fixed, a single-cutoff default run completed in about 14 minutes.
  PostgreSQL remained ready without a container restart; both clones and the temporary Job were removed. The full
  report is on the shared models volume at `backtests/2026-09-19/report-default.json`; the baseline is logged below.

**Variants that retrain a model.**
- Today a variant only patches how lists are built; the scorer and taste vectors are trained once per cutoff, in
  `prepare`, and shared.
- A training variant would copy the prepared clone with `CREATE DATABASE … TEMPLATE`, which takes seconds. Then it
  retrains only the model it changes, and builds and scores the lists there.
- Expected cost: 3–5 minutes per variant per cutoff, with no change to the live code.

**The scorer's switch as a setting.** Its on/off becomes a setting the backtest decides, in place of the nightly AUC
check. The check still runs and is still reported.

## Phase 1: baseline and the scorer

One run: `default` against `no-scorer` against `scorer-on` (forced on). It settles the scorer switching itself on and
off, and every later phase starts from the answer.

## Phase 2: list shaping (C2, C8)

These variants already exist. They patch the list build only, so they're cheap.

| Lever | Variants |
|---|---|
| Novelty share | 20%, 30%, 40% |
| Coherence cut (`editorial.COHERENCE`) | −0.25, −0.5 (now), −0.75 |
| Tracks per artist (`MAX_PER_ARTIST`) | 3, 4 (now), 5 |
| Resurfacing old favourites | 0 (now), 10% |
| Dormant mixes | 2, 4 (now), 6 |
| Taste elsewhere (C2) | `taste-explore`, `taste-sequence`, `taste-contexts` again |

## Phase 3: retrieval weights

- **One weight at a time** over `retrieve.SIGNAL_WEIGHTS`: each of text, context, seed, session, together, graph,
  artist and stick at ×0, ×0.5 and ×2.
- **Taste** at 0.25, 0.5 and 1. This is C2 (d), retuned now that C1 is on.
- **`CANON_BONUS`** at 0, 0.25, 0.5 (now) and 1.
- **The candidate pool (`POOL`)** at 400 (now) and 800.
- That's about 25 variants, all build only.
- Then a second pass around the best combination, since the weights interact.

## Phase 4: the track scorer

These are training variants, so they need Phase 0.

- **Model:**
  - the boosted trees (now);
  - the shallow trees;
  - logistic regression;
  - LightGBM with a ranking objective. This would be a new dependency; ranking within a list is closer to what the
    lists need.
- **What counts as a hit:** today, lasting affinity from a first play. Alternatives:
  - came back within 6 weeks;
  - later, verdicts and Keeps (C7, once `recommend.labels` has about 200 explicit labels).
- **Training window:**
  - all history (now);
  - the last 2 years only;
  - all history, with recent plays weighted more.
- **Inputs:** leave out one group at a time: taste, Last.fm's similar-artist graph, labels and remixers, mood and
  audio, and placement in the mix. A group whose removal doesn't hurt is dropped.
- **Its scope:** the scorer ranks new tracks only, or familiar ones too (a follow-up to C1).

## Phase 5: the taste model (C5)

**Screen first** on `taste.evaluate`'s held-out recall@10. It takes minutes and needs no clone. Only the best 3–4
go on to the full backtest.

| Setting | Values |
|---|---|
| `DIMENSIONS` | 64 (now), 128 |
| `WINDOW` | 3 (now), 5 |
| `EPOCHS` | 6 (now), 12 with a learning-rate schedule |
| `TEMPERATURE` | 0.05, 0.07 (now), 0.1 |
| `ARTIST_DROPOUT` | 0.15, 0.3 (now), 0.5 |

**Training data:**
- pairs only from tracks you came back to;
- a per-track adjustment for played tracks;
- **hard negatives:** similar-sounding tracks skipped past in the same session;
- an average of 3 differently seeded models.

## Phase 6: audio embeddings (C6)

1. **Cheap version first:** feed the CLAP audio embeddings the catalogue already has for text search into the taste
   model and the scorer, alongside Essentia. Screen it as in Phase 5, then backtest.
2. **Only if that helps:** a newer music model (MERT- or MuQ-style) on a 2,000-track sample from Tidal previews,
   before paying for a full re-analysis. Record the cost per track.

## Phase 7: combine, confirm, ship

1. **Combine** the winners of phases 2–6 into one candidate. Run it against the old default on all six cutoffs,
   including the two held back. Ship only if the primary measure also wins on the held-back two.
2. **Ship it, then watch** the live outcomes in the Recommendation log for 2–3 weeks. The log measures what was
   shown and then played, which the backtest can't.
3. **Score Tidal's mixes the same way** (C3), so the final number reads as "ours against Tidal's", not just against
   our old self.

## Cost and order

| Phase | Variants | Time per variant per cutoff | Total |
|---|---|---|---|
| 0 | none (building the runner) | — | about a day |
| 1–3 | about 45, build only | about 4 min | 8–12 h, two at a time over 2–3 days |
| 4–6 | about 15 after screening | about 5 min | 5–6 h |
| 7 | 1, on six cutoffs | about 5 min | under an hour |

- **Order:** 0, then 1 and 2, then 3. Phases 4, 5 and 6 can run in parallel once 3 is settled.
- **Wall time:** about one to two weeks, mostly unattended.
- **After each phase:** a short results table and a recommendation, added to the log below. Turning anything on is
  George's call.

## Results log

Add each phase's table here as it finishes: the variant, the primary measure and the guards against the default
(the mean and 95% interval), how many cutoffs agree, and the verdict.

### Phase 0 pilot — 31 Oct 2026

One `default` cutoff, 19 Sep–31 Oct (42 days), using image
`943d551191ba07a82a7c33d28311e0bbd02e4829` and snapshot `4b092b47d67f413aa8183b83ef5aeef1`.
The default scorer gate was off (test AUC 0.655). Across 1,302 distinct listed tracks, 42 came back on at least two
days (3.2%), 10 of 407 new tracks were played (2.5%), and the lists covered 16.3% of plays. Overall, 127 listed
tracks were played (9.8%). This is a runner and baseline check on one cutoff, not a comparison or a decision.

### Phase 1 — scorer comparison, 31 Oct–1 Nov 2026

Four tuning cutoffs (42, 84, 126 and 168 days), each scored over the following 42 days, using image
`ec884ff4c5697d67725cb8e63faff261a82d2a02` and shared snapshot `af32b38a5cd14478b7c4fbbb54498112`.
The default scorer gate was off for all four. Differences below are percentage points against `default`, with paired
bootstrap 95% intervals and the number of cutoffs whose difference has the same sign.

| Variant | Came back % | New played % | Plays covered % | Verdict |
|---|---|---|---|---|
| `no-scorer` | 0.00 [0.00, 0.00], 0/4 | 0.00 [0.00, 0.00], 0/4 | 0.00 [0.00, 0.00], 0/4 | Identical to default |
| `scorer-on` | +0.19 [-0.04, +0.43], 4/4 | +0.36 [-0.89, +1.51], 2/4 | +0.14 [-1.20, +1.45], 2/4 | Noise |

The forced-on scorer improved the primary measure at every cutoff, but its interval includes zero and neither guard
shows a clear change. Keep the current gate for now; this run does not establish that forcing the scorer on wins.
The Job took 66 minutes with one cutoff at a time and a two-CPU container limit. Its full summary is on the shared
models volume at `backtests/multi/2026-11-01-42-84-126-168-default+no-scorer+scorer-on.json`.
