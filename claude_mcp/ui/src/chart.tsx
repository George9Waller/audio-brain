// The stats component. Inline it's one chart with its title, a colour key and a Weighted/Plays switch; expanded to
// full screen it adds every series as a sparkline row and the numbers as a table. Which chart follows the question:
// ranked bars for one span, lines over time, columns for hours and weekdays, a heatmap for hour × weekday, the sound
// over time for all plays. Claude has every number as text either way.
import { render } from "preact";
import { useState } from "preact/hooks";
import "./style.css";
import { connect } from "./bridge";
import { Close, Expand } from "./controls";
import { Bars, Columns, Heatmap, Legend, Lines, ScaleKey, SLOTS, Sparkline, fmt, pct, slot, useColours, type Series } from "./charts";
import { contextColor } from "./music";

type Row = { key: string; label: string; plays: number; weighted: number; share: number; hue?: number | null };
type Sound = { bpm: number; energy: number; danceability: number; vocal: number; happy: number; sad: number; relaxed: number } | null;
export type Stats = {
  view: "bars" | "lines" | "columns" | "heatmap" | "sound" | "total";
  group: string; measure: "weighted" | "plays"; granularity: string; start: string | null; end: string | null;
  filters: Record<string, string>; total: { plays: number; weighted: number }; rows: Row[];
  unassigned: { plays: number; weighted: number } | null;
  periods?: string[]; totals?: number[]; series?: { key: string; label: string; values: number[]; hue?: number | null }[];
  sound?: Sound | Sound[];
};

const { useHost } = connect<Stats>("audio-brain chart");
const INLINE_BARS = 8, INLINE_LINES = 5;
const NOUNS: Record<string, string> = {
  artist: "Artists", track: "Tracks", album: "Albums", label: "Labels", style: "Styles", context: "Contexts", country: "Countries",
  source: "Sources", hour: "By hour", weekday: "By weekday", weekday_hour: "Hour by weekday", all: "All plays",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function periodLabel(day: string, granularity: string) {
  const [y, m, d] = day.split("-");
  if (granularity === "year") return y;
  if (granularity === "quarter") return `Q${Math.floor((+m - 1) / 3) + 1} ’${y.slice(2)}`;
  if (granularity === "month") return `${MONTHS[+m - 1]} ’${y.slice(2)}`;
  return `${+d} ${MONTHS[+m - 1]}`;
}

function title(s: Stats) {
  const when = s.start && s.end ? `${s.start.slice(0, 7)} to ${s.end.slice(0, 7)}` : s.start ? `since ${s.start.slice(0, 7)}` : s.end ? `before ${s.end.slice(0, 7)}` : "all time";
  const filters = Object.entries(s.filters).map(([k, v]) => (k === "country" ? `in ${v}` : v)).join(", ");
  return { name: NOUNS[s.group] ?? s.group, sub: [when, filters].filter(Boolean).join(" · ") };
}

function Switch<T extends string>({ value, options, onChange, busy }: { value: T; options: [T, string][]; onChange: (v: T) => void; busy?: boolean }) {
  return (
    <div class="switch" role="group">
      {options.map(([v, label]) => <button key={v} aria-pressed={value === v} class={value === v ? "on" : ""} disabled={busy} onClick={() => value !== v && onChange(v)}>{label}</button>)}
    </div>
  );
}

function Table({ s }: { s: Stats }) {
  const periods = s.periods ?? [];
  return (
    <>
      <table class="numbers">
        <thead><tr><th>{NOUNS[s.group] ?? ""}</th><th>Plays</th><th>Weighted</th><th>Share</th></tr></thead>
        <tbody>{s.rows.map((r) => <tr key={r.key}><td>{r.label}</td><td>{fmt(r.plays)}</td><td>{fmt(r.weighted)}</td><td>{pct(r.share)}</td></tr>)}</tbody>
      </table>
      {s.series && s.series.length > 0 && periods.length > 0 && s.group !== "all" && (
        <div class="scroll">
          <table class="numbers">
            <thead><tr><th />{periods.map((p) => <th key={p}>{periodLabel(p, s.granularity)}</th>)}</tr></thead>
            <tbody>
              {s.series.map((x) => <tr key={x.key}><td>{x.label}</td>{x.values.map((v, i) => <td key={i}>{fmt(v)}</td>)}</tr>)}
              <tr class="total"><td>All plays</td>{(s.totals ?? []).map((v, i) => <td key={i}>{fmt(v)}</td>)}</tr>
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Plot({ s, full, share, colour }: { s: Stats; full: boolean; share: boolean; colour: (key: string) => string }) {
  const [picked, pick] = useState<string | null>(null);
  const measure = s.measure === "weighted" ? "weighted plays" : "plays";
  const value = (r: Row) => (s.measure === "weighted" ? r.weighted : r.plays);
  const colourOf = (key: string, hue?: number | null) => (s.group === "context" ? contextColor(hue ?? null) : colour(key));
  const periods = s.periods ?? [];

  if (s.view === "bars") {
    const rows = full ? s.rows : s.rows.slice(0, INLINE_BARS);
    return <Bars bars={rows.map((r) => ({ label: r.label, value: value(r), detail: `${fmt(value(r))} · ${pct(r.share)}`,
                                          color: s.group === "context" ? contextColor(r.hue ?? null) : slot(0) }))} />;
  }
  if (s.view === "columns") {
    const rows = s.rows;
    return <Columns values={rows.map(value)} labels={rows.map((r) => r.label)} height={full ? 180 : 130}
                    tick={(i) => (s.group === "weekday" ? rows[i].label : +rows[i].key % 6 === 0 ? rows[i].label.slice(0, 2) : null)}
                    detail={(i) => `${fmt(value(rows[i]))} ${measure} · ${pct(rows[i].share)}`} />;
  }
  if (s.view === "heatmap") {
    const cells = Array.from({ length: 7 }, () => Array(24).fill(0));
    for (const r of s.rows) { const [d, h] = r.key.split("-").map(Number); cells[d - 1][h] = value(r); }
    return <><Heatmap cells={cells} detail={(d, h) => `${fmt(cells[d][h])} ${measure}`} /><ScaleKey low="fewer" high="more plays" /></>;
  }
  if (s.view === "total") {
    const sound = s.sound as Sound;
    return (
      <div class="tiles">
        <div class="tile"><span class="hero">{fmt(s.total.plays)}</span><span class="faint">plays</span></div>
        <div class="tile"><span class="hero">{fmt(s.total.weighted)}</span><span class="faint">weighted</span></div>
        {sound && <div class="tile"><span class="hero">{Math.round(sound.bpm)}</span><span class="faint">BPM on average</span></div>}
        {sound && <div class="tile"><span class="hero">{pct(sound.vocal)}</span><span class="faint">vocal</span></div>}
      </div>
    );
  }
  const label = (i: number) => periodLabel(periods[i], s.granularity);
  if (s.view === "sound") {
    const sound = (s.sound as Sound[]) ?? [];
    const keep = (f: (x: NonNullable<Sound>) => number) => sound.map((x) => (x ? f(x) : 0));
    const feel: Series[] = [
      { key: "energy", label: "Energy", values: keep((x) => x.energy), color: slot(0) },
      { key: "danceability", label: "Danceability", values: keep((x) => x.danceability), color: slot(1) },
      { key: "vocal", label: "Vocal share", values: keep((x) => x.vocal), color: slot(2) },
    ];
    return (
      <div class="stack">
        <figure><figcaption>{s.measure === "weighted" ? "Weighted plays" : "Plays"}</figcaption>
          <Columns values={s.totals ?? []} labels={periods.map((_, i) => label(i))} height={90}
                   tick={(i) => (i % Math.max(1, Math.ceil(periods.length / 6)) === 0 ? label(i) : null)} detail={(i) => `${fmt((s.totals ?? [])[i])} ${measure}`} /></figure>
        <figure><figcaption>Sound, 0 to 1</figcaption><Legend series={feel} picked={picked} pick={pick} />
          <Lines series={feel.filter((x) => !picked || x.key === picked)} periods={periods} label={label} max={1} height={full ? 200 : 150} format={(v) => v.toFixed(2)} /></figure>
        {full && <figure><figcaption>Tempo, BPM</figcaption>
          <Lines series={[{ key: "bpm", label: "BPM", values: keep((x) => x.bpm), color: slot(0) }]} periods={periods} label={label} height={140}
                 min={Math.floor((Math.min(...keep((x) => x.bpm).filter(Boolean)) - 5) / 10) * 10} /></figure>}
      </div>
    );
  }
  // lines: the leading series over time, as counts or as a share of every play in the period
  const totals = s.totals ?? [];
  const all: Series[] = (s.series ?? []).map((x) => ({ key: x.key, label: x.label, color: colourOf(x.key, x.hue),
    values: share ? x.values.map((v, i) => (totals[i] ? v / totals[i] : 0)) : x.values }));
  const drawn = all.slice(0, full ? SLOTS : INLINE_LINES);
  const shown = picked ? drawn.filter((x) => x.key === picked) : drawn;
  const format = share ? (v: number) => pct(v) : fmt;
  return (
    <div class="stack">
      <Legend series={drawn} picked={picked} pick={pick} />
      <Lines series={shown} periods={periods} label={label} height={full ? 240 : 180} format={format} />
      {full && all.length > 1 && (
        <ul class="sparks">
          {all.map((x) => (
            <li key={x.key}><span class="swatch" style={{ background: drawn.includes(x) ? x.color : "var(--faint)" }} />
              <span class="spark-label">{x.label}</span><Sparkline values={x.values} color={drawn.includes(x) ? x.color : "var(--faint)"} />
              <span class="number faint">{format(Math.max(...x.values))} peak</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Root() {
  const { data: s, full, expand, collapse, call, busy, note } = useHost();
  const [share, setShare] = useState(false);
  const [tab, setTab] = useState<"chart" | "table">("chart");
  const colour = useColours();
  if (!s) return <p class="note pad">Counting…</p>;
  const { name, sub } = title(s);
  const measure = (m: "weighted" | "plays") => call("stats", {
    group: s.group, granularity: s.granularity, measure: m, limit: Math.max(s.rows.length, 1),
    ...(s.start ? { start: s.start } : {}), ...(s.end ? { end: s.end } : {}), ...s.filters,
  });
  const total = s.measure === "weighted" ? s.total.weighted : s.total.plays;
  const meta = `${fmt(total)} ${s.measure === "weighted" ? "weighted plays" : "plays"}` + (s.group === "style" ? " · a play counts in each of its styles" : "");
  const controls = (
    <div class="controls">
      {s.view !== "total" && <Switch value={s.measure} options={[["weighted", "Weighted"], ["plays", "Plays"]]} onChange={measure} busy={busy} />}
      {s.view === "lines" && <Switch value={share ? "share" : "count"} options={[["count", "Count"], ["share", "Share"]]} onChange={(v) => setShare(v === "share")} />}
    </div>
  );
  const head = <div class="name"><h1>{name}</h1><p class="meta">{sub} · {meta}</p></div>;
  if (!full) {
    return (
      <main class="card">
        <div class="top">{head}<Expand onClick={expand} /></div>
        {controls}
        <Plot s={s} full={false} share={share} colour={colour} />
        {note && <p class="note">{note}</p>}
      </main>
    );
  }
  return (
    <main class="full">
      <div class="bar"><span class="faint">audio-brain</span><Close onClick={collapse} /></div>
      <header class="head">
        <h1 class="big">{name}</h1><p class="muted">{sub} · {meta}</p>
        {s.unassigned && <p class="note">{fmt(s.measure === "weighted" ? s.unassigned.weighted : s.unassigned.plays)} plays have no {s.group} yet, so aren't ranked.</p>}
        <div class="controls">
          <Switch value={tab} options={[["chart", "Chart"], ["table", "Table"]]} onChange={setTab} />
          {tab === "chart" && controls}
        </div>
      </header>
      <div class="body">{tab === "chart" ? <Plot s={s} full share={share} colour={colour} /> : <Table s={s} />}{note && <p class="note">{note}</p>}</div>
    </main>
  );
}

render(<Root />, document.getElementById("root")!);
