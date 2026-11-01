// Fitting Claude's full screen, for every component. Claude draws its own header and close button there, and floats
// its message box over the bottom of the frame (on the phone its close button and title float over the top too), so
// in full screen the page takes the frame's height, keeps clear of those, and only its middle scrolls. Claude's
// safe-area insets are used when it sends them; it hasn't been seen to, so these allowances stand in, and the larger
// wins: over-tall beats cut off.
import type { McpUiHostContext } from "@modelcontextprotocol/ext-apps";

const COMPOSER = 140;  // Claude's message box, over the bottom of the frame (desktop and phone)
const PHONE_TOP = 96;  // the phone's status bar, and Claude's close button and title floating under it

// Once the tracks are scrolled, the head folds to what's needed to act (the name, actions, chips and tabs: `.fold`
// goes), so a phone shows more than two rows; it opens again at the top. Apart, so it doesn't flicker at the edge.
const FOLD_AT = 48, OPEN_AT = 4;
document.addEventListener("scroll", (event) => {
  const body = event.target as HTMLElement;
  if (!body.matches?.(".full > .body")) return;
  const root = document.documentElement;
  if (body.scrollTop > FOLD_AT) root.setAttribute("data-folded", "");
  else if (body.scrollTop < OPEN_AT) root.removeAttribute("data-folded");
}, true);

export function applyFrame(context: McpUiHostContext | undefined) {
  if (!context) return;
  const root = document.documentElement;
  const full = context.displayMode === "fullscreen";
  root.toggleAttribute("data-host-full", full);  // the host draws a close button: ours goes
  if (!full) root.removeAttribute("data-folded");
  const box = context.containerDimensions as { height?: number; maxHeight?: number } | undefined;
  const height = box?.height ?? box?.maxHeight;
  if (full && height) root.style.setProperty("--frame-h", `${height}px`); else root.style.removeProperty("--frame-h");
  const inset = context.safeAreaInsets ?? { top: 0, right: 0, bottom: 0, left: 0 };
  const phone = context.platform === "mobile";
  const px = (n: number) => `${Math.round(n)}px`;
  root.style.setProperty("--inset-top", px(full ? Math.max(inset.top, phone ? PHONE_TOP : 0) : 0));
  root.style.setProperty("--inset-bottom", px(full ? Math.max(inset.bottom, COMPOSER) : 0));
  root.style.setProperty("--inset-left", px(full ? inset.left : 0));
  root.style.setProperty("--inset-right", px(full ? inset.right : 0));
}
