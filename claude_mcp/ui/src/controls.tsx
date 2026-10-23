// The expand and close buttons the chart and story components share.

export function Expand({ onClick }: { onClick: () => void }) {
  return (
    <button class="icon" aria-label="Expand to full screen" onClick={onClick}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" /></svg>
    </button>
  );
}

export function Close({ onClick }: { onClick: () => void }) {
  return (
    <button class="icon" aria-label="Close" onClick={onClick}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>
    </button>
  );
}
