// The shapes the studio reads from the app's routes. Only the fields a screen uses.

export type Workspace = { name: string; path: string; label: string; missing: boolean };

export type Unit = {
  name: string;
  number: number;
  slug: string;
  type: string | null;
  phase: string;
  next_stage: string | null;
  why: string | null;
  open: number;
  pr: { url?: string; number?: number } | string | null;
  hold: string | null;
};

export type Board = {
  units: Unit[];
  autopilot?: { on: boolean; cap?: { spent: number; limit: number; running: number } };
};

export type Agent = {
  key: string;
  glyph: string;
  name: string;
  meaning: string;
  role: string;
  config: { model: string; effort: string; ceilings: { max_turns: number | null; max_budget_usd: number | null } };
  runs_30d: number;
  cost_30d: number;
  chip: string;
  last: { outcome: string; at: string } | null;
};

/** Where a unit stands, in the words people use: a phase, or shipped, dropped, an idea. */
export function unitState(u: Unit): { group: string; label: string } {
  if (u.why === "dropped" || u.why === "rejected") return { group: "Dropped", label: u.why === "rejected" ? "Rejected" : "Dropped" };
  if (u.why === "paused" || u.hold === "paused") return { group: "Paused", label: "Paused" };
  if (u.why === "finished" || u.why === "outdated-main") return { group: "Shipped", label: "Shipped" };
  if (u.phase === "pre-intent") return { group: "Ideas", label: "Idea" };
  if (u.open > 0) return { group: "Needs you", label: `${u.open} question${u.open > 1 ? "s" : ""}` };
  return { group: "In progress", label: u.next_stage ?? "In progress" };
}

export const GROUP_ORDER = ["Needs you", "In progress", "Ideas", "Paused", "Shipped", "Dropped"];
