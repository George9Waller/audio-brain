import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { list, lists, played, releases, stats, story } from "./fixtures";

const params = new URLSearchParams(location.search);
const theme = params.get("theme") === "dark" ? "dark" : "light";
const page = params.get("page") ?? "list";  // list, chart or story
const view = params.get("view") ?? (page === "chart" ? "lines" : page === "story" ? "track" : "index");
document.body.classList.toggle("dark", theme === "dark");
document.querySelector<HTMLElement>(".chat")!.style.maxWidth = `${params.get("width") ?? 720}px`;

// roughly Claude's own colours, sent as the host's style variables
const variables = theme === "dark"
  ? { "--color-background-primary": "#30302e", "--color-background-secondary": "#262624", "--color-text-primary": "#faf9f5",
      "--color-text-secondary": "#c2c0b6", "--color-border-tertiary": "#faf9f51f", "--font-sans": "ui-sans-serif, system-ui, sans-serif" }
  : { "--color-background-primary": "#ffffff", "--color-background-secondary": "#f5f4ed", "--color-text-primary": "#141413",
      "--color-text-secondary": "#5e5d59", "--color-border-tertiary": "#1f1e1d1f", "--font-sans": "ui-sans-serif, system-ui, sans-serif" };

if (params.get("tall")) document.getElementById("app")!.style.minHeight = "700px";  // to see past a stuck size
const log = (line: string) => { document.getElementById("log")!.textContent += line + "\n"; };
const iframe = document.getElementById("app") as HTMLIFrameElement;
const result = (data: object) => ({ content: [{ type: "text" as const, text: "(summary)" }], structuredContent: data as Record<string, unknown> });
const byKey: Record<string, object> = { "explore:late": list, releases };
let mode: "inline" | "fullscreen" = params.get("mode") === "fullscreen" ? "fullscreen" : "inline";
document.body.classList.toggle("fullscreen", mode === "fullscreen");

const bridge = new AppBridge(null, { name: "harness", version: "1" }, { openLinks: {}, serverTools: {} },
  { hostContext: { theme, styles: { variables }, displayMode: mode, availableDisplayModes: ["inline", "fullscreen"],
    platform: params.get("width") === "390" ? "mobile" : "web" } as never });
// full screen as a host does it: the component fills the window, and is told so
bridge.onrequestdisplaymode = async ({ mode: wanted }) => {
  mode = wanted === "fullscreen" ? "fullscreen" : "inline";
  document.body.classList.toggle("fullscreen", mode === "fullscreen");
  log(`requestDisplayMode ${mode}`);
  bridge.sendHostContextChange({ displayMode: mode });
  return { mode };
};
bridge.oncalltool = async ({ name, arguments: args }) => {
  log(`callServerTool ${name} ${JSON.stringify(args)}`);
  if (name === "keep") return result({ key: args?.key, kept: true });
  if (name === "stats") {
    const shown = stats[view] as { measure: string };
    return result({ ...shown, measure: args?.measure ?? shown.measure });
  }
  if (name === "track_story") return result(story.track);
  if (name === "artist") return result(story.artist);
  if (name === "play") return result(played);
  return result(byKey[String(args?.key)] ?? list);
};
bridge.onmessage = async (params) => { log(`sendMessage: ${JSON.stringify(params.content)}`); return {}; };
bridge.onopenlink = async ({ url }) => { log(`openLink ${url}`); return {}; };
// the component's errors, in the log under it (same origin in dev)
iframe.addEventListener("load", () => {
  iframe.contentWindow!.addEventListener("error", (e) => log(`error: ${e.message}`));
  iframe.contentWindow!.addEventListener("unhandledrejection", (e) => log(`rejected: ${e.reason}`));
});
bridge.onsizechange = ({ height }) => { log(`size ${height}`); if (height && mode === "inline") iframe.style.height = `${height}px`; };
// ?local=1 draws harness/local/<view>.json (real results, git-ignored) instead of the samples
async function initial() {
  if (params.get("local")) return (await fetch(`/harness/local/${view}.json`)).json();
  if (page === "chart") return stats[view];
  if (page === "story") return story[view];
  return view === "index" ? lists : view === "releases" ? releases : list;
}
bridge.oninitialized = async () => { bridge.sendToolResult(result(await initial())); };
// listen before loading the component: it says hello as soon as its script runs, before the iframe's load event
bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!)).then(() => { iframe.src = `/${page}.html`; });
