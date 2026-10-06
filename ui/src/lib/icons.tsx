// Stroke icons on a 16px grid, agent runes drawn as strokes (system fonts carry no runes), and
// Leif's mark. Taken from the chosen design (direction A).

const PATHS: Record<string, string> = {
  home: '<path d="M2.5 7.2 8 2.8l5.5 4.4V13a.7.7 0 0 1-.7.7H9.6V10H6.4v3.7H3.2a.7.7 0 0 1-.7-.7z"/>',
  inbox:
    '<path d="M2.2 9.2 4 3.4a.8.8 0 0 1 .8-.6h6.4a.8.8 0 0 1 .8.6l1.8 5.8V12.5a.8.8 0 0 1-.8.8H3a.8.8 0 0 1-.8-.8z"/><path d="M2.4 9.2h3.3l.8 1.6h3l.8-1.6h3.3"/>',
  chat: '<path d="M13.5 7.6c0 2.9-2.5 5-5.5 5a6 6 0 0 1-2-.3l-3 1.1.8-2.5A4.8 4.8 0 0 1 2.5 7.6c0-2.9 2.5-5 5.5-5s5.5 2.1 5.5 5z"/>',
  decided: '<path d="M8 1.8 13.5 4v3.8c0 3.2-2.3 5.5-5.5 6.4-3.2-.9-5.5-3.2-5.5-6.4V4z"/><path d="m5.7 8 1.6 1.6 3-3.2"/>',
  board: '<rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M2.5 6h11M6 6v7.5"/>',
  team: '<circle cx="6" cy="5.5" r="2.3"/><path d="M1.8 13.2c.4-2.2 2.1-3.6 4.2-3.6s3.8 1.4 4.2 3.6"/><path d="M10.5 3.4a2.2 2.2 0 0 1 0 4.3M11.6 9.8c1.4.4 2.4 1.6 2.6 3.4"/>',
  chart: '<path d="M2.5 13.5h11"/><rect x="3.5" y="8" width="2" height="4" rx=".5"/><rect x="7" y="4.5" width="2" height="7.5" rx=".5"/><rect x="10.5" y="6.5" width="2" height="5.5" rx=".5"/>',
  book: '<path d="M3 3.2c0-.6.5-.9 1-.9h3.4c.4 0 .6.3.6.6v10.3c-.4-.5-1-.8-1.7-.8H3z"/><path d="M13 3.2c0-.6-.5-.9-1-.9H8.6c-.4 0-.6.3-.6.6v10.3c.4-.5 1-.8 1.7-.8H13z"/>',
  search: '<circle cx="7" cy="7" r="4.3"/><path d="m10.2 10.2 3.3 3.3"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  edit: '<path d="M10.8 2.8 13.2 5.2 6 12.4l-3.2.8.8-3.2z"/>',
  bolt: '<path d="M8.8 1.8 3.5 9h4l-.8 5.2L12.5 7h-4z"/>',
  pause: '<path d="M6 3.5v9M10 3.5v9"/>',
  warn: '<path d="M8 2.3 14 13H2z"/><path d="M8 6.5v3"/><circle cx="8" cy="11.3" r=".5"/>',
  check: '<path d="m3.2 8.4 3 3 6.6-6.8"/>',
  x: '<path d="m4 4 8 8M12 4l-8 8"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  clock: '<circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.2 1.4"/>',
  arrow: '<path d="M3 8h10M9 4l4 4-4 4"/>',
  chevd: '<path d="m4 6 4 4 4-4"/>',
  sun: '<circle cx="8" cy="8" r="2.8"/><path d="M8 1.5v1.3M8 13.2v1.3M1.5 8h1.3M13.2 8h1.3M3.4 3.4l.9.9M11.7 11.7l.9.9M3.4 12.6l.9-.9M11.7 4.3l.9-.9"/>',
  moon: '<path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8z"/>',
  refresh: '<path d="M13 4.5v3h-3M3 11.5v-3h3"/><path d="M12.6 7.5A4.8 4.8 0 0 0 4 5.5M3.4 8.5A4.8 4.8 0 0 0 12 10.5"/>',
  ext: '<path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5v3.3c0 .4-.3.7-.7.7H3.2a.7.7 0 0 1-.7-.7V4.7c0-.4.3-.7.7-.7H6.5"/>',
  palette: '<circle cx="8" cy="8" r="5.8"/><circle cx="5.6" cy="6.4" r=".7"/><circle cx="8.6" cy="5" r=".7"/><circle cx="10.6" cy="7.6" r=".7"/><path d="M8 13.8c-.9 0-1.4-.6-1.2-1.4.2-1 1-1.4 2-1.4"/>',
  send: '<path d="M2.5 8 13.5 2.8 10 13.5 7.6 8.6z"/><path d="M7.6 8.6 13.5 2.8"/>',
  wand: '<path d="m3 13 7.2-7.2M9.4 3.6l.5-1.4.5 1.4 1.4.5-1.4.5-.5 1.4-.5-1.4-1.4-.5zM12.6 7.6l.3-.9.3.9.9.3-.9.3-.3.9-.3-.9-.9-.3z"/>',
  lock: '<rect x="3" y="7" width="10" height="6.8" rx="1.3"/><path d="M5.2 7V5a2.8 2.8 0 0 1 5.6 0v2"/>',
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className = "ic" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.35}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: PATHS[name] ?? "" }}
    />
  );
}

const RUNES: Record<string, string> = {
  idea: "M8 2.5 12.5 8 8 13.5 3.5 8z",
  intent: "M8 2v12M5 6l6 4",
  spec: "M11 3 5 8l6 5",
  spike: "M5.5 2v12M5.5 2.5 9 5.5l2.8-2.5M5.5 13.5 9 10.5l2.8 2.5",
  plan: "M5.5 2v12M5.5 2.2l5.5 2.9-5.5 2.9 5.5 5.8",
  impl: "M4.5 14V2.2l7 3.6V14",
  pr: "M5.5 2v12M5.5 2.5l5.5 3.5M5.5 6.5l5.5 3.5",
  review: "M8 2v12M4.2 5.8 8 2l3.8 3.8",
  ship: "M8 2 12 6 4 13.5M8 2 4 6l8 7.5",
  integrate: "M3.5 2.5l9 11M12.5 2.5l-9 11",
  dagaz: "M3 2.5v11l10-11v11z",
};

export function Rune({ stage, size = 14 }: { stage: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={RUNES[stage] ?? RUNES.idea} />
    </svg>
  );
}

export function LeifMark({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path fill="currentColor" d="M8 1.2c.5 3.6 2.4 5.6 6 6.1v1.4c-3.6.5-5.5 2.5-6 6.1H8c-.5-3.6-2.4-5.6-6-6.1V7.3c3.6-.5 5.5-2.5 6-6.1z" />
    </svg>
  );
}

export function LeifAvatar({ size = "" }: { size?: "" | "lg" | "xl" }) {
  return (
    <span className={`av leif ${size}`}>
      <LeifMark size={size === "xl" ? 20 : size === "lg" ? 16 : 11} />
    </span>
  );
}

export function AgentAvatar({ stage, title, size = "" }: { stage: string; title?: string; size?: "" | "lg" | "xl" }) {
  return (
    <span className={`av ${size}`} title={title}>
      <Rune stage={stage} size={size === "xl" ? 20 : size === "lg" ? 16 : 12} />
    </span>
  );
}
