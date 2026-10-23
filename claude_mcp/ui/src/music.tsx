// The app's music marks, ported from android/core/design/.../Music.kt: the sound sigil, the colour strip, the energy
// arc and the key path. Context colours follow Theme.kt (contextColor), so a context looks the same here as in the app;
// everything else takes the host's colours.
import type { Track } from "./list";

/** A context's colour from its hue; grey for a track outside your contexts. */
export const contextColor = (hue: number | null) => (hue == null ? "var(--faint)" : `hsl(${((hue % 360) + 360) % 360} 58% 62%)`);

/** The sigil's colour: its context's, shifted a little round the Camelot wheel so keys that mix look related. */
function sigilColor(hue: number | null, camelot: string | null) {
  const key = camelot ? parseInt(camelot, 10) : NaN;
  if (hue == null || isNaN(key)) return contextColor(hue);
  const h = (((hue + ((key - 6.5) / 5.5) * 16) % 360) + 360) % 360;
  return `hsl(${h} 58% ${camelot!.endsWith("A") ? 59 : 66}%)`;
}

/** A stable stand-in for a track that hasn't been analysed: same names, same glyph, drawn fainter. */
function fromSeed(seed: string) {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  const next = () => { h = Math.imul(h, 1103515245) + 12345; return ((h >>> 8) % 1000) / 1000; };
  return { moods: Array.from({ length: 6 }, () => 0.25 + next() * 0.5), energy: 0.3 + next() * 0.4, bpm: 90 + next() * 50 };
}

/**
 * The glyph for a track's sound: lobes for its moods, a ring for energy, ticks for tempo, in its context's colour.
 * Minor keys are hollow, the key turns the shape, and a dot in the middle means vocals.
 */
export function Sigil({ track, size = 30 }: { track: Track; size?: number }) {
  const measured = track.moods != null && track.bpm != null && track.energy != null;
  const spec = measured ? { moods: track.moods!, energy: track.energy!, bpm: track.bpm! } : fromSeed(`${track.artist}|${track.title}`);
  const camelot = measured ? track.camelot : null;
  const color = sigilColor(track.hue, camelot);
  const key = camelot ? parseInt(camelot, 10) : NaN;
  const minor = !isNaN(key) && camelot!.endsWith("A");
  const c = 22, outer = 22;  // drawn on a 44 grid, as the app's are, and scaled
  const ticks = Math.min(20, Math.max(6, Math.round(spec.bpm / 10)));
  const polar = (a: number, r: number) => [c + Math.cos(a) * r, c + Math.sin(a) * r];
  const turn = isNaN(key) ? 0 : ((key - 1) * Math.PI) / 6;
  const points = spec.moods.map((m, i) => polar((2 * Math.PI * i) / 6 - Math.PI / 2 + turn, outer * (0.17 + 0.44 * Math.min(1, Math.max(0, m)))));
  const mid = (a: number[], b: number[]) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const start = mid(points[5], points[0]);
  const d = `M${start.join(" ")}` + points.map((p, i) => ` Q${p.join(" ")} ${mid(p, points[(i + 1) % 6]).join(" ")}`).join("") + "Z";
  return (
    <svg class="sigil" width={size} height={size} viewBox="0 0 44 44" style={{ opacity: measured ? 1 : 0.45 }} aria-hidden="true">
      {Array.from({ length: ticks }, (_, i) => {
        const a = (2 * Math.PI * i) / ticks - Math.PI / 2;
        const [x1, y1] = polar(a, outer * 0.86), [x2, y2] = polar(a, outer);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} stroke-opacity="0.5" stroke-width="1.2" stroke-linecap="round" />;
      })}
      <circle cx={c} cy={c} r={outer * 0.72} fill="none" stroke={color} stroke-opacity="0.9" stroke-width={0.8 + spec.energy * 3.2} />
      <path d={d} fill={minor ? "none" : color} stroke={minor ? color : "none"} stroke-width="1.8" stroke-linejoin="round" />
      {measured && track.vocal && <circle cx={c} cy={c} r={Math.max(1.6, outer * 0.075)} fill={minor ? color : "var(--bg)"} />}
    </svg>
  );
}

/** What a list is made of: one flat segment per track, in order, in its context's colour (grey when it has none). */
export function ColourStrip({ hues, tall }: { hues: (number | null)[]; tall?: boolean }) {
  return (
    <span class={tall ? "strip tall" : "strip"} aria-hidden="true">
      {hues.map((h, i) => <i key={i} style={{ background: h == null ? "color-mix(in srgb, var(--faint) 50%, transparent)" : contextColor(h) }} />)}
    </span>
  );
}

/** Energy across the list in play order, as a line over a faint fill. */
export function EnergyArc({ energies }: { energies: number[] }) {
  const w = 300, h = 44, n = energies.length;
  const pts = energies.map((e, i) => [(i * w) / (n - 1), h - 3 - Math.min(1, Math.max(0, e)) * (h - 8)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
  return (
    <svg class="arc" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-label="Energy across the list">
      <line x1="0" y1={h - 0.5} x2={w} y2={h - 0.5} stroke="var(--line)" vector-effect="non-scaling-stroke" />
      <path d={`${line}L${w} ${h}L0 ${h}Z`} fill="var(--text)" fill-opacity="0.07" />
      <path d={line} fill="none" stroke="var(--text)" stroke-width="1.4" vector-effect="non-scaling-stroke" stroke-linejoin="round" />
    </svg>
  );
}

/** Whether two Camelot keys mix: the same key, a neighbour on the wheel in the same letter. */
function compatible(a: string, b: string) {
  const na = parseInt(a, 10), nb = parseInt(b, 10);
  if (isNaN(na) || isNaN(nb)) return true;
  const d = Math.min((na - nb + 12) % 12, (nb - na + 12) % 12);
  return na === nb || (a.slice(-1) === b.slice(-1) && d <= 1);
}

/** The key path in Camelot notation, clashes between neighbours in the No colour. */
export function KeyPath({ keys }: { keys: string[] }) {
  return (
    <div class="keys number">
      {keys.map((k, i) => (
        <span key={i}>{i > 0 && <span class="faint"> → </span>}<span class={i > 0 && !compatible(keys[i - 1], k) ? "clash" : "faint"}>{k}</span></span>
      ))}
    </div>
  );
}

/** The artist summary, with the leading artists' names in bold. */
export function Artists({ text, names }: { text: string; names: string[] }) {
  const parts: (string | preact.JSX.Element)[] = [];
  let rest = text;
  for (const name of names) {
    const at = rest.indexOf(name);
    if (at < 0) continue;
    parts.push(rest.slice(0, at), <b key={name}>{name}</b>);
    rest = rest.slice(at + name.length);
  }
  return <p class="artists">{parts}{rest}</p>;
}
