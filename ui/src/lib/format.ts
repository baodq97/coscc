// How numbers, times and names read on screen: no raw ids, no model strings.

import type { Paused } from "../api.gen";

export function money(x: number | null | undefined, digits = 2): string {
  if (x == null) return "—";
  return "$" + x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const minutes = Math.round((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** `claude-sonnet-5-5[1m]` reads "Sonnet 5.5". */
export function modelName(model: string | null | undefined): string {
  if (!model) return "—";
  const m = model.match(/claude-(\w+)-(\d+)-(\d+)/);
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : model;
}

/** A unit's short code: `COS-162`, from the workspace name and the unit number. */
export function unitCode(workspace: string, number: number | string): string {
  const prefix = workspace.replace(/[^a-z]/gi, "").slice(0, 3).toUpperCase() || "U";
  return `${prefix}-${Number(number)}`;
}

/** `0162_shipped-units-are-run-again` reads "Shipped units are run again". */
export function unitTitle(slug: string): string {
  const words = slug.replace(/^\d+_/, "").replace(/-/g, " ");
  return words ? words[0].toUpperCase() + words.slice(1) : slug;
}

/** What a card says of a run held at a ceiling: `Paused at $1.00 of $2.00`, or `Paused at 40 of 40 turns`. */
export function pausedAt(p: Paused): string {
  return p.ceiling === "turns" ? `Paused at ${p.turns ?? "?"} of ${p.max_turns ?? "?"} turns` : `Paused at ${money(p.usd)} of ${money(p.max_usd)}`;
}

const WHO: Record<string, string> = { manual: "you", person: "you", leif: "Leif", schedule: "the schedule", event: "an event", autopilot: "the autopilot" };

/** Who started a run, in plain words ("you", "Leif", "the schedule"); `""` when unknown. */
export function startedBy(by: string | null | undefined): string {
  return by ? (WHO[by] ?? by) : "";
}
