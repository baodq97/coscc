// The studio's names for the app's shapes, which `api.gen.ts` holds as the app makes them.

import type { AgentRow, Card, WorkspaceRow } from "../api.gen";
import { STAGE_LABEL } from "./format";

export type Workspace = WorkspaceRow;
export type Unit = Card;
export type Agent = AgentRow;




/** Where a unit stands, in the words people use: a phase, or shipped, dropped, an idea. */
export function unitState(u: Unit): { group: string; label: string } {
  if (u.why === "dropped" || u.why === "rejected") return { group: "Dropped", label: u.why === "rejected" ? "Rejected" : "Dropped" };
  if (u.why === "paused" || u.hold?.state === "paused") return { group: "Paused", label: "Paused" };
  if (u.why === "finished" || u.why === "outdated-main") return { group: "Shipped", label: "Shipped" };
  if (u.phase === "pre-intent") return { group: "Ideas", label: "Idea" };
  if (u.open > 0) return { group: "Needs you", label: `${u.open} question${u.open > 1 ? "s" : ""}` };
  return { group: "In progress", label: STAGE_LABEL[u.next_stage] ?? (u.next_stage || "In progress") };
}

export const GROUP_ORDER = ["Needs you", "In progress", "Ideas", "Paused", "Shipped", "Dropped"];
