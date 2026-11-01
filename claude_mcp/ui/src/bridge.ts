// The host connection the chart and story components share: theme and fonts from Claude, the latest tool result,
// full screen, and calling the server's tools from a tap. (The list component keeps its own, from Phase 0.)
import { useEffect, useState } from "preact/hooks";
import { App, applyDocumentTheme, applyHostFonts, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import type { McpUiDisplayMode, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { applyFrame } from "./frame";

export function connect<T>(name: string) {
  const app = new App({ name, version: "0.1.0" });
  let latest: T | null = null;
  const listeners = new Set<(data: T) => void>();
  const modes = new Set<(mode: McpUiDisplayMode) => void>();
  const apply = (context: McpUiHostContext | undefined) => {
    if (context?.theme) applyDocumentTheme(context.theme);
    if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
    if (context?.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
    applyFrame({ ...app.getHostContext(), ...context });
    if (context?.displayMode) modes.forEach((m) => m(context.displayMode!));
  };
  // handlers go on before connect(), so the first tool result isn't missed
  app.ontoolresult = (result) => {
    if (!result.structuredContent) return;
    latest = result.structuredContent as T;
    listeners.forEach((l) => l(latest!));
  };
  app.onhostcontextchanged = apply;
  app.connect().then(() => apply(app.getHostContext()));

  /** The data to draw, the display mode, and ways to change them. */
  function useHost() {
    const [data, setData] = useState<T | null>(latest);
    const [mode, setMode] = useState<McpUiDisplayMode>(app.getHostContext()?.displayMode ?? "inline");
    const [local, setLocal] = useState(false);  // expanded in place, where the host has no full screen
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState("");
    useEffect(() => {
      listeners.add(setData); modes.add(setMode);
      // whatever arrived between the first render and now
      if (latest) setData(latest);
      const shown = app.getHostContext()?.displayMode;
      if (shown) setMode(shown);
      return () => { listeners.delete(setData); modes.delete(setMode); };
    }, []);
    const full = mode === "fullscreen" || local;
    async function expand() {
      if (app.getHostContext()?.availableDisplayModes?.includes("fullscreen")) setMode((await app.requestDisplayMode({ mode: "fullscreen" })).mode);
      else setLocal(true);
    }
    async function collapse() {
      if (mode === "fullscreen") setMode((await app.requestDisplayMode({ mode: "inline" })).mode);
      setLocal(false);
    }
    /** Call a server tool and show what it returns in place of the current view. */
    async function call(name: string, args: Record<string, unknown>) {
      setBusy(true); setNote("");
      try {
        const result = await app.callServerTool({ name, arguments: args });
        if (result.isError || !result.structuredContent) setNote("That didn't work. Ask Claude to try again.");
        else setData(result.structuredContent as T);
      } catch { setNote("That didn't work. Ask Claude to try again."); } finally { setBusy(false); }
    }
    return { data, setData, full, expand, collapse, call, busy, note };
  }
  return { app, useHost };
}
