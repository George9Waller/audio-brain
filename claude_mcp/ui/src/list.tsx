// The list component. Inline in the chat it's a short card: the list's name and age tag, its size, how new it is and
// how recently its context's been played, the colour strip of its contexts, who's in it, and three actions (Open in
// Tidal, Keep it, Play on Sonos). Expanded to Claude's full screen it's the whole list: the colour key and the hours
// it's played, every track with its sigil and why it's there, and tabs for its sound and the reasons; the head folds
// to its name and actions once you scroll. With no key it's every list, in the app's Lists sections (ListAges.kt).
// Claude gets every detail as text either way, so the card only shows what helps decide.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { App, applyDocumentTheme, applyHostFonts, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import type { McpUiDisplayMode, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import "./style.css";
import { applyFrame } from "./frame";
import { AgeTag, Artists, ColourStrip, EnergyArc, HourBars, KeyPath, ListSwatch, Sigil, contextColor } from "./music";
import type { Tone } from "./music";

export type Track = {
  track_id: number; artist: string; title: string; reason: string; novel: boolean; hue: number | null; verdict: string | null;
  bpm: number | null; camelot: string | null; energy: number | null; moods: number[] | null; vocal: boolean;
  context: string | null; artist_new: boolean; locked?: boolean; near?: boolean;
};
/** How recently a list's context has been played (lists.context_recency); null for a list without one. */
type Recency = {
  active: boolean | null; recent_plays: number; lifetime_plays: number; last_played: string | null;
  era_start: string | null; era_end: string | null; hours: number[]; era: string;
} | null;
type Other = { kind: string; title: string; artist: string; detail: string; reason: string; url: string | null; state: string | null };
type ListRow = {
  key: string; title: string; subtitle: string; section: string; kind: string; sync: boolean; age: string;
  tracks: number; count: string; novel: number | null; hue: number | null; hues: (number | null)[]; near: boolean[];
  recency: Recency;
};
type Index = { view: "index"; sections: { age: string; title: string; about: string }[]; lists: ListRow[] };
type List = {
  view: "list"; key: string; kind: string; section: string; title: string; subtitle: string; count: string;
  duration: string | null; new: number; novel: number | null; artists: string | null; top_artists: string[];
  energy: string | null; contexts: { name: string; hue: number | null; tracks: number; near?: number }[]; hue: number | null;
  recency: Recency;
  kept: boolean; can_keep: boolean; tidal_url: string | null; tracks: Track[]; others: Other[];
};
type Data = Index | List;

const shortName = (name: string) => name.split(" (")[0];
const pct = (share: number | null) => (share == null ? "" : `${Math.round(share * 100)}% new to you`);
const count = (n: number) => n.toLocaleString("en-GB");

/** Each section's line, as the app's Lists page says it (ListAges.kt); the server's `about` is worded for Claude. */
const CAPTIONS: Record<string, string> = {
  yours: "Lists you asked for, and artists you're exploring", sets: "Lists made from DJ sets' tracklists", now: "What you've played most in the last 45 days",
  lately: "Played lately, just less", era: "Big for you once, quiet lately", kept: "Their context has retired; kept because they're in Tidal",
  new: "Tracks you haven't played", habit: "What you replay, and what you've left behind", year: "What you played most each year",
  discover: "Releases, sets and artists to try",
};

/** Rows a section shows before "Show all" (ListAges.shown). */
const SHOWN: Record<string, number> = { yours: 10, sets: 5 };

/** "today", "yesterday", "3 days ago", "2 weeks ago", "5 months ago", "2 years ago". */
function ago(iso: string) {
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  if (days < 1) return "today";
  if (days < 2) return "yesterday";
  const [n, unit] = days < 14 ? [days, "day"] : days < 60 ? [Math.round(days / 7), "week"] : days < 365 ? [Math.round(days / 30), "month"] : [Math.round(days / 365), "year"];
  return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
}

/** The tag on a list's row in its section (ListAges.tag): none under Yours, From sets, Years and Discover, whose titles say it. */
function tagOf(age: string, recency: Recency): { label: string; tone: Tone } | null {
  if (age === "era") return { label: recency?.era || "Era", tone: "era" };
  const tags: Record<string, [string, Tone]> = { now: ["Now", "now"], lately: ["Lately", "lately"], kept: ["Retired", "retired"], new: ["New", "new"], habit: ["Habit", "habit"] };
  return tags[age] ? { label: tags[age][0], tone: tags[age][1] } : null;
}

/** How much a context's been played lately, as a list's row says it: "120 plays lately · last 2 days ago". */
const lately = (r: NonNullable<Recency>) => `${count(r.recent_plays)} plays lately` + (r.last_played ? ` · last ${ago(r.last_played)}` : "");

/** The line under a list's title in its section (ListAges.meta). */
function rowMeta(row: ListRow) {
  const base = [row.tracks ? `${row.tracks} tracks` : row.count, row.novel != null && row.tracks ? `${Math.round(row.novel * 100)}% new` : ""].filter(Boolean).join(" · ");
  const r = row.recency;
  if (!r) return base;
  if (row.age === "now" || row.age === "lately") return lately(r);
  if (row.age === "era") return `${count(r.lifetime_plays)} plays · ${count(r.recent_plays)} lately`;
  if (row.age === "kept") return `Kept as it last was · ${base}`;
  return base;
}

const app = new App({ name: "audio-brain list", version: "0.3.0" });
let setData: (data: Data) => void = () => {};
let setMode: (mode: McpUiDisplayMode) => void = () => {};
let pending: Data | null = null;

function applyContext(context: McpUiHostContext | undefined) {
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context?.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
  applyFrame({ ...app.getHostContext(), ...context });
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
function Actions({ list, act }: { list: List; act: Act }) {
  if (!list.tracks.length) return null;
  const keep = list.can_keep && (
    <button class="secondary" disabled={act.busy || act.kept} onClick={act.keep}>{act.kept ? "Kept ✓" : "Keep it"}</button>
  );
  const sonos = <button class="tertiary" disabled={act.busy} onClick={act.sonos}>Play on Sonos</button>;
  const tidal = <button class="primary" disabled={act.busy} onClick={act.tidal}>Open in Tidal ↗</button>;
  return <div class="actions">{tidal}{keep}{sonos}</div>;
}

/** A single list's recency line, and its tag when its context has retired (a list alone can't tell Now from Lately). */
function recencyOf(list: List) {
  const r = list.recency;
  if (!r) return { line: "", tag: null };
  if (r.active === false) return { line: "Kept as it last was; no longer rebuilt", tag: { label: "Retired", tone: "retired" as Tone } };
  return { line: lately(r), tag: null };
}

function ListCard({ list, act, expand, back }: { list: List; act: Act; expand: () => void; back: (() => void) | null }) {
  const meta = [list.count, list.duration, list.tracks.length ? pct(list.novel) : ""].filter(Boolean).join(" · ");
  const { line, tag } = recencyOf(list);
  return (
    <main class="card">
      {back && <button class="back" onClick={back}>‹ Your lists</button>}
      <div class="top">
        <div class="name">
          <div class="title-line"><h1>{list.title}</h1>{tag && <AgeTag {...tag} />}</div>
          <p class="meta">{meta}</p>
          {line && <p class="meta mono">{line}</p>}
        </div>
        <Expand onClick={expand} />
      </div>
      {list.tracks.length > 0 && <ColourStrip hues={list.tracks.map((t) => t.hue)} near={list.tracks.map((t) => !!t.near)} />}
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
      <i class={`verdict ${track.verdict ?? ""}`} aria-label={track.verdict ? `Verdict: ${track.verdict.replace("_", " ")}` : undefined} />
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
      {list.recency?.hours?.length === 24 && (
        <figure class="day"><HourBars hours={list.recency.hours} hue={list.hue} label="When in the day its context is played" />
          <figcaption>When in the day you play it; the hour now in the text colour</figcaption></figure>
      )}
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
  const other = list.contexts.slice(4).reduce((n, c) => n + c.tracks + (c.near ?? 0), 0);
  const { line, tag } = recencyOf(list);
  return (
    <main class="full">
      <div class="bar"><span class="faint">audio-brain</span>
        <button class="icon" aria-label="Close" onClick={collapse}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>
        </button>
      </div>
      <header class="head">
        <div class="title-line"><h1 class="big">{list.title}</h1>{tag && <AgeTag {...tag} />}</div>
        <p class="muted one-line">{summary}{list.energy ? `. ${list.energy}` : ""}</p>
        <div class="fold">
          {line && <p class="meta mono">{line}</p>}
          {list.artists && <Artists text={list.artists} names={list.top_artists} />}
        </div>
        {list.tracks.length > 0 && <ColourStrip hues={list.tracks.map((t) => t.hue)} near={list.tracks.map((t) => !!t.near)} tall />}
        <div class="fold">
          {shown.length > 0 && (
            <ul class="legend">
              {shown.map((c) => (
                <li key={c.name}><span class="dot" style={{ background: contextColor(c.hue) }} />{c.name} {c.tracks}
                  {c.near ? <span class="faint"> · {c.near} sound like it</span> : null}</li>
              ))}
              {other > 0 && <li><span class="dot" style={{ background: contextColor(null) }} />Other {other}</li>}
            </ul>
          )}
        </div>
        {list.tracks.length > 0 && <Actions list={list} act={act} />}
        {act.note && <p class="note">{act.note}</p>}
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
    </main>
  );
}

function IndexRow({ row, busy, open }: { row: ListRow; busy: boolean; open: (key: string) => void }) {
  const tag = tagOf(row.age, row.recency);
  return (
    <li>
      <button class="list-row" disabled={busy} onClick={() => open(row.key)}>
        <ListSwatch hues={row.hues} near={row.near ?? []} />
        <span class="text"><span class="heading">{shortName(row.title)}</span><span class="meta mono">{rowMeta(row)}</span></span>
        {tag && <AgeTag {...tag} />}
        <span class={row.sync ? "sync on" : "sync"} aria-label={row.sync ? "In Tidal" : "Not in Tidal"} />
      </button>
    </li>
  );
}

/** Every list in the app's sections. Inline, only Playing now (else the first section), as a summary, and a way to
 * see the rest in full screen, where every section is, in the app's order. */
function IndexCard({ data, busy, open, full, expand }: { data: Index; busy: boolean; open: (key: string) => void; full: boolean; expand: () => void }) {
  const sections = data.sections ?? [{ age: "", title: "Your lists", about: "" }];
  const [opened, setOpened] = useState<string[]>([]);  // sections showing all their lists
  const rows = (age: string) => data.lists.filter((l) => !age || l.age === age);
  const section = (s: { age: string; title: string; about: string }) => {
    const all = rows(s.age);
    const long = all.length > (SHOWN[s.age] ?? Infinity);
    const shown = long && !opened.includes(s.age) ? all.slice(0, SHOWN[s.age]) : all;
    const head = <span class="text"><h2>{s.title}</h2>{CAPTIONS[s.age] && <p class="caption">{CAPTIONS[s.age]}</p>}</span>;
    const isOpen = opened.includes(s.age);
    return (
      <section key={s.age} class="section">
        {long
          // a long section opens and closes from its header, which says how many there are
          ? <button class="section-head toggle" aria-expanded={isOpen} onClick={() => setOpened(isOpen ? opened.filter((a) => a !== s.age) : [...opened, s.age])}>
              {head}
              <span class="count mono">{isOpen ? `All ${all.length}` : `${SHOWN[s.age]} of ${all.length}`}</span>
              <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 9.5L12 16l6.5-6.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
          : <header class="section-head">{head}</header>}
        <ul class="lists">{shown.map((r) => <IndexRow key={r.key} row={r} busy={busy} open={open} />)}</ul>
      </section>
    );
  };
  const top = <div class="top"><div class="name"><h1>Your lists</h1><p class="meta">{data.lists.length} lists</p></div>{!full && <Expand onClick={expand} />}</div>;
  if (full) return <main class="full"><header class="head">{top}</header><div class="body">{sections.map(section)}</div></main>;
  const first = sections.find((s) => s.age === "now") ?? sections[0];
  return (
    <main class="card">
      {top}
      {first && section(first)}
      {data.lists.length > rows(first?.age ?? "").length && <button class="more" onClick={expand}>All {data.lists.length} lists, in sections</button>}
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
  useEffect(() => {
    setData = set; setMode = setModeState;
    // whatever the host said before this ran: the mode, as well as the result (pending)
    const shown = app.getHostContext()?.displayMode;
    if (shown) setModeState(shown);
    if (pending) set(pending);
  }, []);
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
  if (data.view === "index") return <IndexCard data={data} busy={busy} open={open} full={full} expand={expand} />;

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
