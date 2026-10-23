// The list component: every list (tap one to open it), or one list's tracks with a Play button. Play writes the
// list to TIDAL "Now", then asks Claude to play the Sonos favourite, since a component can't call another connector.
import { render } from "preact";
import { useEffect, useState } from "preact/hooks";
import { App, applyDocumentTheme, applyHostFonts, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import type { McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import "./style.css";

type Track = {
  track_id: number; artist: string; title: string; reason: string; novel: boolean; hue: number | null;
  styles: string[]; bpm: number | null; camelot: string | null; energy: number | null;
};
type Index = { view: "index"; lists: { key: string; title: string; subtitle: string; tracks: number; hue: number | null }[] };
type List = { view: "list"; key: string; title: string; subtitle: string; hue: number | null; tracks: Track[] };
type Data = Index | List;

const app = new App({ name: "audio-brain list", version: "0.1.0" });
let setData: (data: Data) => void = () => {};
let pending: Data | null = null;

function applyContext(context: McpUiHostContext | undefined) {
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context?.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
}

// handlers go on before connect(), so the first tool result isn't missed
app.ontoolresult = (result) => {
  const data = result.structuredContent as Data | undefined;
  if (!data) return;
  pending = data;
  setData(data);
};
app.onhostcontextchanged = applyContext;

const dot = (hue: number | null) => (hue == null ? undefined : { background: `oklch(0.68 0.13 ${hue})` });

function Root() {
  const [data, set] = useState<Data | null>(pending);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => { setData = set; }, []);

  async function open(key: string) {
    setBusy(true);
    try {
      const result = await app.callServerTool({ name: "show_list", arguments: { key } });
      if (result.structuredContent) set(result.structuredContent as Data);
    } finally { setBusy(false); }
  }

  async function play(list: List) {
    setBusy(true); setNote("");
    try {
      const result = await app.callServerTool({ name: "play", arguments: { key: list.key } });
      if (result.isError) { setNote("Couldn't write it to TIDAL."); return; }
      setNote(`Written to TIDAL “Now”. Asking Claude to play it…`);
      await app.sendMessage({ role: "user", content: [{ type: "text",
        text: `I've written “${list.title}” to the TIDAL playlist “Now”. Play the Sonos favourite “Audio Brain · Now” — ask me which room if you don't know.` }] });
    } finally { setBusy(false); }
  }

  async function tidal(list: List) {
    const result = await app.callServerTool({ name: "play", arguments: { key: list.key } });
    const url = (result.structuredContent as { tidal_url?: string } | undefined)?.tidal_url;
    if (url) await app.openLink({ url });
  }

  if (!data) return <p class="muted pad">Loading…</p>;

  if (data.view === "index") return (
    <main>
      <header><h1>Your lists</h1><p class="muted">{data.lists.length} lists</p></header>
      <ul class="rows">
        {data.lists.map((l) => (
          <li key={l.key}>
            <button class="row" disabled={busy} onClick={() => open(l.key)}>
              <span class="dot" style={dot(l.hue)} />
              <span class="text"><b>{l.title}</b><span class="muted">{l.subtitle}</span></span>
              <span class="chip">{l.tracks}</span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );

  return (
    <main>
      <header>
        <h1><span class="dot" style={dot(data.hue)} />{data.title}</h1>
        <p class="muted">{data.subtitle} · {data.tracks.length} tracks</p>
        <div class="actions">
          <button class="primary" disabled={busy} onClick={() => play(data)}>Play on Sonos</button>
          <button disabled={busy} onClick={() => tidal(data)}>Open in TIDAL</button>
        </div>
        {note && <p class="note">{note}</p>}
      </header>
      <ol class="rows">
        {data.tracks.map((t, n) => (
          <li key={t.track_id} class="track">
            <span class="num">{n + 1}</span>
            <span class="dot" style={dot(t.hue)} />
            <span class="text">
              <span><b>{t.artist}</b> – {t.title}{t.novel && <span class="badge">new</span>}</span>
              <span class="chips">
                {t.bpm != null && <span class="chip">{Math.round(t.bpm)} BPM</span>}
                {t.camelot && <span class="chip">{t.camelot}</span>}
                {t.energy != null && <span class="chip">energy {Math.round(t.energy * 100)}</span>}
                {t.styles.slice(0, 2).map((s) => <span class="chip quiet" key={s}>{s}</span>)}
              </span>
              {t.reason && <span class="muted small">{t.reason}</span>}
            </span>
          </li>
        ))}
      </ol>
    </main>
  );
}

render(<Root />, document.getElementById("root")!);
app.connect().then(() => applyContext(app.getHostContext()));
