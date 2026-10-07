// What a person builds on the page, as plain data: a new agent's form, and a process as an ordered
// list of steps that becomes the `{start, end, states}` the app checks. No screen code here.

import type { AgentRow, Condition, PackShown, ProcessShown, State } from "../api.gen";

export type BuildPack = PackShown;
export type BuildAgent = AgentRow;
export const isBuiltIn = (r: BuildAgent) => !r.pack || r.pack === "coscc-sdlc";
export const packTitle = (r: BuildAgent) => (r.pack === "local" ? "Yours" : isBuiltIn(r) ? "Built in" : (r.pack as string));

export type BuildProcess = ProcessShown;

export const KEY = /^[a-z][a-z0-9-]*$/;
export const KEY_MAX = 24;
export const GUARDS = ["skip-decision", "spike-holds", "dependency-merged", "ship-ready", "fast-lane"];
export const GUARD_WORDS: Record<string, string> = {
  "skip-decision": "the spec may be skipped",
  "spike-holds": "the spike holds",
  "dependency-merged": "what it depends on is merged",
  "ship-ready": "review passed and CI is green",
  "fast-lane": "it qualifies for the fast lane",
};
export const ACTION_WORDS: Record<string, string> = { "open-pr": "opens the pull request", merge: "merges it" };

/** `Add a line` -> `add-a-line`: a key from words, cut to the longest the app takes. */
export function slugKey(words: string): string {
  const s = words.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^[^a-z]+/, "").replace(/-+$/, "");
  return s.slice(0, KEY_MAX).replace(/-+$/, "");
}

/** Why a key cannot be used, in a sentence, or null. */
export function keyProblem(key: string, taken: string[]): string | null {
  if (!key) return "A key is needed.";
  if (!KEY.test(key)) return "Use lowercase letters, digits and dashes, starting with a letter.";
  if (key.length > KEY_MAX) return `At most ${KEY_MAX} characters.`;
  if (taken.includes(key)) return "Another agent already has this key.";
  return null;
}

export type NewAgentField = "key" | "name" | "glyph" | "from";

/** Which field of the New agent form a refusal's reason is about; null for none in particular. */
export function fieldOf(reason: string): NewAgentField | null {
  if (/\bglyph\b/i.test(reason)) return "glyph";
  if (/\bkey\b|already|taken/i.test(reason)) return "key";
  if (/\bname\b/i.test(reason)) return "name";
  if (/\bfrom\b|no such agent|no agent/i.test(reason)) return "from";
  return null;
}

export function sortReasons(reasons: string[]): { byField: Partial<Record<NewAgentField, string[]>>; rest: string[] } {
  const byField: Partial<Record<NewAgentField, string[]>> = {};
  const rest: string[] = [];
  for (const r of reasons) {
    const f = fieldOf(r);
    if (f) (byField[f] ??= []).push(r);
    else rest.push(r);
  }
  return { byField, rest };
}

/** Agents that may sit in a state: every row except a helper another agent starts. */
export const stateAgents = (rows: AgentRow[]) => rows.filter((r) => (r.row.output as { kind?: string } | undefined)?.kind !== "helper");

/** What a way on may ask of an agent's output: each field, with its values (an enum) or empty/non-empty. */
export function fieldOptions(row: AgentRow | undefined): { field: string; values: string[] }[] {
  const fields = ((row?.row.output as { fields?: Record<string, unknown> } | undefined)?.fields ?? {}) as Record<string, unknown>;
  return Object.entries(fields).map(([k, t]) => ({
    field: k.replace(/\?$/, ""),
    values: t && typeof t === "object" && Array.isArray((t as { enum?: unknown }).enum) ? ((t as { enum: string[] }).enum) : ["non-empty", "empty"],
  }));
}

/** A condition in words: "verdict is changes-requested", "the spike holds". */
export function conditionWords(c: Condition): string {
  return c.guard ? (GUARD_WORDS[c.guard] ?? c.guard.replace(/-/g, " ")) : `${c.field} is ${c.is}`;
}

export type Way = { to: string; cond: Condition | null; rest: Condition[] };
export type Step = {
  key: string;
  agent: string;
  action: string;
  optional: boolean;
  /** Where it goes on when no way above applies: the next step in the list. */
  then: boolean;
  ways: Way[];
  /** What the editor does not touch (`label`, `hint`, `skip`, `rerun`, a state's own `when`). */
  keep: Partial<State>;
};
export type Draft = { name: string; steps: Step[] };

export const blankDraft = (name = ""): Draft => ({ name, steps: [] });

const list = (when: State["when"]): Condition[] => (when === undefined ? [] : Array.isArray(when) ? when : [when]);
const asWhen = (cs: Condition[]): State["when"] => (cs.length === 0 ? undefined : cs.length === 1 ? cs[0] : cs);

/** A process as steps, in the order of its states; a last way with no `when` to the next step is "then". */
export function fromProcess(name: string, p: Pick<ProcessShown, "start" | "states">): Draft {
  const keys = [p.start, ...Object.keys(p.states).filter((k) => k !== p.start)];
  const steps = keys.map((key, i): Step => {
    const { agent, action, optional, next, ...keep } = p.states[key];
    const ways = (next ?? []).map((w) => ({ to: w.to, when: list(w.when) }));
    const last = ways[ways.length - 1];
    const then = Boolean(last && !last.when.length && last.to === keys[i + 1]);
    return {
      key,
      agent: agent ?? "",
      action: action ?? "",
      optional: Boolean(optional),
      then,
      ways: (then ? ways.slice(0, -1) : ways).map((w) => ({ to: w.to, cond: w.when[0] ?? null, rest: w.when.slice(1) })),
      keep,
    };
  });
  return { name, steps };
}

type Way2 = { to: string; when?: State["when"] };

/** The `{start, end, states}` a draft stands for. */
export function toProcess(d: Draft): Pick<ProcessShown, "start" | "end" | "states"> {
  const states: Record<string, State> = {};
  d.steps.forEach((s, i) => {
    const next: Way2[] = [
      ...s.ways.map((w) => {
        const when = asWhen([...(w.cond ? [w.cond] : []), ...w.rest]);
        return when === undefined ? { to: w.to } : { to: w.to, when };
      }),
      ...(s.then && d.steps[i + 1] ? [{ to: d.steps[i + 1].key }] : []),
    ];
    states[s.key] = {
      ...s.keep,
      ...(s.agent ? { agent: s.agent } : { action: s.action }),
      ...(s.optional ? { optional: true } : {}),
      ...(next.length ? { next } : {}),
    };
  });
  return { start: d.steps[0]?.key ?? "", end: "shipped", states };
}

const defaultKey = (s: { agent: string; action: string }) => s.agent || (s.action === "merge" ? "ship" : "pr");

function freeKey(d: Draft, want: string): string {
  const taken = d.steps.map((s) => s.key);
  let key = want;
  for (let n = 2; taken.includes(key); n++) key = `${want}-${n}`;
  return key;
}

/** A step added at the end; the step before it now goes on to it. */
export function addStep(d: Draft, what: { agent: string } | { action: string }): Draft {
  const agent = "agent" in what ? what.agent : "";
  const action = "action" in what ? what.action : "";
  const key = freeKey(d, defaultKey({ agent, action }));
  const keep: Partial<State> = action === "merge" ? { when: { guard: "ship-ready" } } : {};
  const steps = d.steps.map((s, i) => (i === d.steps.length - 1 ? { ...s, then: true } : s));
  return { ...d, steps: [...steps, { key, agent, action, optional: false, then: false, ways: [], keep }] };
}

export function removeStep(d: Draft, key: string): Draft {
  const steps = d.steps.filter((s) => s.key !== key).map((s) => ({ ...s, ways: s.ways.filter((w) => w.to !== key) }));
  if (steps.length) steps[steps.length - 1] = { ...steps[steps.length - 1], then: false };
  return { ...d, steps };
}

export function moveStep(d: Draft, key: string, by: -1 | 1): Draft {
  const i = d.steps.findIndex((s) => s.key === key);
  const j = i + by;
  if (i < 0 || j < 0 || j >= d.steps.length) return d;
  const steps = [...d.steps];
  [steps[i], steps[j]] = [steps[j], steps[i]];
  // "Then the next step" belongs to a place in the list, not to the step that stood there.
  return { ...d, steps: steps.map((s, n) => ({ ...s, then: d.steps[n].then })) };
}

export function setStep(d: Draft, key: string, change: Partial<Step>): Draft {
  return { ...d, steps: d.steps.map((s) => (s.key === key ? { ...s, ...change } : s)) };
}

/** Another agent in a step; its key follows when it still carried the old agent's name. */
export function setAgent(d: Draft, key: string, agent: string): Draft {
  const step = d.steps.find((s) => s.key === key);
  if (!step) return d;
  const fresh = step.key === defaultKey(step) || step.key.startsWith(`${defaultKey(step)}-`) ? freeKey({ ...d, steps: d.steps.filter((s) => s.key !== key) }, agent) : step.key;
  const steps = d.steps.map((s) =>
    s.key === key ? { ...s, key: fresh, agent, ways: s.ways.map((w) => ({ ...w, cond: null, rest: [] })) } : { ...s, ways: s.ways.map((w) => (w.to === key ? { ...w, to: fresh } : w)) },
  );
  return { ...d, steps };
}

/**
 * A refusal's reasons by the step they name (`tiny.ship: a review state is…`), and the rest.
 * A reason names a step when a dotted word before its first colon is that step's key.
 */
export function reasonsByStep(reasons: string[], keys: string[]): { byStep: Record<string, string[]>; rest: string[] } {
  const byStep: Record<string, string[]> = {};
  const rest: string[] = [];
  for (const r of reasons) {
    const head = r.split(":")[0].replace(/\[\d+\]/g, "");
    const parts = head.split(/[./]/).slice(1);
    const hit = keys.find((k) => parts.includes(k));
    if (hit) (byStep[hit] ??= []).push(r.slice(r.indexOf(":") + 1).trim());
    else rest.push(r);
  }
  return { byStep, rest };
}

/** The step a draft's process name needs; a sentence, or null. */
export function nameProblem(name: string, taken: string[]): string | null {
  if (!name) return "A name is needed.";
  if (!KEY.test(name) || name.length > KEY_MAX) return "Use lowercase letters, digits and dashes, starting with a letter.";
  if (taken.includes(name)) return "Another process here has this name.";
  return null;
}

/** `42 KB`, `1.2 MB`: a file's size for a reader. */
export function sizeWords(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
