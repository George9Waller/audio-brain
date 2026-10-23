// Sample tool results, shaped like the server's structuredContent (claude_mcp/src/claude_mcp/server.py)
const CONTEXT: Record<number, string> = { 250: "Late night", 200: "Ambient mornings", 160: "Edges", 10: "Running", 40: "Sunday kitchen" };
const t = (track_id: number, artist: string, title: string, bpm: number | null, camelot: string | null, energy: number | null,
           hue: number | null, moods: number[] | null, reason: string, extra: { novel?: boolean; verdict?: string; vocal?: boolean; newArtist?: boolean } = {}) =>
  ({ track_id, artist, title, bpm, camelot, energy, hue, moods, reason, novel: !!extra.novel, verdict: extra.verdict ?? null, vocal: !!extra.vocal,
     context: hue == null ? null : CONTEXT[hue] ?? null, artist_new: !!extra.newArtist });

const strip = (...hues: (number | null)[]) => Array.from({ length: 30 }, (_, i) => hues[i % hues.length]);
const row = (key: string, title: string, kind: string, family: string, hue: number | null, tracks: number, novel: number | null, hues: (number | null)[], sync = false, section = "lists", count = `${tracks} tracks`) =>
  ({ key, title, subtitle: "", section, kind, family, sync, tracks, count, novel, hue, hues });

export const lists = {
  view: "index",
  lists: [
    row("radio:1", "Radio: Bonobo – Kerala", "radio", "", 200, 40, 0.3, strip(200, 205, 160, 200, 250)),
    row("explore:quirky", "Something new and quirky", "explore", "", 160, 25, 0.6, strip(160, 250, 40, 160, null)),
    row("context:12", "Late night (2019–2026)", "mix", "Downtempo", 250, 32, 0.1, strip(250, 245, 255, 160), true),
    row("context:13", "Ambient mornings", "mix", "Downtempo", 230, 30, 0.2, strip(230, 250, 235)),
    row("context:7", "Running: driving house", "mix", "House", 10, 40, 0.15, strip(10, 15, 5, 20), true),
    row("context:8", "Dom Dolla and friends", "mix", "House", 20, 35, 0.25, strip(20, 10, 25)),
    row("year:2019", "2019", "year", "", null, 50, 0, strip(300, 250, 10, 160, 40)),
    row("rediscovery", "Rediscovery", "rediscovery", "", null, 30, 0, strip(250, 10, 40, 200)),
    row("releases", "New releases", "releases", "", null, 0, null, [], false, "discover", "28 releases"),
  ],
};

export const list = {
  view: "list", key: "explore:late", kind: "explore", section: "lists", title: "Late night, but new",
  subtitle: "Unfamiliar, but close to what you play late", count: "12 tracks", duration: "58 m", new: 8, novel: 0.67,
  artists: "Kiasmos, Rival Consoles and Ólafur Arnalds lead, with 6 artists new to you.", top_artists: ["Kiasmos", "Rival Consoles", "Ólafur Arnalds"],
  energy: "Calm, with a lift in the middle.", hue: 250, kept: false, can_keep: true, tidal_url: null,
  contexts: [{ name: "Late night", hue: 250, tracks: 6 }, { name: "Ambient mornings", hue: 200, tracks: 3 }, { name: "Edges", hue: 160, tracks: 2 }, { name: "Running", hue: 10, tracks: 1 }],
  tracks: [
    t(101, "Kiasmos", "Blurred EP", 118, "4A", 0.3, 250, [0.3, 0.2, 0.5, 0.1, 0.5, 0.8], "like Late night", { novel: true, newArtist: true }),
    t(102, "Rival Consoles", "Persona", 120, "4A", 0.42, 250, [0.2, 0.3, 0.6, 0.3, 0.5, 0.5], "same label as Jon Hopkins", { novel: true, newArtist: true }),
    t(103, "Ólafur Arnalds", "saman", 68, "11A", 0.12, 200, [0.2, 0.05, 0.2, 0.05, 0.8, 0.9], "close to Nils Frahm", { novel: true }),
    t(104, "Jon Hopkins", "Open Eye Signal", 126, "4A", 0.72, 250, [0.3, 0.6, 0.7, 0.5, 0.3, 0.2], "you played it 6 times this week", { verdict: "yes" }),
    t(105, "Floating Points", "Nuits Sonores", 124, "5A", 0.68, 160, [0.5, 0.7, 0.8, 0.4, 0.2, 0.3], "the lift starts here", { novel: true, newArtist: true, vocal: true }),
    t(106, "Bonobo", "Kerala", 122, "5A", 0.58, 10, [0.5, 0.6, 0.8, 0.2, 0.3, 0.4], "played 40 times, not lately", { vocal: true }),
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
