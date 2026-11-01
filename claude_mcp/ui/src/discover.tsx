// Discover, as the app's Discover page has it (android pages/DiscoverPage.kt, Discover.kt): sections named for why
// things are there, each a kicker in its tag's colour, a title and why, with its rows together in one card and "All"
// at the foot. A release's cover is its colours (the contexts its tracks sound closest to, hollow: none is yours yet)
// and plays it in Tidal; Save and Skip sit under its text, and deciding one brings the next up, the decided one left
// as a line to undo. Inline it's the queue's count and the first section; full screen, every section; "All" opens a
// section's own page. Decisions go through queue_decision, and Claude is told what the user decided.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import "./style.css";
import { connect } from "./bridge";
import { Close, Expand } from "./controls";
import { ListSwatch, Sigil } from "./music";
import type { Track } from "./list";

type Other = { release_id: number; title: string; detail: string; url: string | null; state: string | null };
type Then = { plays: number; peak: number; peak_at: string; last_at: string };
type Item = {
  kind: string; release_id?: number; title: string; artist?: string; detail?: string; reason?: string; why?: string; source?: string;
  url?: string; state?: string; hues?: number[]; others?: Other[]; others_count?: number; close_to?: string[]; close_to_more?: number;
  years?: Record<string, number>; span?: string; plays?: number; identified?: number; yours?: number; context?: string; then?: Then | null;
} & Partial<Track>;
type Section = { key: string; tone: string; title: string; caption: string; shown: number; kicker: string; total: number; items: Item[] };
type Data = { view: "page" | "section"; waiting: Record<string, number>; total: number; decided_this_week: number; sections: Section[] };

const { app, useHost } = connect<Data>("audio-brain discover");
const INLINE_ROWS = 2;
const count = (n: number) => n.toLocaleString("en-GB");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const month = (m: string) => (m?.length >= 7 ? `${MONTHS[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}` : m);
const soundsLike = (reason = "") => reason.split(" sounds like ")[1]?.trim() || "";
const open = (url?: string | null) => url && app.openLink({ url });

type Decided = Record<number, string>;
type Slot = { item: Item; decided?: string; fresh?: boolean };

/** The app's refill: the first `shown` undecided items in order, with ones decided on this visit left in place. */
function refill(items: Item[], decided: Decided, here: Set<number>, shown: number): Slot[] {
  const stateOf = (i: Item) => (i.release_id != null ? decided[i.release_id] : undefined) ?? i.state ?? "new";
  const before = items.filter((i) => i.kind !== "release" || stateOf(i) === "new" || here.has(i.release_id!));
  let open = 0;
  const slots: Slot[] = [];
  before.forEach((item, n) => {
    const state = item.kind === "release" ? stateOf(item) : "new";
    if (state !== "new") slots.push({ item, decided: state });
    else if (open < shown) { open++; slots.push({ item, fresh: n >= shown }); }
  });
  return slots;
}

type Decide = (item: { release_id?: number; title: string }, state: string) => void;

function Decision({ item, state, decide, busy }: { item: { release_id?: number; title: string }; state?: string; decide: Decide; busy: boolean }) {
  if (state === "saved" || state === "dismissed" || state === "heard") {
    return (
      <span class="decided">
        <span class={state === "saved" ? "mono keep-text" : "mono faint"}>{state === "saved" ? "Saved" : state === "heard" ? "Heard" : "Skipped"}</span>
        <button class="undo" disabled={busy} onClick={() => decide(item, "new")}>Undo</button>
      </span>
    );
  }
  return (
    <span class="decide">
      <button class="save" disabled={busy} onClick={() => decide(item, "saved")}>✓ Save</button>
      <button class="skip" disabled={busy} onClick={() => decide(item, "dismissed")}>Skip</button>
    </span>
  );
}

/** A release's icon: a hollow cell per analysed track in the colour of the context it sounds closest to, with a play
 * mark; tapping it plays it in Tidal. */
const Cover = ({ item, size = 52 }: { item: Item; size?: number }) => (
  <button class="cover" style={{ width: `${size}px`, height: `${size}px` }} aria-label={`Play ${item.title} in Tidal`} onClick={() => open(item.url)}>
    <ListSwatch hues={item.hues ?? []} near={(item.hues ?? []).map(() => true)} size={size} />
    <span class="play" aria-hidden="true"><svg width="12" height="12" viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z" fill="currentColor" /></svg></span>
  </button>
);

function ReleaseRow({ item, decided, decide, busy, why, fresh, compact }: {
  item: Item; decided: Decided; decide: Decide; busy: boolean; why: boolean; fresh?: boolean; compact?: boolean;
}) {
  const [unfolded, setUnfolded] = useState(false);
  const like = soundsLike(item.reason);
  const others = item.others ?? [];
  const openOthers = others.filter((o) => (decided[o.release_id] ?? o.state ?? "new") === "new");
  return (
    <div class="drow release">
      <div class="release-main">
        <Cover item={item} size={compact ? 44 : 52} />
        <div class="text">
          <button class="release-text" onClick={() => open(item.url)}>
            <span class="title-line"><span class="heading">{item.title}</span>{fresh && <span class="mono just-in">just in</span>}</span>
            <span class="caption muted">{item.artist}</span>
            <span class="mono faint one-line">{[item.detail, why ? item.why : ""].filter(Boolean).join(" · ")}</span>
            {like && !compact && <span class="caption">Sounds like {like}</span>}
          </button>
          <div class="decide-line">
            <Decision item={item} state={decided[item.release_id!] ?? item.state ?? undefined} decide={decide} busy={busy} />
            {(item.others_count ?? 0) > 0 && !compact && (
              <button class="more-from" onClick={() => setUnfolded(!unfolded)}>{unfolded ? "Fold" : `+${item.others_count} more`}</button>
            )}
          </div>
        </div>
      </div>
      {unfolded && (
        <div class="others">
          {others.map((o) => (
            <div key={o.release_id} class="other">
              <button class="text" onClick={() => open(o.url)}><span class="label one-line">{o.title}</span><span class="mono faint one-line">{o.detail}</span></button>
              <Decision item={o} state={decided[o.release_id] ?? o.state ?? undefined} decide={decide} busy={busy} />
            </div>
          ))}
          {(item.others_count ?? 0) > others.length && <p class="caption faint">…and {(item.others_count ?? 0) - others.length} more</p>}
          {openOthers.length > 1 && (
            <button class="quiet" disabled={busy} onClick={() => openOthers.forEach((o) => decide(o, "dismissed"))}>
              {openOthers.length === item.others_count ? `Skip the other ${openOthers.length}` : `Skip these ${openOthers.length}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function DecidedLine({ item, state, decide, busy }: { item: Item; state: string; decide: Decide; busy: boolean }) {
  return (
    <div class="drow decided-line">
      <span class={state === "saved" ? "label keep-text" : "label faint"}>{state === "saved" ? "Saved" : state === "heard" ? "Heard" : "Skipped"}</span>
      <span class="caption faint one-line grow">{item.title}</span>
      <button class="undo" disabled={busy} onClick={() => decide(item, "new")}>Undo</button>
    </div>
  );
}

/** An artist to try: their name and the artists of yours they're closest to, as chips. */
const CloseTo = ({ item }: { item: Item }) => (
  <button class="drow block" onClick={() => open(item.url)}>
    <span class="heading">{item.title}</span>
    {item.close_to?.length
      ? <span class="mini-chips"><span class="caption faint">Close to</span>
          {[...item.close_to, ...(item.close_to_more ? [`+${item.close_to_more}`] : [])].map((n) => <span key={n} class="mini-chip">{n}</span>)}</span>
      : <span class="caption faint">{item.reason}</span>}
  </button>
);

/** Plays per year, the biggest year in the era colour and this one in the now colour (the app's YearBars). */
function YearBars({ years, label }: { years: Record<string, number>; label: string }) {
  const now = new Date().getFullYear();
  const first = Math.min(...Object.keys(years).map(Number), now);
  const bars = Array.from({ length: now - first + 1 }, (_, i) => ({ year: first + i, plays: years[String(first + i)] ?? 0 }));
  const max = Math.max(...bars.map((b) => b.plays), 0);
  if (!max) return null;
  const peak = bars.reduce((a, b) => (b.plays > a.plays ? b : a)).year;
  const w = 300, h = 30, gap = 3, bw = (w - gap * (bars.length - 1)) / bars.length;
  return (
    <svg class="years" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={label}>
      {bars.map((b, i) => {
        const bh = Math.max(2, (b.plays / max) * h);
        return <rect key={b.year} x={i * (bw + gap)} y={h - bh} width={bw} height={bh}
                     fill={b.year === now ? "var(--age-now)" : b.year === peak ? "var(--age-era)" : "color-mix(in srgb, var(--faint) 60%, transparent)"} />;
      })}
    </svg>
  );
}

const Revisit = ({ item }: { item: Item }) => (
  <button class="drow block" onClick={() => open(item.url)}>
    <span class="title-line"><span class="heading grow">{item.title}</span>{item.plays != null && <span class="mono faint">{count(item.plays)} plays</span>}</span>
    {item.years && <YearBars years={item.years} label={`${item.title}: plays per year`} />}
    <span class="caption faint one-line">{[item.span ? `Mostly ${item.span}` : "", item.reason].filter(Boolean).join(" · ")}</span>
  </button>
);

/** A faded favourite: the track, then when you played it most and when you last did. */
const PastTrack = ({ item }: { item: Item }) => (
  <button class="drow track" onClick={() => open(item.url)}>
    <Sigil track={item as Track} size={30} />
    <span class="text"><span class="label one-line">{item.title}</span><span class="caption faint one-line">{item.artist}</span>
      {item.then?.peak ? <span class="then mono"><span class="era-text">{item.then.peak} plays around {month(item.then.peak_at)}</span><span class="faint">last {month(item.then.last_at)}</span></span> : null}
    </span>
  </button>
);

/** A set: its source and length, a bar for how much of its tracklist you play, and the context it's closest to. */
const SetRow = ({ item }: { item: Item }) => (
  <button class="drow block" onClick={() => open(item.url)}>
    <span class="heading">{item.title}</span>
    <span class="mono faint one-line">{[item.artist, item.detail].filter(Boolean).join(" · ")}</span>
    {item.identified ? (
      <span class="title-line"><span class="set-bar grow"><i style={{ width: `${((item.yours ?? 0) / item.identified) * 100}%` }} /></span>
        <span class="mono faint">{item.yours ?? 0} of {item.identified} tracks you play</span></span>
    ) : null}
    {item.context && <span class="caption faint">Closest to your {item.context}</span>}
  </button>
);

const Plain = ({ item }: { item: Item }) => (
  <button class="drow block" onClick={() => open(item.url)}>
    <span class="heading">{item.title}</span>
    <span class="caption faint">{[item.artist, item.reason].filter(Boolean).join(" · ")}</span>
  </button>
);

function SectionCard({ section, decided, here, decide, busy, rows, all, compact }: {
  section: Section; decided: Decided; here: Set<number>; decide: Decide; busy: boolean; rows: number; all?: () => void; compact?: boolean;
}) {
  const releases = ["artist", "remixer", "label"].includes(section.key);
  const slots = releases ? refill(section.items, decided, here, rows) : section.items.slice(0, rows).map((item) => ({ item } as Slot));
  if (!slots.length) return null;
  return (
    <section class="dsection">
      <header class="dhead">
        <span class={`kicker ${section.tone}`}><i />{section.kicker}</span>
        <h2>{section.title}</h2>
        {!compact && <p class="caption faint">{section.caption}</p>}
      </header>
      <div class="dcard">
        {slots.map(({ item, decided: state, fresh }, n) => {
          const key = item.release_id ?? `${item.kind}-${item.title}-${n}`;
          if (state) return <DecidedLine key={key} item={item} state={state} decide={decide} busy={busy} />;
          if (item.kind === "release") return <ReleaseRow key={key} item={item} decided={decided} decide={decide} busy={busy} why={section.key === "label"} fresh={fresh} compact={compact} />;
          if (item.kind === "track") return <PastTrack key={key} item={item} />;
          if (item.kind === "artist" && section.key === "past") return <Revisit key={key} item={item} />;
          if (item.kind === "artist") return <CloseTo key={key} item={item} />;
          if (item.kind === "set") return <SetRow key={key} item={item} />;
          return <Plain key={key} item={item} />;
        })}
        {all && section.total > rows && <button class="all" onClick={all}><span>All {count(section.total)}</span><span class="faint">→</span></button>}
      </div>
    </section>
  );
}

function Root() {
  const { data, setData, full, expand, collapse, busy: loading, note: failed } = useHost();
  const [page, setPage] = useState<Data | null>(null);  // the page, kept for Back from a section's own
  const [decided, setDecided] = useState<Decided>({});
  const [here, setHere] = useState<Set<number>>(new Set());
  const [log, setLog] = useState<string[]>([]);  // what was decided on this visit, for Claude
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  // tell Claude what the user decided here, so the conversation knows without asking
  useEffect(() => {
    if (log.length) app.updateModelContext({ content: [{ type: "text", text: `In the Discover card the user has, this visit:\n${log.map((l) => `- ${l}`).join("\n")}` }] }).catch(() => {});
  }, [log]);

  const decide: Decide = async (item, state) => {
    if (item.release_id == null) return;
    const id = item.release_id, before = decided[id];
    setDecided((d) => ({ ...d, [id]: state }));
    setHere((h) => new Set(h).add(id));
    setBusy(true); setNote("");
    try {
      const result = await app.callServerTool({ name: "queue_decision", arguments: { release_id: id, state } });
      if (result.isError) throw new Error();
      setLog((l) => [...l, `${state === "new" ? "undid the decision on" : state === "saved" ? "saved" : "skipped"} “${item.title}” (release #${id})`]);
    } catch {
      setDecided((d) => ({ ...d, [id]: before }));
      setNote("That didn't save. Ask Claude to try again.");
    } finally { setBusy(false); }
  };
  async function openSection(key: string) {
    setBusy(true);
    try {
      const result = await app.callServerTool({ name: "discover", arguments: { section: key } });
      if (result.structuredContent) { setPage(data); setData(result.structuredContent as Data); if (!full) expand(); }
    } catch { setNote("Couldn't open that section."); } finally { setBusy(false); }
  }

  if (!data) return <p class="note pad">Fetching Discover…</p>;
  const said = note || failed;
  const queueLine = data.total ? `${count(data.total)} new releases waiting · ${count(data.decided_this_week)} decided this week` : "No new releases waiting";
  const shared = { decided, here, decide, busy: busy || loading };

  if (data.view === "section") {
    const s = data.sections[0];
    return (
      <main class={full ? "full" : "card"}>
        {full && <div class="bar"><span class="faint">audio-brain · discover</span><Close onClick={collapse} /></div>}
        <header class={full ? "head" : ""}>
          {page && <button class="back" onClick={() => { setData(page); setPage(null); }}>‹ Discover</button>}
          <div class="top"><div class="name"><h1 class={full ? "big" : ""}>{s.title}</h1><p class="meta">{s.kicker}</p></div>{!full && <Expand onClick={expand} />}</div>
          {said && <p class="note">{said}</p>}
        </header>
        <div class={full ? "body" : ""}><SectionCard section={{ ...s, kicker: "", caption: s.caption }} {...shared} rows={s.items.length} /></div>
      </main>
    );
  }

  if (!full) {
    const first = data.sections[0];
    return (
      <main class="card">
        <div class="top"><div class="name"><h1>Discover</h1><p class="meta">{queueLine}</p></div><Expand onClick={expand} /></div>
        {first && <SectionCard section={first} {...shared} rows={Math.min(first.shown, INLINE_ROWS)} compact />}
        {said && <p class="note">{said}</p>}
        <button class="more" onClick={expand}>All of Discover, {data.sections.length} sections</button>
      </main>
    );
  }
  return (
    <main class="full">
      <div class="bar"><span class="faint">audio-brain · discover</span><Close onClick={collapse} /></div>
      <header class="head">
        <h1 class="big">Discover</h1>
        <p class="muted one-line">Things you haven't played yet, or haven't for a long time</p>
        <p class="mono new-text fold">{queueLine}</p>
        {said && <p class="note">{said}</p>}
      </header>
      <div class="body">
        {data.sections.map((s) => <SectionCard key={s.key} section={s} {...shared} rows={s.shown} all={() => openSection(s.key)} />)}
      </div>
    </main>
  );
}

render(<Root />, document.getElementById("root")!);
