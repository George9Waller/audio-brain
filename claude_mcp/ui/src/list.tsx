// The list component. Inline in the chat it's a short card: the list's name, size and how new it is, the colour strip
// of its contexts, who's in it, and three actions (Open in Tidal, Keep it, Play on Sonos). Expanded to Claude's full
// screen it's the whole list: the colour key, every track with its sigil and why it's there, and tabs for its sound
// and the reasons. Claude gets every detail as text either way, so the card only shows what helps decide.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { App, applyDocumentTheme, applyHostFonts, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import type { McpUiDisplayMode, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import "./style.css";
import { Artists, ColourStrip, EnergyArc, KeyPath, Sigil, contextColor } from "./music";

export type Track = {
  track_id: number; artist: string; title: string; reason: string; novel: boolean; hue: number | null; verdict: string | null;
  bpm: number | null; camelot: string | null; energy: number | null; moods: number[] | null; vocal: boolean;
  context: string | null; artist_new: boolean; locked?: boolean;
};
type Other = { kind: string; title: string; artist: string; detail: string; reason: string; url: string | null; state: string | null };
type ListRow = {
  key: string; title: string; subtitle: string; section: string; kind: string; sync: boolean;
  tracks: number; count: string; novel: number | null; hue: number | null; hues: (number | null)[];
};
type Index = { view: "index"; lists: ListRow[] };
type List = {
  view: "list"; key: string; kind: string; section: string; title: string; subtitle: string; count: string;
  duration: string | null; new: number; novel: number | null; artists: string | null; top_artists: string[];
  energy: string | null; contexts: { name: string; hue: number | null; tracks: number }[]; hue: number | null;
  kept: boolean; can_keep: boolean; tidal_url: string | null; tracks: Track[]; others: Other[];
};
type Data = Index | List;

const INDEX_SHOWN = 6;
const shortName = (name: string) => name.split(" (")[0];
const pct = (share: number | null) => (share == null ? "" : `${Math.round(share * 100)}% new to you`);

const app = new App({ name: "audio-brain list", version: "0.3.0" });
let setData: (data: Data) => void = () => {};
let setMode: (mode: McpUiDisplayMode) => void = () => {};
let pending: Data | null = null;

function applyContext(context: McpUiHostContext | undefined) {
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context?.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
  if (context?.displayMode) setMode(context.displayMode);
}

// handlers go on before connect(), so the first tool result isn't missed
app.ontoolresult = (result) => {
  const data = result.structuredContent as Data | undefined;
  if (!data) return;
  pending = data;
  setData(data);
};
app.onhostcontextchanged = applyContext;

function Expand({ onClick }: { onClick: () => void }) {
  return (
    <button class="icon" aria-label="Expand to full screen" onClick={onClick}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" />
      </svg>
    </button>
  );
}

type Act = { busy: boolean; kept: boolean; note: string; tidal: () => void; keep: () => void; sonos: () => void };

/** Open in Tidal first, then Keep it, then Play on Sonos: the order they matter in. */
function Actions({ list, act, stacked }: { list: List; act: Act; stacked?: boolean }) {
  if (!list.tracks.length) return null;
  const keep = list.can_keep && (
    <button class="secondary" disabled={act.busy || act.kept} onClick={act.keep}>{act.kept ? "Kept ✓" : "Keep it"}</button>
  );
  const sonos = <button class="tertiary" disabled={act.busy} onClick={act.sonos}>Play on Sonos</button>;
  const tidal = <button class="primary" disabled={act.busy} onClick={act.tidal}>Open in Tidal ↗</button>;
  return stacked
    ? <div class="actions stacked">{tidal}<div class="pair">{keep}{sonos}</div></div>
    : <div class="actions">{tidal}{keep}{sonos}</div>;
}

function ListCard({ list, act, expand, back }: { list: List; act: Act; expand: () => void; back: (() => void) | null }) {
  const meta = [list.count, list.duration, list.tracks.length ? pct(list.novel) : ""].filter(Boolean).join(" · ");
  return (
    <main class="card">
      {back && <button class="back" onClick={back}>‹ Your lists</button>}
      <div class="top">
        <div class="name"><h1>{list.title}</h1><p class="meta">{meta}</p></div>
        <Expand onClick={expand} />
      </div>
      {list.tracks.length > 0 && <ColourStrip hues={list.tracks.map((t) => t.hue)} />}
      {list.artists && <Artists text={list.artists} names={list.top_artists} />}
      <Actions list={list} act={act} />
      {act.note && <p class="note">{act.note}</p>}
    </main>
  );
}

function TrackRow({ track }: { track: Track }) {
  const why = [track.artist, track.artist_new ? "new artist" : track.novel ? "new" : "", track.reason].filter(Boolean).join(" · ");
  return (
    <li class="row">
      <Sigil track={track} size={32} />
      <span class="text"><span class="label">{track.title}</span><span class="caption">{why}</span></span>
    </li>
  );
}

function OtherRow({ item }: { item: Other }) {
  const detail = [item.artist, item.detail, item.state === "saved" ? "saved" : ""].filter(Boolean).join(" · ");
  const body = <span class="text"><span class="label">{item.title}</span><span class="caption">{detail}</span>{item.reason && <span class="caption faint">{item.reason}</span>}</span>;
  return (
    <li class="row">
      {item.url ? <a class="link" href={item.url} onClick={(e) => { e.preventDefault(); app.openLink({ url: item.url! }); }}>{body}<span class="go" aria-hidden="true">↗</span></a> : body}
    </li>
  );
}

function Sound({ list }: { list: List }) {
  const energies = list.tracks.map((t) => t.energy).filter((e): e is number => e != null);
  const keys = list.tracks.map((t) => t.camelot).filter((k): k is string => !!k);
  const bpms = list.tracks.map((t) => t.bpm).filter((b): b is number => b != null).map(Math.round);
  const vocal = list.tracks.filter((t) => t.vocal).length;
  return (
    <div class="sound">
      {energies.length > 2 && <figure><EnergyArc energies={energies} /><figcaption>Energy, in play order{list.energy ? `: ${list.energy.toLowerCase()}` : ""}</figcaption></figure>}
      {keys.length > 2 && <figure><KeyPath keys={keys} /><figcaption>Keys, in Camelot; clashes in red</figcaption></figure>}
      <dl>
        {bpms.length > 0 && <><dt>Tempo</dt><dd>{Math.min(...bpms)}–{Math.max(...bpms)} BPM</dd></>}
        <dt>Vocals</dt><dd>{vocal} of {list.tracks.length} tracks</dd>
      </dl>
    </div>
  );
}

function Why({ list }: { list: List }) {
  const groups = new Map<string, number>();
  for (const t of list.tracks) {
    const reason = t.reason || (t.novel ? "New to you" : "From your listening");
    groups.set(reason, (groups.get(reason) ?? 0) + 1);
  }
  return (
    <ul class="why">
      {[...groups].sort((a, b) => b[1] - a[1]).map(([reason, n]) => <li key={reason}><span>{reason}</span><span class="faint">{n}</span></li>)}
    </ul>
  );
}

function ListFull({ list, act, collapse }: { list: List; act: Act; collapse: () => void }) {
  const [tab, setTab] = useState<"tracks" | "sound" | "why">("tracks");
  const summary = [list.count, list.duration, list.tracks.length ? `${list.new} new to you` : ""].filter(Boolean).join(" · ");
  const shown = list.contexts.slice(0, 4);
  const other = list.contexts.slice(4).reduce((n, c) => n + c.tracks, 0);
  return (
    <main class="full">
      <div class="bar"><span class="faint">audio-brain</span>
        <button class="icon" aria-label="Close" onClick={collapse}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>
        </button>
      </div>
      <header class="head">
        <h1 class="big">{list.title}</h1>
        <p class="muted">{summary}{list.energy ? `. ${list.energy}` : ""}</p>
        {list.artists && <Artists text={list.artists} names={list.top_artists} />}
        {list.tracks.length > 0 && <ColourStrip hues={list.tracks.map((t) => t.hue)} tall />}
        {shown.length > 0 && (
          <ul class="legend">
            {shown.map((c) => <li key={c.name}><span class="dot" style={{ background: contextColor(c.hue) }} />{c.name} {c.tracks}</li>)}
            {other > 0 && <li><span class="dot" style={{ background: contextColor(null) }} />Other {other}</li>}
          </ul>
        )}
        {list.tracks.length > 0 && (
          <div class="tabs" role="tablist" aria-label="View">
            {(["tracks", "sound", "why"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} class={tab === t ? "tab on" : "tab"} onClick={() => setTab(t)}>
                {{ tracks: "Tracks", sound: "Sound", why: "Why these" }[t]}
              </button>
            ))}
          </div>
        )}
      </header>
      <div class="body">
        {tab === "tracks" && <ol class="rows">{list.tracks.map((t) => <TrackRow key={t.track_id} track={t} />)}{list.others.map((o, n) => <OtherRow key={n} item={o} />)}</ol>}
        {tab === "sound" && <Sound list={list} />}
        {tab === "why" && <Why list={list} />}
      </div>
      {list.tracks.length > 0 && <footer class="foot"><Actions list={list} act={act} stacked />{act.note && <p class="note">{act.note}</p>}</footer>}
    </main>
  );
}

function IndexCard({ data, busy, open, full }: { data: Index; busy: boolean; open: (key: string) => void; full: boolean }) {
  const [all, setAll] = useState(full);
  const lists = all ? data.lists : data.lists.slice(0, INDEX_SHOWN);
  return (
    <main class={full ? "full pad" : "card"}>
      <div class="top"><div class="name"><h1>Your lists</h1><p class="meta">{data.lists.length} lists · tap one to open it</p></div></div>
      <ul class="lists">
        {lists.map((l) => (
          <li key={l.key}>
            <button class="list" disabled={busy} onClick={() => open(l.key)}>
              <span class="line"><span class="label">{shortName(l.title)}</span>
                <span class="faint small">{l.tracks ? [`${l.tracks}`, l.novel != null ? `${Math.round(l.novel * 100)}% new` : ""].filter(Boolean).join(" · ") : l.count}</span></span>
              {l.hues.length > 0 && <ColourStrip hues={l.hues} />}
            </button>
          </li>
        ))}
      </ul>
      {!all && data.lists.length > INDEX_SHOWN && <button class="more" onClick={() => setAll(true)}>Show all {data.lists.length}</button>}
    </main>
  );
}

function Root() {
  const [data, set] = useState<Data | null>(pending);
  const [index, setIndex] = useState<Index | null>(null);  // kept for Back after opening a list from it
  const [mode, setModeState] = useState<McpUiDisplayMode>(app.getHostContext()?.displayMode ?? "inline");
  const [local, setLocal] = useState(false);  // expanded in place, where the host has no full screen
  const [busy, setBusy] = useState(false);
  const [kept, setKept] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => { setData = set; setMode = setModeState; }, []);
  useEffect(() => { setKept(data?.view === "list" && data.kept); setNote(""); }, [data]);

  const full = mode === "fullscreen" || local;
  async function expand() {
    if (app.getHostContext()?.availableDisplayModes?.includes("fullscreen")) {
      const result = await app.requestDisplayMode({ mode: "fullscreen" });
      setModeState(result.mode);
    } else setLocal(true);
  }
  async function collapse() {
    if (mode === "fullscreen") setModeState((await app.requestDisplayMode({ mode: "inline" })).mode);
    setLocal(false);
  }

  async function run(work: () => Promise<void>) {
    setBusy(true); setNote("");
    try { await work(); } catch { setNote("That didn't work. Ask Claude to try again."); } finally { setBusy(false); }
  }
  const open = (key: string) => run(async () => {
    const result = await app.callServerTool({ name: "show_list", arguments: { key } });
    if (!result.structuredContent) return;
    if (data?.view === "index") setIndex(data);
    set(result.structuredContent as Data);
  });

  if (!data) return <p class="note pad">Fetching your lists…</p>;
  if (data.view === "index") return <IndexCard data={data} busy={busy} open={open} full={full} />;

  const list = data;
  const act: Act = {
    busy, kept, note,
    // its own Tidal playlist when it's synced; otherwise it's written to "Now", as the app's Play in Tidal does
    tidal: () => run(async () => {
      let url = list.tidal_url;
      if (!url) url = ((await app.callServerTool({ name: "play", arguments: { key: list.key } })).structuredContent as { tidal_url?: string } | undefined)?.tidal_url ?? null;
      if (url) await app.openLink({ url }); else setNote("Couldn't reach Tidal.");
    }),
    keep: () => run(async () => {
      const result = await app.callServerTool({ name: "keep", arguments: { key: list.key } });
      if (result.isError) setNote("Couldn't keep it."); else { setKept(true); setNote(list.kind === "draft" ? "Kept in your lists." : "Kept: synced to Tidal and pinned."); }
    }),
    sonos: () => run(async () => {
      const result = await app.callServerTool({ name: "play", arguments: { key: list.key } });
      if (result.isError) { setNote("Couldn't write it to Tidal."); return; }
      setNote("Asking Claude to play it…");
      await app.sendMessage({ role: "user", content: [{ type: "text",
        text: `I've written “${list.title}” to the TIDAL playlist “Now”. Play the Sonos favourite “Audio Brain · Now” — ask me which room if you don't know.` }] });
    }),
  };
  return full
    ? <ListFull key={list.key} list={list} act={act} collapse={collapse} />
    : <ListCard list={list} act={act} expand={expand} back={index ? () => { set(index); setIndex(null); } : null} />;
}

render(<Root />, document.getElementById("root")!);
app.connect().then(() => applyContext(app.getHostContext()));
