// The draft editor: a mix Claude made, to shape by taps or by words. Inline it's the compact card every list gets
// (MCP.md's style brief): the name, its size, the colour strip, who's in it, and Open in Tidal, Keep it and Play on
// Sonos on one line, with Undo by the note once something's changed. Expanded it's the editor: steer chips (more
// energy, calmer, fewer vocals, more new, darker) and every track, its sigil first, with lock, swap and remove. Each
// tap calls refine_mix and tells Claude what changed (updateModelContext), so the conversation keeps up.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import "./style.css";
import { connect } from "./bridge";
import { Close, Expand } from "./controls";
import { Artists, ColourStrip, Sigil } from "./music";
import type { Track } from "./list";

type Request = { sound?: string; vocals?: string; energy?: string; familiarity?: string; styles?: string[]; context_id?: number | null };
type Draft = {
  view: "draft"; key: string; kind: string; title: string; subtitle: string; understood: string; count: string;
  duration: string | null; new: number; novel: number | null; artists: string | null; top_artists: string[]; energy: string | null;
  tracks: Track[]; tidal_url: string | null; kept: boolean; request: Request | null; exploration: number | null;
  versions: number; done: string[];
};
type Op = Record<string, unknown> & { op: string };

const { app, useHost } = connect<Draft>("audio-brain draft");
const SONOS = "Audio Brain · Now";

/** The steer chips, each a change to the request as it stands. */
function chips(draft: Draft): { label: string; op: Op; on?: boolean }[] {
  const request = draft.request ?? {};
  const exploration = draft.exploration ?? draft.novel ?? 0.3;
  return [
    { label: "More energy", op: { op: "steer", changes: { energy: "up" } }, on: request.energy === "high" },
    { label: "Calmer", op: { op: "steer", changes: { energy: "down" } }, on: request.energy === "low" },
    { label: "Fewer vocals", op: { op: "steer", changes: { vocals: "instrumental" } }, on: request.vocals === "instrumental" },
    { label: "More new", op: { op: "steer", exploration: Math.min(1, Math.round((exploration + 0.25) * 100) / 100) }, on: exploration >= 1 },
    { label: "Darker", op: { op: "steer", text: "darker, moodier" } },
  ];
}

const Icon = ({ d, label, on, onClick, disabled }: { d: string; label: string; on?: boolean; onClick: () => void; disabled?: boolean }) => (
  <button class={on ? "tool on" : "tool"} aria-label={label} aria-pressed={on} title={label} disabled={disabled} onClick={onClick}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d={d} /></svg>
  </button>
);
const LOCK = "M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z";
const UNLOCK = "M7 11V8a5 5 0 0 1 9.6-2M5 11h14v10H5z";
const SWAP = "M4 7h13l-3-3M20 17H7l3 3";
const REMOVE = "M6 6l12 12M18 6L6 18";

function Row({ track, busy, change }: { track: Track; busy: boolean; change: (op: Op) => void }) {
  const why = [track.artist, track.artist_new ? "new artist" : track.novel ? "new" : "", track.reason].filter(Boolean).join(" · ");
  const ids = { track_ids: [track.track_id] };
  return (
    <li class={track.locked ? "row edit locked" : "row edit"}>
      <Sigil track={track} size={32} />
      <span class="text"><span class="label">{track.title}</span><span class="caption">{why}</span></span>
      <span class="tools">
        <Icon d={track.locked ? LOCK : UNLOCK} label={track.locked ? "Unlock" : "Lock"} on={track.locked} disabled={busy}
              onClick={() => change({ op: track.locked ? "unlock" : "lock", ...ids })} />
        <Icon d={SWAP} label="Swap for something close" disabled={busy || track.locked} onClick={() => change({ op: "swap", track_id: track.track_id })} />
        <Icon d={REMOVE} label="Remove" disabled={busy} onClick={() => change({ op: "remove", ...ids })} />
      </span>
    </li>
  );
}

function Root() {
  const { data, setData, full, expand, collapse } = useHost();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [kept, setKept] = useState(false);
  useEffect(() => { setKept(!!data?.kept); }, [data?.key]);
  useEffect(() => { if (data?.done?.length) setNote(data.done.join(". ") + "."); }, [data]);
  if (!data) return <p class="note pad">Making the mix…</p>;
  const draft = data;

  async function run(work: () => Promise<void>) {
    setBusy(true); setNote("");
    try { await work(); } catch { setNote("That didn't work. Ask Claude to try again."); } finally { setBusy(false); }
  }
  /** Refine the draft, show the result, and tell Claude what the user did. */
  const change = (op: Op) => run(async () => {
    const result = await app.callServerTool({ name: "refine_mix", arguments: { key: draft.key, operations: [op] } });
    const text = result.content?.find((c) => c.type === "text")?.text ?? "";
    if (result.isError || !result.structuredContent) { setNote(text.replace(/^audio-brain said \d+: /, "") || "That didn't work."); return; }
    setData(result.structuredContent as unknown as Draft);
    try {
      await app.updateModelContext({ content: [{ type: "text", text: `The user changed draft ${draft.key} in the editor. It is now:\n\n${text}` }] });
    } catch { /* the host doesn't keep model context; the draft itself is up to date */ }
  });
  const tidal = () => run(async () => {
    const played = (await app.callServerTool({ name: "play", arguments: { key: draft.key } })).structuredContent as { tidal_url?: string } | undefined;
    if (played?.tidal_url) await app.openLink({ url: played.tidal_url }); else setNote("Couldn't reach Tidal.");
  });
  const keep = () => run(async () => {
    const result = await app.callServerTool({ name: "keep", arguments: { key: draft.key } });
    if (result.isError) setNote("Couldn't keep it."); else { setKept(true); setNote("Kept in your lists."); }
  });
  const sonos = () => run(async () => {
    const result = await app.callServerTool({ name: "play", arguments: { key: draft.key } });
    if (result.isError) { setNote("Couldn't write it to Tidal."); return; }
    setNote("Asking Claude to play it…");
    await app.sendMessage({ role: "user", content: [{ type: "text",
      text: `I've written “${draft.title}” to the TIDAL playlist “Now”. Play the Sonos favourite “${SONOS}” — ask me which room if you don't know.` }] });
  });

  const meta = [draft.count, draft.duration, draft.novel != null ? `${Math.round(draft.novel * 100)}% new to you` : ""].filter(Boolean).join(" · ");
  const locked = draft.tracks.filter((t) => t.locked).length;
  const empty = !draft.tracks.length;
  const steer = (
    <div class="chips-row" role="group" aria-label="Steer the mix">
      {chips(draft).map((c) => (
        <button key={c.label} class={c.on ? "chip on" : "chip"} aria-pressed={!!c.on} disabled={busy}
                onClick={() => !c.on && change(c.op)}>{c.on ? `✓ ${c.label}` : c.label}</button>
      ))}
    </div>
  );
  const keepButton = <button class="secondary" disabled={busy || kept || empty} onClick={keep}>{kept ? "Kept ✓" : "Keep it"}</button>;
  const sonosButton = <button class="tertiary" disabled={busy || empty} onClick={sonos}>Play on Sonos</button>;
  const tidalButton = <button class="primary" disabled={busy || empty} onClick={tidal}>Open in Tidal ↗</button>;
  const undo = draft.versions > 0 && <button class="undo" disabled={busy} onClick={() => change({ op: "undo" })}>Undo</button>;
  const said = busy ? "Working…" : [note, locked ? `${locked} locked: steering keeps them.` : ""].filter(Boolean).join(" ");
  const status = (said || undo) && <div class="status"><p class="note" aria-live="polite">{said}</p>{undo}</div>;

  if (full) {
    return (
      <main class="full">
        <div class="bar"><span class="faint">audio-brain · draft</span><Close onClick={collapse} /></div>
        <header class="head">
          <h1 class="big">{draft.title}</h1>
          <p class="muted">{meta}{draft.energy ? `. ${draft.energy}` : ""}</p>
          {draft.understood && <p class="caption">Read as: {draft.understood}</p>}
          {!empty && <ColourStrip hues={draft.tracks.map((t) => t.hue)} tall />}
          {steer}
        </header>
        <div class="body"><ol class="rows">{draft.tracks.map((t) => <Row key={t.track_id} track={t} busy={busy} change={change} />)}</ol></div>
        <footer class="foot">
          <div class="actions stacked">{tidalButton}<div class="pair">{keepButton}{sonosButton}</div></div>
          {status}
        </footer>
      </main>
    );
  }
  return (
    <main class="card">
      <div class="top">
        <div class="name"><h1>{draft.title}</h1><p class="meta">{meta}</p></div>
        <Expand onClick={expand} />
      </div>
      {!empty && <ColourStrip hues={draft.tracks.map((t) => t.hue)} />}
      {draft.artists && <Artists text={draft.artists} names={draft.top_artists ?? []} />}
      <div class="actions">{tidalButton}{keepButton}{sonosButton}</div>
      {status}
    </main>
  );
}

render(<Root />, document.getElementById("root")!);
