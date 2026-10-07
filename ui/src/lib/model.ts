// The studio's names for the app's shapes, which `api.gen.ts` holds as the app makes them.

import type { AgentRow, Card, NextStep, WorkspaceRow } from "../api.gen";
import { modelName, money, pausedAt } from "./format";
import { stageLabel } from "./pack";

export type Workspace = WorkspaceRow;
export type Unit = Card;
export type Agent = AgentRow;




/** Where a unit stands, in the words people use: a phase, or shipped, dropped, an idea. */
export function unitState(u: Unit): { group: string; label: string } {
  if (u.why === "dropped" || u.why === "rejected") return { group: "Dropped", label: u.why === "rejected" ? "Rejected" : "Dropped" };
  if (u.why === "paused" || u.hold?.state === "paused") return { group: "Paused", label: "Paused" };
  if (u.why === "finished" || u.why === "outdated-main") return { group: "Shipped", label: "Shipped" };
  if (u.phase === "pre-intent") return { group: "Ideas", label: "Idea" };
  // A run stopped at its ceiling and kept its session: only a person raises it, reruns it or drops the unit.
  if (u.paused) return { group: "Needs you", label: pausedAt(u.paused) };
  if (u.open > 0) return { group: "Needs you", label: `${u.open} question${u.open > 1 ? "s" : ""}` };
  // The next stage is refused before it spends until what it declares it needs is there.
  if (u.missing?.length) return { group: "Needs you", label: `Needs ${u.missing.join(" and ")}` };
  return { group: "In progress", label: u.next_stage ? stageLabel(u.next_stage) : "In progress" };
}

export const GROUP_ORDER = ["Needs you", "In progress", "Ideas", "Paused", "Shipped", "Dropped"];

/** The one rule for "needs you": open questions count only on a unit that is neither dropped nor shipped. */
export function liveQuestions(u: Unit, open: number = u.open): number {
  const group = unitState(u).group;
  return group === "Dropped" || group === "Shipped" ? 0 : open;
}

/** Whether the run button is offered: a stage that has not run yet is refused only for `missing` (its file), which is what running makes. */
export function runnable(next: NextStep | undefined): boolean {
  if (!next?.stage || next.gate) return false;
  return !next.blocked || (next.reasons.length > 0 && next.reasons.every((r) => r === "missing"));
}

/** What running a stage costs a person to know: its agent, model, effort and ceilings, as its row configures them. */
export function consequence(c: Agent["config"] | undefined): string {
  if (!c) return "Runs a real Claude session and spends account quota.";
  const turns = c.ceilings.max_turns;
  const usd = c.ceilings.max_budget_usd;
  return `Runs ${modelName(c.model)}${c.effort ? ` at ${c.effort} effort` : ""}, at most ${turns != null ? `${turns} turn${turns === 1 ? "" : "s"}` : "no turn limit"} and ${usd != null ? money(usd) : "no $ ceiling"}. Spends account quota.`;
}
