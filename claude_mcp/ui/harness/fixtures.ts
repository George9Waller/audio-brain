// Sample tool results, shaped like the server's structuredContent (claude_mcp/src/claude_mcp/server.py)
const CONTEXT: Record<number, string> = { 250: "Late night", 200: "Ambient mornings", 160: "Edges", 10: "Running", 40: "Sunday kitchen" };
const t = (track_id: number, artist: string, title: string, bpm: number | null, camelot: string | null, energy: number | null,
           hue: number | null, moods: number[] | null, reason: string, extra: { novel?: boolean; verdict?: string; vocal?: boolean; newArtist?: boolean; near?: boolean } = {}) =>
  ({ track_id, artist, title, bpm, camelot, energy, hue, moods, reason, novel: !!extra.novel, verdict: extra.verdict ?? null, vocal: !!extra.vocal,
     context: hue == null ? null : CONTEXT[hue] ?? null, artist_new: !!extra.newArtist, near: !!extra.near });

const strip = (...hues: (number | null)[]) => Array.from({ length: 30 }, (_, i) => hues[i % hues.length]);
const DAY = [0, 0, 0, 0, 0, 0, 1, 3, 5, 4, 3, 3, 4, 3, 2, 2, 3, 4, 6, 8, 10, 12, 9, 4];
const ago = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();
const recency = (recent: number, lifetime: number, last: number | null, era = "", active = true) =>
  ({ active, recent_plays: recent, lifetime_plays: lifetime, last_played: last == null ? null : ago(last), era_start: null, era_end: null, hours: DAY, era });
type Rec = ReturnType<typeof recency> | null;
const row = (key: string, title: string, kind: string, age: string, hue: number | null, tracks: number, novel: number | null, hues: (number | null)[],
             rec: Rec = null, sync = false, section = "lists", count = `${tracks} tracks`) =>
  ({ key, title, subtitle: "", section, kind, family: "", sync, age, tracks, count, novel, hue, hues,
     near: hues.map((_, i) => novel != null && i / hues.length < novel / 2), recency: rec });

export const lists = {
  view: "index",
  sections: [
    { age: "yours", title: "Yours", about: "lists the user asked for, and artists they're exploring" },
    { age: "now", title: "Playing now", about: "the contexts played most in the last 45 days" },
    { age: "era", title: "From earlier eras", about: "big once, quiet lately" },
    { age: "kept", title: "Kept as they were", about: "their context has retired; kept because they're in Tidal, no longer rebuilt" },
    { age: "new", title: "New music", about: "tracks the user hasn't played" },
    { age: "year", title: "Your years", about: "what they played most each year" },
    { age: "discover", title: "Discover", about: "releases, sets and artists to try, as the app's Discover page has them" },
  ],
  lists: [
    row("radio:1", "Radio: Bonobo – Kerala", "radio", "yours", 200, 40, 0.3, strip(200, 205, 160, 200, 250)),
    row("explore:quirky", "Something new and quirky", "explore", "yours", 160, 25, 0.6, strip(160, 250, 40, 160, null)),
    row("context:12", "Late night (2019–2026)", "mix", "now", 250, 32, 0.1, strip(250, 245, 255, 160), recency(214, 3120, 0), true),
    row("context:13", "Ambient mornings", "mix", "now", 230, 30, 0.2, strip(230, 250, 235), recency(96, 840, 2)),
    row("context:7", "Running: driving house", "mix", "now", 10, 40, 0.15, strip(10, 15, 5, 20), recency(41, 1210, 6), true),
    row("context:8", "Dom Dolla and friends", "mix", "era", 20, 35, 0.25, strip(20, 10, 25), recency(3, 960, 80, "2021–23")),
    row("context:9", "Festival trance", "mix", "kept", 300, 28, 0.1, strip(300, 310), recency(0, 410, 400, "2018–20", false), true),
    row("fresh", "Fresh for you", "fresh", "new", null, 30, 0.9, strip(250, 160, 10, 200)),
    row("year:2019", "2019", "year", "year", null, 50, 0, strip(300, 250, 10, 160, 40)),
    row("releases", "New releases", "releases", "discover", null, 0, null, [], null, false, "discover", "28 releases"),
  ],
};

export const list = {
  view: "list", key: "explore:late", kind: "explore", section: "lists", title: "Late night, but new",
  subtitle: "Unfamiliar, but close to what you play late", count: "12 tracks", duration: "58 m", new: 8, novel: 0.67,
  artists: "Kiasmos, Rival Consoles and Ólafur Arnalds lead, with 6 artists new to you.", top_artists: ["Kiasmos", "Rival Consoles", "Ólafur Arnalds"],
  energy: "Calm, with a lift in the middle.", hue: 250, recency: recency(214, 3120, 0), kept: false, can_keep: true, tidal_url: null,
  contexts: [{ name: "Late night", hue: 250, tracks: 4, near: 2 }, { name: "Ambient mornings", hue: 200, tracks: 3 }, { name: "Edges", hue: 160, tracks: 2 }, { name: "Running", hue: 10, tracks: 1 }],
  tracks: [
    t(101, "Kiasmos", "Blurred EP", 118, "4A", 0.3, 250, [0.3, 0.2, 0.5, 0.1, 0.5, 0.8], "like Late night", { novel: true, newArtist: true, near: true }),
    t(102, "Rival Consoles", "Persona", 120, "4A", 0.42, 250, [0.2, 0.3, 0.6, 0.3, 0.5, 0.5], "same label as Jon Hopkins", { novel: true, newArtist: true }),
    t(103, "Ólafur Arnalds", "saman", 68, "11A", 0.12, 200, [0.2, 0.05, 0.2, 0.05, 0.8, 0.9], "close to Nils Frahm", { novel: true, near: true }),
    t(104, "Jon Hopkins", "Open Eye Signal", 126, "4A", 0.72, 250, [0.3, 0.6, 0.7, 0.5, 0.3, 0.2], "you played it 6 times this week", { verdict: "yes" }),
    t(105, "Floating Points", "Nuits Sonores", 124, "5A", 0.68, 160, [0.5, 0.7, 0.8, 0.4, 0.2, 0.3], "the lift starts here", { novel: true, newArtist: true, vocal: true }),
    t(106, "Bonobo", "Kerala", 122, "5A", 0.58, 10, [0.5, 0.6, 0.8, 0.2, 0.3, 0.4], "played 40 times, not lately", { vocal: true, verdict: "not_now" }),
    t(107, "Christian Löffler", "Lune", 121, "6A", 0.5, 160, [0.4, 0.4, 0.6, 0.2, 0.4, 0.6], "", { novel: true, newArtist: true, vocal: true }),
    t(108, "Nils Frahm", "Says", 116, "6A", 0.44, 250, [0.3, 0.3, 0.5, 0.2, 0.4, 0.7], "you play it most Sundays"),
    t(109, "Kiasmos", "Looped", 120, "7A", 0.35, 250, [0.3, 0.2, 0.5, 0.1, 0.4, 0.8], "like Late night", { novel: true }),
    t(110, "Hania Rani", "Eden", 72, "7B", 0.15, 200, [0.4, 0.1, 0.2, 0.05, 0.6, 0.9], "", { novel: true, newArtist: true }),
    t(111, "Ólafur Arnalds", "re:member", 92, "8A", 0.3, 200, null, "close to Nils Frahm"),
    t(112, "Lambert", "Stay in the Dark", 80, "8A", 0.2, 250, [0.3, 0.1, 0.3, 0.1, 0.6, 0.8], "", { novel: true, newArtist: true }),
  ],
  others: [],
};

export const releases = {
  view: "list", key: "releases", kind: "releases", section: "discover", title: "New releases",
  subtitle: "From artists, remixers and labels you play", count: "3 releases", duration: null, new: 0, novel: null,
  artists: "Jon Hopkins, Bonobo and Anz, George Riley lead.", top_artists: ["Jon Hopkins", "Bonobo", "Anz, George Riley"],
  energy: null, contexts: [], hue: null, kept: false, can_keep: false, tidal_url: null, tracks: [],
  others: [
    { kind: "release", title: "Immunity (10th anniversary)", artist: "Jon Hopkins", detail: "Album · 4 Oct 2026 · artist you play", reason: "Matches your Late night sound", url: "https://tidal.com/browse/album/1", state: "new" },
    { kind: "release", title: "Kiara", artist: "Bonobo", detail: "Single · 18 Oct 2026 · remixer you like", reason: "Not analysed yet", url: "https://tidal.com/browse/album/2", state: "saved" },
    { kind: "release", title: "Night Swim", artist: "Anz, George Riley", detail: "EP · 11 Oct 2026 · on Ninja Tune", reason: "Close to your Running list", url: "https://tidal.com/browse/album/3", state: "new" },
  ],
};

export const played = { key: "explore:late", tidal_url: "https://tidal.com/browse/playlist/abc", sonos_favourite: "Audio Brain · Now" };

// The chart and story components (server reasoning.py). Synthetic: real listening data stays out of the repo; put
// real results in harness/local/<view>.json (git-ignored) and open ?page=chart&view=<view>&local=1 to see those.
const years = ["2019-01-01", "2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01", "2024-01-01", "2025-01-01", "2026-01-01"];
const seriesOf = (key: string, label: string, values: number[]) => ({ key, label, values });
const labels = [
  seriesOf("5", "Ninja Tune", [320, 410, 380, 290, 210, 160, 120, 90]), seriesOf("9", "Warp", [120, 150, 220, 260, 300, 280, 240, 260]),
  seriesOf("12", "Hyperdub", [200, 180, 90, 60, 40, 30, 20, 25]), seriesOf("3", "Anjunadeep", [10, 30, 90, 240, 380, 420, 390, 350]),
  seriesOf("7", "Erased Tapes", [80, 90, 110, 100, 120, 150, 170, 160]), seriesOf("2", "Defected", [5, 10, 40, 160, 220, 180, 90, 60]),
  seriesOf("8", "Ghostly", [40, 35, 30, 50, 45, 30, 25, 20]),
];
const total = (values: number[][]) => values[0].map((_, i) => values.reduce((n, v) => n + v[i], 0) * 3);
const rowsOf = (series: { key: string; label: string; values: number[] }[]) => {
  const sum = series.reduce((n, s) => n + s.values.reduce((a, b) => a + b, 0), 0) * 3;
  return series.map((s) => { const w = s.values.reduce((a, b) => a + b, 0); return { key: s.key, label: s.label, plays: Math.round(w * 1.3), weighted: w, share: w / sum }; });
};
const base = { measure: "weighted", start: "2019-01-01", end: null, filters: {}, unassigned: { plays: 2100, weighted: 1500 } };

export const stats: Record<string, object> = {
  lines: { ...base, view: "lines", group: "label", granularity: "year", total: { plays: 31000, weighted: 24000 }, rows: rowsOf(labels),
           periods: years, totals: total(labels.map((s) => s.values)), series: labels },
  bars: { ...base, view: "bars", group: "artist", granularity: "none", total: { plays: 31000, weighted: 24000 }, unassigned: null,
          rows: rowsOf(["Bonobo", "Jon Hopkins", "Kiasmos", "Four Tet", "Floating Points", "Nils Frahm", "Burial", "Rival Consoles", "Ólafur Arnalds", "Caribou", "Moderat", "Jamie xx"]
            .map((name, i) => seriesOf(String(i + 1), name, [900 / (1 + i * 0.35)]))) },
  columns: { ...base, view: "columns", group: "hour", granularity: "none", total: { plays: 31000, weighted: 24000 }, unassigned: null,
             rows: Array.from({ length: 24 }, (_, h) => ({ key: String(h), label: `${String(h).padStart(2, "0")}:00`, plays: 0,
               weighted: Math.round(400 + 900 * Math.max(0, Math.sin(((h - 7) / 24) * Math.PI * 2)) + (h > 20 ? 500 : 0)), share: 0.04 })) },
  heatmap: { ...base, view: "heatmap", group: "weekday_hour", granularity: "none", total: { plays: 31000, weighted: 24000 }, unassigned: null,
             rows: Array.from({ length: 168 }, (_, i) => { const d = Math.floor(i / 24) + 1, h = i % 24;
               return { key: `${d}-${String(h).padStart(2, "0")}`, label: "", plays: 0, share: 0,
                 weighted: Math.round((h >= 8 && h <= 18 ? (d <= 5 ? 120 : 60) : h >= 19 && h <= 23 ? (d >= 5 ? 150 : 70) : 10) * (0.8 + ((i * 37) % 10) / 25)) }; }) },
  sound: { ...base, view: "sound", group: "all", granularity: "year", total: { plays: 31000, weighted: 24000 }, unassigned: null,
           rows: [{ key: "", label: "All plays", plays: 31000, weighted: 24000, share: 1 }], periods: years,
           totals: [6636, 5772, 9007, 11281, 11893, 10594, 6551, 6820],
           sound: [0.33, 0.48, 0.58, 0.65, 0.68, 0.67, 0.67, 0.64].map((energy, i) => ({ energy, bpm: 110 + i * 2, danceability: 0.6 + i * 0.03,
             vocal: [0.49, 0.78, 0.89, 0.86, 0.8, 0.79, 0.78, 0.82][i], happy: 0.4, sad: 0.2, relaxed: 0.4 })) },
};

const months = Array.from({ length: 52 }, (_, i) => {
  const y = 2022 + Math.floor((i + 7) / 12), m = ((i + 7) % 12) + 1;
  return { month: `${y}-${String(m).padStart(2, "0")}-01`, plays: i === 0 ? 21 : i < 3 ? 11 - i * 3 : (i * 7) % 5 === 0 ? 4 + (i % 3) : 0, weighted: 0 };
});
export const story: Record<string, object> = {
  track: { view: "track", id: 104, name: "Open Eye Signal", artist: "Jon Hopkins", artist_id: 12, album: "Immunity", label: "Domino",
           released: "2013-06-03", plays: 97, weighted_plays: 74.8, first_played: "2022-08-01T12:10+01:00", last_played: "2026-06-02T15:40+01:00",
           sound: { bpm: 126, camelot: "4A", key: "F", scale: "minor", energy: 0.72, instrumental: 0.9, shape: [0.3, 0.6, 0.7, 0.5, 0.3, 0.2],
                    happy: 0.3, party: 0.6, danceability: 0.7, aggressive: 0.5, sad: 0.3, relaxed: 0.2 },
           context: { id: 12, name: "Late night", hue: 250 }, styles: ["Techno", "Ambient", "IDM"],
           countries: [{ key: "GB", label: "GB", plays: 80, weighted: 60 }, { key: "FR", label: "FR", plays: 12, weighted: 9 }, { key: "IS", label: "IS", plays: 5, weighted: 4 }],
           months, hours: Array.from({ length: 24 }, (_, h) => (h >= 21 || h < 2 ? 9 : h >= 12 && h <= 16 ? 4 : 1)),
           verdicts: [{ verdict: "yes", given_at: "2026-10-01", where: "Late night" }], favourites: ["tidal"],
           lists: [{ key: "context:12", title: "Late night (2019–2026)" }], tidal_url: "https://tidal.com/browse/track/1",
           recommendations: { suggested: 3, played: 2, not_played: 1, pending: 0 } },
  artist: { view: "artist", id: 12, name: "Jon Hopkins", country: "GB", area: "Kingston upon Thames", kind: "Person", active_from: 2001,
            plays: 873, weighted_plays: 693, first_played: "2021-09-06T09:00+01:00", last_played: "2026-09-29T22:00+01:00", tracks: 78, tracks_played: 69,
            top_tracks: [["Open Eye Signal", 97], ["Emerald Rush", 73], ["Singularity", 58], ["Collider", 39], ["Abandon Window", 38]].map(([title, plays], i) => ({ track_id: 104 + i, title, plays, weighted: Number(plays) * 0.8 })),
            months, hours: Array.from({ length: 24 }, (_, h) => (h >= 21 ? 30 : h >= 9 && h <= 17 ? 18 : 3)),
            labels: [{ key: "1", label: "Domino", plays: 550, weighted: 430 }, { key: "2", label: "Just Music", plays: 97, weighted: 70 }],
            styles: [{ key: "1", label: "Techno", plays: 430, weighted: 1 }, { key: "2", label: "Ambient", plays: 354, weighted: 1 }, { key: "3", label: "IDM", plays: 274, weighted: 1 }],
            contexts: [{ key: "12", label: "Late night", plays: 300, weighted: 1, hue: 250 }, { key: "13", label: "Ambient mornings", plays: 120, weighted: 1, hue: 200 }],
            similar: [{ name: "Kiasmos", match: 1, artist_id: 101, plays: 206 }, { name: "Rival Consoles", match: 0.7, artist_id: 102, plays: 107 }, { name: "Max Cooper", match: 0.6, artist_id: null, plays: 0 }],
            verdicts: { yes: 3 }, favourite: true, favourite_tracks: 7,
            releases: [{ title: "Ritual (live)", kind: "ALBUM", date: "2026-10-16", state: "new" }] },
};

export const draft = {
  ...list, view: "draft", key: "draft:ab12", kind: "draft", section: "drafts", title: "Kitchen, low vocals",
  subtitle: "sounds like “warm, rolling”; instrumental; medium energy", understood: "sounds like “warm, rolling”; instrumental; medium energy",
  kept: false, request: { sound: "warm, rolling", vocals: "instrumental", energy: "medium", familiarity: "mixed", styles: [] },
  exploration: null, versions: 0, done: [],
};

// discover: the app's sections (claude_mcp/discover.py), with the evidence each row shows
const rel = (release_id: number, artist: string, title: string, detail: string, why: string, hues: number[], reason = "", others: number = 0) =>
  ({ kind: "release", release_id, artist, title, detail, why, hues, reason, url: `https://tidal.com/browse/album/${release_id}`,
     others_count: others, others: Array.from({ length: Math.min(others, 3) }, (_, i) =>
       ({ release_id: release_id * 10 + i, title: `${title} (Remixes ${i + 1})`, detail: `Single · ${12 + i} Sep 2026`, url: null, state: null })) });
export const discover = {
  view: "page", waiting: { artist: 312, remixer: 64, label: 41 }, total: 417, decided_this_week: 12,
  sections: [
    { key: "artist", tone: "new", title: "Artists you play", caption: "Their new releases, closest to your taste first", shown: 3, kicker: "New · 312 waiting", total: 312,
      items: [
        rel(901, "Jon Hopkins", "Ritual (Remixes)", "EP · 24 Oct 2026", "artist you play", [250, 250, 160, 250], "It sounds like Late night", 4),
        rel(902, "Kiasmos", "II", "Album · 17 Oct 2026", "artist you play", [250, 200, 200, 250, 250, 200], "It sounds like Ambient mornings"),
        rel(903, "Bonobo", "Kiara (Live)", "Single · 18 Oct 2026", "artist you play", [], ""),
        rel(904, "Floating Points", "Cascade II", "Single · 10 Oct 2026", "artist you play", [160, 160], "It sounds like Edges"),
        rel(905, "Rival Consoles", "Landscape from Memory", "Album · 3 Oct 2026", "artist you play", [250, 250, 250], "It sounds like Late night"),
      ] },
    { key: "label", tone: "new", title: "Labels you play", caption: "Releases on the labels behind your tracks", shown: 2, kicker: "New · 41 waiting", total: 41,
      items: [rel(911, "Anz", "Night Swim", "EP · 11 Oct 2026", "on Ninja Tune", [10, 20, 10], "It sounds like Running"),
              rel(912, "Hania Rani", "Ghosts (Reworks)", "EP · 2 Oct 2026", "on Gondwana", [200, 250], "It sounds like Ambient mornings")] },
    { key: "artists", tone: "new", title: "Close to artists you love", caption: "Artists you haven't played, next to several you do", shown: 3, kicker: "New · 24 artists", total: 24,
      items: [{ kind: "artist", title: "Lambert", close_to: ["Nils Frahm", "Ólafur Arnalds", "Hania Rani"], close_to_more: 2, url: null },
               { kind: "artist", title: "Bicep", close_to: ["Floating Points", "Four Tet"], url: null }] },
    { key: "past", tone: "era", title: "Back from your past", caption: "Tracks and artists you loved and let go", shown: 4, kicker: "Era · 30 tracks", total: 40,
      items: [{ ...t(201, "Bonobo", "Cirrus", 125, "8A", 0.6, 10, [0.5, 0.6, 0.7, 0.2, 0.3, 0.4], ""), kind: "track", then: { plays: 140, peak: 38, peak_at: "2016-03", last_at: "2019-08" } },
              { kind: "artist", title: "Ludovico Einaudi", plays: 3297, span: "2016–18", reason: "none since 2021",
                years: { 2016: 1400, 2017: 1100, 2018: 600, 2019: 150, 2020: 40, 2021: 7 }, url: null }] },
    { key: "sets", tone: "habit", title: "Long listens", caption: "Sets on channels you save from, by how much of the tracklist you play", shown: 3, kicker: "Habit · 8 sets", total: 8,
      items: [{ kind: "set", title: "Boiler Room: Floating Points", artist: "Boiler Room", detail: "2 h 04 m", identified: 31, yours: 9, context: "Edges", url: null }] },
  ],
};
