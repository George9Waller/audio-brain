// The story component: a track card or an artist card. Inline it's the headline (plays, first heard, last played),
// the plays per month since the start and the few facts that matter; expanded it's everything: when in the day,
// where, styles, contexts, lists, verdicts and, for an artist, every top track and similar artist. Tapping an
// artist, a track or a similar artist opens their story here.
import { render } from "preact";
import { useState } from "preact/hooks";
import "./style.css";
import { connect } from "./bridge";
import { Close, Expand } from "./controls";
import { Bars, Columns, fmt, slot } from "./charts";
import { Sigil, contextColor } from "./music";
import type { Track } from "./list";

type Month = { month: string; plays: number; weighted: number };
type Ranked = { key: string; label: string; plays: number; weighted: number; hue?: number | null };
type Sound = { bpm: number; camelot: string | null; key: string; scale: string; energy: number; instrumental: number; shape?: number[];
               happy: number; party: number; danceability: number; aggressive: number; sad: number; relaxed: number };
type TrackStory = {
  view: "track"; id: number; name: string; artist: string; artist_id: number; album: string | null; label: string | null;
  released: string | null; plays: number; weighted_plays: number; first_played: string | null; last_played: string | null;
  sound: Sound | null; context: { id: number; name: string; hue: number } | null; styles: string[]; countries: Ranked[];
  months: Month[]; hours: number[]; verdicts: { verdict: string; given_at: string; where: string }[]; favourites: string[];
  lists: { key: string; title: string }[]; tidal_url: string | null;
  recommendations: { suggested: number; played: number; not_played: number; pending: number };
};
type ArtistStory = {
  view: "artist"; id: number; name: string; country: string; area: string; kind: string; active_from: number | null;
  plays: number; weighted_plays: number; first_played: string | null; last_played: string | null; tracks: number; tracks_played: number;
  top_tracks: { track_id: number; title: string; plays: number; weighted: number }[]; months: Month[]; hours: number[];
  labels: Ranked[]; styles: Ranked[]; contexts: Ranked[]; similar: { name: string; match: number; artist_id: number | null; plays: number }[];
  verdicts: Record<string, number>; favourite: boolean; favourite_tracks: number;
  releases: { title: string; kind: string; date: string; state: string }[];
};
type Story = TrackStory | ArtistStory;

const { app, useHost } = connect<Story>("audio-brain story");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso: string | null) => (iso ? `${+iso.slice(8, 10)} ${MONTHS[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}` : "never");
const month = (iso: string | null) => (iso ? `${MONTHS[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}` : "never");  // fits a tile
const VERDICTS: Record<string, string> = { yes: "Yes", no: "No", not_now: "Not now", never: "Never" };

function Tiles({ items }: { items: [string, string][] }) {
  return <div class="tiles">{items.map(([value, label]) => <div class="tile" key={label}><span class="hero">{value}</span><span class="faint">{label}</span></div>)}</div>;
}

/** Plays per month, from the first month to now; ticks on Januaries. */
function MonthChart({ months, full }: { months: Month[]; full: boolean }) {
  if (!months.length) return null;
  const years = new Set(months.map((m) => m.month.slice(0, 4))).size;
  const every = Math.max(1, Math.ceil(years / (full ? 8 : 5)));
  return (
    <figure><figcaption>Plays per month</figcaption>
      <Columns values={months.map((m) => m.plays)} labels={months.map((m) => `${MONTHS[+m.month.slice(5, 7) - 1]} ${m.month.slice(0, 4)}`)} height={full ? 140 : 100}
               tick={(i) => (months[i].month.slice(5, 7) === "01" && +months[i].month.slice(0, 4) % every === 0) || (i === 0 && years === 1) ? months[i].month.slice(0, 4) : null}
               detail={(i) => `${months[i].plays} plays`} /></figure>
  );
}

function HourChart({ hours }: { hours: number[] }) {
  if (!hours.some(Boolean)) return null;
  return (
    <figure><figcaption>When in the day</figcaption>
      <Columns values={hours} labels={hours.map((_, h) => `${String(h).padStart(2, "0")}:00`)} height={90}
               tick={(h) => (h % 6 === 0 ? String(h).padStart(2, "0") : null)} detail={(h) => `${hours[h]} plays`} /></figure>
  );
}

function Chips({ items }: { items: { label: string; color?: string }[] }) {
  if (!items.length) return null;
  return <ul class="chips">{items.map((c) => <li key={c.label}>{c.color && <span class="dot" style={{ background: c.color }} />}{c.label}</li>)}</ul>;
}

function Ranks({ caption, items, color }: { caption: string; items: Ranked[]; color?: (r: Ranked) => string }) {
  if (!items.length) return null;
  return <figure><figcaption>{caption}</figcaption><Bars bars={items.map((r) => ({ label: r.label, value: r.plays, detail: `${fmt(r.plays)}`, color: color?.(r) ?? slot(0) }))} /></figure>;
}

function asTrack(s: TrackStory): Track {
  const sound = s.sound;
  const moods = sound ? (sound.shape?.length === 6 ? sound.shape : [sound.happy, sound.party, sound.danceability, sound.aggressive, sound.sad, sound.relaxed]) : null;
  return { track_id: s.id, artist: s.artist, title: s.name, reason: "", novel: false, hue: s.context?.hue ?? null, verdict: null,
           bpm: sound?.bpm ?? null, camelot: sound?.camelot ?? null, energy: sound?.energy ?? null, moods, vocal: sound ? sound.instrumental < 0.5 : false,
           context: s.context?.name ?? null, artist_new: false };
}

type CardProps<S> = { s: S; full: boolean; open: (tool: string, args: Record<string, unknown>) => void; side?: preact.JSX.Element | false };

/** The card's header, with the expand button beside it inline; everything below it gets the full width. */
const Head = ({ children, side }: { children: preact.ComponentChildren; side?: preact.JSX.Element | false }) =>
  side ? <div class="top top-start">{children}{side}</div> : <>{children}</>;

function TrackCard({ s, full, open, side }: CardProps<TrackStory>) {
  const sound = s.sound;
  const facts = [
    sound && `${Math.round(sound.bpm)} BPM · ${sound.camelot ?? `${sound.key} ${sound.scale}`} · energy ${sound.energy.toFixed(2)} · ${sound.instrumental < 0.5 ? "vocal" : "instrumental"}`,
    s.verdicts.length ? `Your verdict: ${VERDICTS[s.verdicts[0].verdict] ?? s.verdicts[0].verdict} (${day(s.verdicts[0].given_at)})` : "",
    s.favourites.length ? `Favourite on ${s.favourites.join(" and ")}` : "",
    s.recommendations.suggested ? `Suggested ${s.recommendations.suggested}×, played after ${s.recommendations.played}` : "",
  ].filter(Boolean) as string[];
  return (
    <>
      <Head side={side}><div class="who grow">
        <Sigil track={asTrack(s)} size={44} />
        <div class="name"><h1 class={full ? "big" : ""}>{s.name}</h1>
          <p class="meta"><button class="link-text" onClick={() => open("artist", { artist: String(s.artist_id) })}>{s.artist}</button>
            {[s.album, s.released?.slice(0, 4)].filter(Boolean).map((x) => ` · ${x}`).join("")}</p></div>
      </div></Head>
      <Tiles items={[[fmt(s.plays), "plays"], [month(s.first_played), "first heard"], [month(s.last_played), "last played"]]} />
      <MonthChart months={s.months} full={full} />
      <Chips items={[...(s.context ? [{ label: s.context.name, color: contextColor(s.context.hue) }] : []), ...s.styles.slice(0, full ? 6 : 3).map((x) => ({ label: x }))]} />
      {facts.length > 0 && <ul class="facts">{facts.slice(0, full ? 4 : 2).map((f) => <li key={f}>{f}</li>)}</ul>}
      {full && <HourChart hours={s.hours} />}
      {full && <Ranks caption="Where" items={s.countries} />}
      {full && s.lists.length > 0 && <figure><figcaption>In your lists</figcaption><Chips items={s.lists.map((l) => ({ label: l.title }))} /></figure>}
    </>
  );
}

function ArtistCard({ s, full, open, side }: CardProps<ArtistStory>) {
  const who = [s.kind, s.area || s.country, s.active_from ? `since ${s.active_from}` : ""].filter(Boolean).join(" · ");
  const top = s.top_tracks.slice(0, full ? 15 : 3);
  return (
    <>
      <Head side={side}><div class="name"><h1 class={full ? "big" : ""}>{s.name}</h1>{who && <p class="meta">{who}</p>}</div></Head>
      <Tiles items={[[fmt(s.plays), "plays"], [`${s.tracks_played} of ${s.tracks}`, "tracks played"], [month(s.first_played), "first heard"]]} />
      <MonthChart months={s.months} full={full} />
      {top.length > 0 && (
        <figure><figcaption>Played most</figcaption>
          <Bars bars={top.map((t) => ({ label: t.title, value: t.plays, detail: `${t.plays}`, onClick: () => open("track_story", { track_id: t.track_id }) }))} /></figure>
      )}
      <Chips items={s.styles.slice(0, full ? 8 : 3).map((x) => ({ label: x.label }))} />
      {full && <Ranks caption="Contexts" items={s.contexts} color={(r) => contextColor(r.hue ?? null)} />}
      {full && <Ranks caption="Labels" items={s.labels} />}
      {full && <HourChart hours={s.hours} />}
      {full && s.similar.length > 0 && (
        <figure><figcaption>Similar artists</figcaption>
          <ul class="chips">{s.similar.map((x) => <li key={x.name}>{x.artist_id
            ? <button class="link-text" onClick={() => open("artist", { artist: String(x.artist_id) })}>{x.name} <span class="faint">{x.plays}</span></button>
            : <span class="faint">{x.name}</span>}</li>)}</ul></figure>
      )}
      {full && s.releases.length > 0 && <figure><figcaption>Recent releases</figcaption>
        <ul class="facts">{s.releases.map((r) => <li key={r.title}>{r.title} <span class="faint">· {r.kind.toLowerCase()} · {day(r.date)}{r.state === "saved" ? " · saved" : ""}</span></li>)}</ul></figure>}
    </>
  );
}

function Root() {
  const { data: s, setData, full, expand, collapse, call, busy, note } = useHost();
  const [back, setBack] = useState<Story[]>([]);
  if (!s) return <p class="note pad">Looking it up…</p>;
  const open = (tool: string, args: Record<string, unknown>) => { setBack([...back, s]); call(tool, args); };
  const side = !full && <Expand onClick={expand} />;
  const card = s.view === "track" ? <TrackCard s={s} full={full} open={open} side={side} /> : <ArtistCard s={s} full={full} open={open} side={side} />;
  const backButton = back.length > 0 && <button class="back" disabled={busy} onClick={goBack}>‹ {back[back.length - 1].name}</button>;
  function goBack() { const previous = back[back.length - 1]; setBack(back.slice(0, -1)); setData(previous); }
  const tidal = s.view === "track" && s.tidal_url;
  const actions = tidal && <div class="actions"><button class="primary" onClick={() => app.openLink({ url: s.tidal_url! })}>Open in Tidal ↗</button></div>;
  if (!full) {
    return (
      <main class="card">
        {backButton}
        {card}
        {actions}
        {note && <p class="note">{note}</p>}
      </main>
    );
  }
  return (
    <main class="full">
      <div class="bar"><span class="faint">audio-brain</span><Close onClick={collapse} /></div>
      <div class="body stack pad-top">{backButton}{card}{actions}{note && <p class="note">{note}</p>}</div>
    </main>
  );
}

render(<Root />, document.getElementById("root")!);
