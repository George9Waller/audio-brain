// The charts the stats and story components draw, in plain SVG: ranked bars, columns, lines with a crosshair,
// sparklines and an hour × weekday heatmap. Following the dataviz method: one series is one colour; series take the
// categorical slots in a fixed order that follows the entity, never its rank; a legend for two or more; thin marks,
// 4px rounded data ends, a 2px gap between bars, hairline grid; a tooltip on every mark.
import type { ComponentChildren } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";

export const SLOTS = 6;  // categorical slots drawn; more series fold into the table
// never cycled: a seventh series has no colour of its own
export const slot = (n: number) => (n < SLOTS ? `var(--s${n + 1})` : "var(--faint)");

export function fmt(value: number) {
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString("en-GB");
  if (Math.abs(value) >= 100) return String(Math.round(value));
  return String(Math.round(value * 10) / 10);
}
export const pct = (share: number) => `${(share * 100).toFixed(share < 0.1 ? 1 : 0)}%`;

/** The width the chart has, kept up to date. */
export function useWidth<T extends HTMLElement>(): [preact.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    setWidth(ref.current.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

type Tip = { x: number; y: number; body: ComponentChildren } | null;

/** A chart's frame: holds the tooltip, positioned over the plot and kept inside it. */
function Frame({ tip, width, children, height }: { tip: Tip; width: number; height: number; children: ComponentChildren }) {
  const left = tip ? Math.min(Math.max(tip.x - 80, 0), Math.max(0, width - 160)) : 0;
  return (
    <div class="frame" style={{ height }}>
      {children}
      {tip && <div class="tip" role="status" style={{ left, top: Math.max(0, tip.y - 8), transform: "translateY(-100%)" }}>{tip.body}</div>}
    </div>
  );
}

/** Ticks for a 0..max axis: a few round numbers. */
function ticks(max: number, count = 4) {
  if (max <= 0) return [0];
  const raw = max / count, step = 10 ** Math.floor(Math.log10(raw));
  const nice = [1, 2, 2.5, 5, 10].map((m) => m * step).find((s) => s >= raw) ?? raw;
  return Array.from({ length: Math.floor(max / nice) + 1 }, (_, i) => i * nice);
}

export type Bar = { label: string; value: number; detail: string; color?: string; onClick?: () => void };

/** Ranked horizontal bars, one row each: the label and value above a thin bar from the left. */
export function Bars({ bars, max }: { bars: Bar[]; max?: number }) {
  const top = max ?? Math.max(...bars.map((b) => b.value), 0);
  return (
    <ol class="bars">
      {bars.map((b, i) => {
        const body = (
          <>
            <span class="bar-line"><span class="bar-label">{b.label}</span><span class="bar-value number">{b.detail}</span></span>
            <span class="bar-track"><i style={{ width: `${top ? Math.max(1, (b.value / top) * 100) : 0}%`, background: b.color ?? slot(0) }} /></span>
          </>
        );
        return <li key={i}>{b.onClick ? <button class="bar-row" onClick={b.onClick}>{body}</button> : <div class="bar-row">{body}</div>}</li>;
      })}
    </ol>
  );
}

/** Vertical columns along an ordered axis (hours, weekdays, months), one series. */
export function Columns({ values, labels, tick, height = 120, color, detail }: {
  values: number[]; labels: string[]; tick: (i: number) => string | null; height?: number; color?: string;
  detail: (i: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const axis = 18, plot = height - axis, n = values.length;
  const max = Math.max(...values, 0) || 1;
  const step = width / Math.max(n, 1), gap = n > 40 ? 1 : 2, w = Math.max(1, step - gap);
  const at = (e: PointerEvent) => setHover(Math.min(n - 1, Math.max(0, Math.floor(e.offsetX / step))));
  const tip: Tip = hover == null ? null
    : { x: hover * step + step / 2, y: plot - (values[hover] / max) * (plot - 4), body: <><b>{labels[hover]}</b><span>{detail(hover)}</span></> };
  return (
    <div ref={ref}>
      {width > 0 && (
        <Frame tip={tip} width={width} height={height}>
          <svg width={width} height={height} onPointerMove={at} onPointerDown={at} onPointerLeave={() => setHover(null)} role="img" aria-label="Column chart">
            <line x1="0" x2={width} y1={plot + 0.5} y2={plot + 0.5} stroke="var(--axis)" />
            {values.map((v, i) => {
              const h = v > 0 ? Math.max(1.5, (v / max) * (plot - 4)) : 0;
              const r = Math.min(4, w / 2, h);
              const x = i * step + gap / 2, y = plot - h;
              // rounded at the data end only, square on the baseline
              return h > 0 && <path key={i} d={`M${x} ${plot}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${plot}Z`}
                fill={color ?? slot(0)} opacity={hover != null && hover !== i ? 0.55 : 1} />;
            })}
            {values.map((_, i) => tick(i) && <text key={`t${i}`} x={i * step + step / 2} y={height - 4} class="tick" text-anchor="middle">{tick(i)}</text>)}
          </svg>
        </Frame>
      )}
    </div>
  );
}

export type Series = { key: string; label: string; values: number[]; color: string };

/** Lines over time on one axis, with a crosshair that lists every series at the period under the finger. */
export function Lines({ series, periods, label, height = 180, format = fmt, max: fixed, min = 0 }: {
  series: Series[]; periods: string[]; label: (i: number) => string; height?: number; format?: (v: number) => string; max?: number;
  min?: number;  // a baseline above zero, for a measure like tempo that never nears it
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const axis = 18, left = 34, plot = height - axis - 6, n = periods.length;
  const max = fixed ?? (Math.max(...series.flatMap((s) => s.values), 0) || 1);
  const grid = fixed === 1 ? [0, 0.5, 1] : ticks(max - min).map((t) => t + min);
  const top = Math.max(max, grid[grid.length - 1]);
  const x = (i: number) => left + (n > 1 ? (i * (width - left - 8)) / (n - 1) : (width - left) / 2);
  const y = (v: number) => 6 + plot - ((v - min) / (top - min || 1)) * plot;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((width - left) / 56))));
  const move = (e: PointerEvent) => {
    const i = Math.round(((e.offsetX - left) / Math.max(1, width - left - 8)) * (n - 1));
    setAt(Math.min(n - 1, Math.max(0, i)));
  };
  const tip: Tip = at == null ? null : {
    x: x(at), y: 6, body: <><b>{label(at)}</b>{[...series].sort((a, b) => b.values[at] - a.values[at]).map((s) => (
      <span key={s.key} class="tip-row"><i class="swatch" style={{ background: s.color }} />{s.label}<span class="number">{format(s.values[at])}</span></span>))}</>,
  };
  return (
    <div ref={ref}>
      {width > 0 && (
        <Frame tip={tip} width={width} height={height}>
          <svg width={width} height={height} onPointerMove={move} onPointerDown={move} onPointerLeave={() => setAt(null)} role="img" aria-label="Line chart over time">
            {grid.map((g) => <g key={g}><line x1={left} x2={width} y1={y(g) + 0.5} y2={y(g) + 0.5} stroke={g !== min ? "var(--grid)" : "var(--axis)"} />
              <text x={left - 6} y={y(g) + 4} class="tick" text-anchor="end">{format(g)}</text></g>)}
            {periods.map((_, i) => i % every === 0 && <text key={i} x={x(i)} y={height - 4} class="tick" text-anchor="middle">{label(i)}</text>)}
            {series.map((s) => (
              <path key={s.key} d={s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("")}
                fill="none" stroke={s.color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
            ))}
            {at != null && <line x1={x(at)} x2={x(at)} y1={6} y2={6 + plot} stroke="var(--axis)" />}
            {at != null && series.map((s) => <circle key={s.key} cx={x(at)} cy={y(s.values[at])} r="4" fill={s.color} stroke="var(--bg)" stroke-width="2" />)}
          </svg>
        </Frame>
      )}
    </div>
  );
}

/** A small line without axes, for a row in a list of series; its peak is marked. */
export function Sparkline({ values, color, width = 120, height = 28 }: { values: number[]; color: string; width?: number; height?: number }) {
  const max = Math.max(...values, 0) || 1, n = values.length;
  const x = (i: number) => (n > 1 ? (i * (width - 4)) / (n - 1) + 2 : width / 2);
  const y = (v: number) => height - 3 - (v / max) * (height - 6);
  const peak = values.indexOf(Math.max(...values));
  return (
    <svg width={width} height={height} aria-hidden="true">
      <path d={values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("")} fill="none" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
      {peak >= 0 && <circle cx={x(peak)} cy={y(values[peak])} r="2.5" fill={color} />}
    </svg>
  );
}

/** Hour × weekday: one cell per hour of each day, darker where more was played. */
export function Heatmap({ cells, detail }: { cells: number[][]; detail: (day: number, hour: number) => string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], left = 32, axis = 16, gap = 2;
  const cell = Math.max(6, (width - left) / 24), h = Math.min(cell, 22), height = days.length * h + axis;
  const max = Math.max(...cells.flat(), 0) || 1;
  return (
    <div ref={ref}>
      {width > 0 && (
        <Frame tip={tip} width={width} height={height}>
          <svg width={width} height={height} onPointerLeave={() => setTip(null)} role="img" aria-label="Plays by hour and weekday">
            {days.map((d, r) => <text key={d} x={0} y={r * h + h / 2 + 4} class="tick">{d}</text>)}
            {cells.map((row, r) => row.map((v, c) => (
              <rect key={`${r}-${c}`} x={left + c * cell} y={r * h} width={cell - gap} height={h - gap} rx="2"
                fill={v ? `color-mix(in oklab, var(--seq-high) ${Math.round(12 + (v / max) * 88)}%, var(--seq-low))` : "var(--empty)"}
                onPointerEnter={() => setTip({ x: left + c * cell + cell / 2, y: r * h, body: <><b>{days[r]} {String(c).padStart(2, "0")}:00</b><span>{detail(r, c)}</span></> })}
                onPointerDown={() => setTip({ x: left + c * cell + cell / 2, y: r * h, body: <><b>{days[r]} {String(c).padStart(2, "0")}:00</b><span>{detail(r, c)}</span></> })} />
            )))}
            {[0, 6, 12, 18].map((hour) => <text key={hour} x={left + hour * cell} y={height - 3} class="tick">{String(hour).padStart(2, "0")}</text>)}
          </svg>
        </Frame>
      )}
    </div>
  );
}

/** Low to high, for the heatmap. */
export function ScaleKey({ low, high }: { low: string; high: string }) {
  return <div class="scale-key"><span>{low}</span><i /><span>{high}</span></div>;
}

/** The colour key for two or more series; tapping one picks it out. */
export function Legend({ series, picked, pick }: { series: Series[]; picked: string | null; pick: (key: string | null) => void }) {
  if (series.length < 2) return null;
  return (
    <ul class="legend">
      {series.map((s) => (
        <li key={s.key}>
          <button class={picked && picked !== s.key ? "faded" : ""} aria-pressed={picked === s.key} onClick={() => pick(picked === s.key ? null : s.key)}>
            <span class="swatch" style={{ background: s.color }} />{s.label}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Keeps each series' colour for as long as the component is open, so re-ranking never repaints one. */
export function useColours() {
  const order = useRef<string[]>([]);
  return (key: string) => {
    if (!order.current.includes(key)) order.current.push(key);
    return slot(order.current.indexOf(key));
  };
}
