// One agent: every part of its row, each shown as built in or as you edited it, in six tabs.
// Edits gather in one draft; the save bar sends each changed part, and the app checks the row
// as it would then stand before writing anything. The next run uses what was saved.

import { useState, type ReactNode } from "react";
import type { AgentPage as Page, AgentRow, CatalogTool } from "../api.gen";
import { api, ApiError } from "../lib/api";
import { refreshPacks } from "../lib/pack";
import { sandboxed, sandboxLine, sandboxOf } from "../lib/build";
import { modelName, money } from "../lib/format";
import { Link, navigate } from "../lib/router";
import { Button, Chip, Empty, ErrorState, SkeletonRows } from "../components/ui";
import { useResource } from "../lib/api";
import { useIndex } from "../lib/pack";
import { AgentGlyph, WorkspaceSwitch, attention, inWorkspace, statusWords, triggerWords, useAgents } from "./Agents";
import { Activity } from "./AgentActivity";

export const TABS = [
  { key: "activity", label: "Activity" },
  { key: "configuration", label: "Configuration" },
  { key: "tools", label: "Tools" },
  { key: "io", label: "Input/Output" },
  { key: "trigger", label: "Trigger" },
  { key: "prompt", label: "Prompt & skills" },
] as const;
type Tab = (typeof TABS)[number]["key"];

const MODELS = ["claude-opus-5-5[1m]", "claude-sonnet-5-5[1m]", "claude-haiku-4-5"];
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];
const DATA: Record<string, string> = {
  catalog: "what agents and processes can be built from",
  idea: "the shared idea",
  siblings: "sibling checkouts",
  mentions: "units this one names",
  "plan-map": "the plan's files as they stand",
  drift: "what main changed since the plan",
  integration: "the last integration",
  screens: "the screenshots",
  interventions: "what people stepped in for since its last run",
  proposals: "the proposals already made",
};
export const EFFECT: Record<string, string> = {
  read: "Reads",
  "write-worktree": "Writes the worktree",
  "write-app": "Writes the app's records",
  external: "Runs commands",
};
export const TIER: Record<string, "plain" | "amber" | "red"> = { low: "plain", medium: "amber", high: "red" };

type Draft = Record<string, unknown>;

/** `get({a: {b: 1}}, ["a", "b"])` is 1; a missing step is undefined. */
export function get(tree: unknown, path: string[]): unknown {
  return path.reduce<unknown>((t, k) => (t && typeof t === "object" ? (t as Record<string, unknown>)[k] : undefined), tree);
}

/** `tree` with `path` set to `value`, or removed when `value` is undefined; parents left empty go too. */
export function put(tree: unknown, path: string[], value: unknown): unknown {
  if (!path.length) return value;
  const obj = tree && typeof tree === "object" ? { ...(tree as Record<string, unknown>) } : {};
  const [k, ...rest] = path;
  const next = put(obj[k], rest, value);
  if (next === undefined || (next && typeof next === "object" && !Array.isArray(next) && !Object.keys(next).length)) delete obj[k];
  else obj[k] = next;
  return Object.keys(obj).length ? obj : undefined;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** The draft's parts that differ from what is saved: what the save bar sends. */
export function changes(draft: Draft, saved: Record<string, unknown>): string[] {
  return Object.keys(draft).filter((k) => !same(draft[k], saved[k]));
}

const LABELS: Record<string, string> = {
  "model.id": "model",
  "model.effort": "effort",
  "model.trial": "trial arms",
  "ceilings.turns": "turns",
  "ceilings.usd": "spend",
  "variants.novel.model.id": "new-ground model",
  "variants.novel.model.effort": "new-ground effort",
  "variants.novel.ceilings.turns": "new-ground turns",
  "variants.novel.ceilings.usd": "new-ground spend",
};

function leaves(v: unknown, path: string[] = []): [string, unknown][] {
  return v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length
    ? Object.entries(v).flatMap(([k, x]) => leaves(x, [...path, k]))
    : [[path.join("."), v]];
}

/** The parts of the draft that differ from what is saved, named as the page names them (`effort`, `spend`), not by their record's keys. */
export function changedParts(draft: Draft, saved: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const k of changes(draft, saved)) {
    const now = new Map(leaves(draft[k], [k]));
    const was = new Map(leaves(saved[k], [k]));
    const hit = [...new Set([...now.keys(), ...was.keys()])].filter((p) => !same(now.get(p), was.get(p)));
    out.push(...hit.map((p) => LABELS[p] ?? (p.startsWith("skill:") ? `${p.slice(6)} skill` : p)));
  }
  return [...new Set(out)];
}

export function AgentPage({ name, tab = "activity" }: { name: string; tab?: string }) {
  const { ws, list, workspace, cwd, agents } = useAgents(name);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ field: string; message: string } | null>(null);
  // A page a save handed back, for the workspace it was saved in.
  const [freshAt, setFreshAt] = useState<{ cwd: string; page: Page } | null>(null);
  const fresh = freshAt?.cwd === cwd ? freshAt.page : null;
  const setFresh = (p: Page) => setFreshAt({ cwd, page: p });
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const page = fresh ?? agents.data;
  // What it is doing now comes from the latest read, whatever a save handed back.
  const mine = page?.rows.find((r) => r.key === name);
  const a = mine && agents.data ? { ...mine, running: agents.data.rows.find((r) => r.key === name)?.running ?? null } : mine;
  if (agents.state === "error" && !page) return <ErrorState error={agents.error} onRetry={agents.reload} />;
  if (!page) return <div className="page"><SkeletonRows rows={5} /></div>;
  if (!a)
    return (
      <div className="page">
        <Empty icon="team" title="No such agent" actions={<Button onClick={() => navigate(inWorkspace("/agents", workspace))}>All agents</Button>}>
          The team has no {name}.
        </Empty>
      </div>
    );

  const saved: Record<string, unknown> = { ...a.row };
  for (const s of a.skills) saved[`skill:${s.name}`] = s.text;
  // The built-in's value of each edited part; every other part is still the built-in's.
  const builtin: Record<string, unknown> = { ...a.builtin };
  for (const [k, v] of Object.entries(a.row)) if (!(k in builtin) && !a.edited.includes(k)) builtin[k] = v;
  for (const s of a.skills) builtin[`skill:${s.name}`] = s.edited ? s.builtin : s.text;
  const pending = changes(draft, saved);
  const value = (k: string) => (k in draft ? draft[k] : saved[k]);
  // `v` may be a function of the part as it then stands, so two quick edits both land.
  const edit = (k: string, v: unknown | ((now: unknown) => unknown)) => {
    setError(null);
    setDone(false);
    setDraft((d) => ({ ...d, [k]: typeof v === "function" ? v(k in d ? d[k] : saved[k]) : v }));
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    let failed = false;
    for (const field of pending) {
      try {
        const v = draft[field];
        const next = await api.post<Page>("/api/agents/field", { key: a.key, field, value: v === undefined || (!a.own && same(v, builtin[field])) ? null : v, cwd });
        setFresh(next);
        setDraft((d) => {
          const { [field]: _, ...rest } = d;
          return rest;
        });
      } catch (e) {
        setError({ field, message: (e as Error).message });
        failed = true;
        break;
      }
    }
    setBusy(false);
    setDone(!failed);
  };
  const own = a.own;
  const remove = async () => {
    setBusy(true);
    setRefused([]);
    try {
      await api.post<Page>("/api/agents/delete", { key: a.key, cwd });
      refreshPacks();
      navigate(inWorkspace("/agents", workspace));
    } catch (e) {
      setRefused(e instanceof ApiError && e.reasons.length ? e.reasons : [(e as Error).message]);
      setAsking(false);
    } finally {
      setBusy(false);
    }
  };
  const ctx: Ctx = { a, page, value, edit, saved, builtin, editable: a.editable && !busy, error, cwd, workspace: workspace?.name, setPage: setFresh };
  const t: Tab = TABS.some((x) => x.key === tab) ? (tab as Tab) : "activity";
  const look = attention(a);

  return (
    <div className="page mid agent-page">
      <div className="agent-head">
        <AgentGlyph a={a} size="xl" />
        <div className="agent-head-main">
          <h1 className="title">
            {a.row.name ?? a.key} <span className="faint mono" style={{ fontSize: 13 }}>{a.key}</span>
          </h1>
          <div className="muted">{a.row.description || triggerWords(a, page.rows)}</div>
          <div className="faint" style={{ fontSize: 12.5, marginTop: 2 }}>{statusWords(a, workspace?.name ?? "")}</div>
          <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            {a.on !== false && <Chip square tone="plain">Runs {triggerWords(a, page.rows)}</Chip>}
            {a.running && <Chip square tone="accent"><span className="dot live" /> running</Chip>}
            {look && <Chip square tone={look.tone}>{look.label}</Chip>}
            {own ? <Chip square tone="accent">Yours</Chip> : a.edited.length > 0 ? <Chip square tone="accent">{a.edited.length} part{a.edited.length > 1 ? "s" : ""} edited</Chip> : <Chip square tone="plain">as built in</Chip>}
          </div>
        </div>
        <div className="agent-head-side">
        {own && (
          <div className="row" style={{ gap: 6 }}>
            {asking ? (
              <>
                <span className="faint" style={{ fontSize: 12.5 }}>Delete {a.key} for good?</span>
                <Button size="sm" kind="danger" disabled={busy} onClick={remove}>{busy ? "Deleting…" : "Yes, delete"}</Button>
                <Button size="sm" kind="ghost" onClick={() => setAsking(false)}>Keep it</Button>
              </>
            ) : (
              <Button size="sm" kind="ghost" onClick={() => setAsking(true)}>Delete</Button>
            )}
          </div>
        )}
        <WorkspaceSwitch list={list} workspace={workspace} to={`/agents/${a.key}/${t}`} />
        <div className="agent-spend">
          <b>{money(a.cost_30d)}</b> <span className="faint">in 30 days</span>
          <div className="faint" style={{ fontSize: 12 }}>{a.runs_30d} run{a.runs_30d === 1 ? "" : "s"}, {page.scope === "workspace" ? "this workspace" : "all workspaces"}</div>
        </div>
        </div>
      </div>
      {a.group === "triggered" && <Controls {...ctx} />}
      {refused.length > 0 && (
        <div className="card card-b problems">
          <b>Not deleted:</b>
          <ul>{refused.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {a.problems.length > 0 && (
        <div className="card card-b problems">
          <b>Its runs are refused until this is fixed:</b>
          <ul>{a.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}

      <div className="tabs agent-tabs" style={{ marginTop: 18 }}>
        {TABS.map((x) => (
          <Link key={x.key} to={inWorkspace(`/agents/${a.key}/${x.key}`, workspace)} className={x.key === t ? "on" : ""}>
            {x.label}
          </Link>
        ))}
      </div>

      {t === "configuration" && <Configuration {...ctx} />}
      {t === "tools" && <Tools {...ctx} />}
      {t === "io" && <InputOutput {...ctx} />}
      {t === "trigger" && <Trigger {...ctx} />}
      {t === "prompt" && <Prompt {...ctx} />}
      {t === "activity" && <Activity a={a} page={page} names={Object.fromEntries((ws.data?.workspaces ?? []).map((w) => [w.path, w.name]))} workspace={workspace?.name ?? ""} />}

      {done && pending.length === 0 && !error && (
        <div className="savebar" role="status">
          <span className="grow" style={{ fontSize: 13 }}>Saved. The next run uses it.</span>
          <Button kind="ghost" size="sm" onClick={() => setDone(false)}>Close</Button>
        </div>
      )}
      {(pending.length > 0 || error) && (
        <div className="savebar">
          <span className="grow" style={{ fontSize: 13 }}>
            {error ? <span className="savebar-err">Not saved: {error.message}</span> : `${pending.length} unsaved change${pending.length > 1 ? "s" : ""}: ${changedParts(draft, saved).join(", ")}`}
          </span>
          <Button kind="ghost" size="sm" disabled={busy} onClick={() => { setDraft({}); setError(null); setDone(false); }}>
            Discard
          </Button>
          {pending.length > 0 && (
            <Button kind="primary" size="sm" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

type Ctx = {
  a: AgentRow;
  page: Page;
  value: (k: string) => unknown;
  edit: (k: string, v: unknown | ((now: unknown) => unknown)) => void;
  saved: Record<string, unknown>;
  builtin: Record<string, unknown>;
  editable: boolean;
  error: { field: string; message: string } | null;
  cwd: string;
  workspace?: string;
  setPage: (p: Page) => void;
};

/** One part of a row: its label, where it comes from (built in, edited, unsaved) and a reset. */
function Part({ ctx, path, label, hint, children }: { ctx: Ctx; path: string; label: string; hint?: string; children: ReactNode }) {
  const [top, ...rest] = path.split(".");
  const now = get(ctx.value(top), rest);
  const saved = get(ctx.saved[top], rest);
  const base = get(ctx.builtin[top], rest);
  const source = !same(now, saved) ? "unsaved" : same(saved, base) ? "built-in" : "edited";
  return (
    <div className="field">
      <div>
        <div className="lab">{label}</div>
        <div className="hint">
          {(source === "unsaved" || (!ctx.a.own && source === "edited")) && <Chip square tone={source === "edited" ? "accent" : "amber"}>{source}</Chip>}
          {!ctx.a.own && source !== "built-in" && ctx.editable && (
            <button className="linkish" onClick={() => ctx.edit(top, (now: unknown) => put(now, rest, base))}>
              Reset to built-in
            </button>
          )}
        </div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
        {children}
        {ctx.error?.field === top && <div className="field-err">{ctx.error.message}</div>}
      </div>
    </div>
  );
}

/** The model choices for a field holding `current`: the known ids, the current one if it is
 * another, each named as a person reads it; the empty choice runs the default. */
export function modelOptions(current: string | undefined, empty: string): { value: string; label: string }[] {
  const ids = current && !MODELS.includes(current) ? [...MODELS, current] : MODELS;
  return [{ value: "", label: empty }, ...ids.map((m) => ({ value: m, label: modelName(m) }))];
}

function ModelSelect({ ctx, path, empty }: { ctx: Ctx; path: string; empty: string }) {
  const [top, ...rest] = path.split(".");
  const v = get(ctx.value(top), rest);
  const current = v == null || v === "" ? undefined : String(v);
  return (
    <select
      className="input sm"
      style={{ width: 260, maxWidth: "100%" }}
      value={current ?? ""}
      disabled={!ctx.editable}
      onChange={(e) => ctx.edit(top, (now: unknown) => put(now, rest, e.target.value === "" ? undefined : e.target.value))}
    >
      {modelOptions(current, empty).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function Text({ ctx, path, width = 260, placeholder }: { ctx: Ctx; path: string; width?: number; placeholder?: string }) {
  const [top, ...rest] = path.split(".");
  const v = get(ctx.value(top), rest);
  return (
    <input
      className="input sm"
      style={{ width, maxWidth: "100%" }}
      value={v == null ? "" : String(v)}
      placeholder={placeholder}
      disabled={!ctx.editable}
      list={path.endsWith("model.id") || path === "model.id" ? "models" : undefined}
      onChange={(e) => ctx.edit(top, (now: unknown) => put(now, rest, e.target.value === "" ? undefined : e.target.value))}
    />
  );
}

function NumberField({ ctx, path, prefix }: { ctx: Ctx; path: string; prefix?: string }) {
  const [top, ...rest] = path.split(".");
  const v = get(ctx.value(top), rest);
  return (
    <span className="row" style={{ gap: 4 }}>
      {prefix && <span className="faint">{prefix}</span>}
      <input
        className="input sm"
        inputMode="decimal"
        style={{ width: 90 }}
        value={v == null ? "" : String(v)}
        disabled={!ctx.editable}
        onChange={(e) => {
          const s = e.target.value.trim();
          const n = Number(s);
          ctx.edit(top, (now: unknown) => put(now, rest, s === "" ? undefined : Number.isFinite(n) ? n : s));
        }}
      />
    </span>
  );
}

function Effort({ ctx, path }: { ctx: Ctx; path: string }) {
  const [top, ...rest] = path.split(".");
  const v = get(ctx.value(top), rest);
  return (
    <div className="seg">
      {EFFORTS.map((e) => (
        <button key={e} className={v === e ? "on" : ""} disabled={!ctx.editable} onClick={() => ctx.edit(top, (now: unknown) => put(now, rest, e))}>
          {e}
        </button>
      ))}
    </div>
  );
}

function Configuration(ctx: Ctx) {
  const { a, page } = ctx;
  const has = (k: string) => ctx.saved[k] !== undefined || ctx.builtin[k] !== undefined;
  const trial = get(ctx.value("model"), ["trial"]) as string[] | undefined;
  const modelId = get(ctx.value("model"), ["id"]) as string | undefined;
  // The row's own model or ceilings, edited with its new-ground variant left alone, rule a new-ground step too.
  const shadowed = ["model", "ceilings"].filter((k) => a.edited.includes(k) && !a.edited.includes("variants"));
  const ran = (c: AgentRow["config"]) =>
    `Runs on ${modelName(c.model)}${c.model_source === "COS_MODEL" ? " (COS_MODEL)" : ""}${c.effort ? `, ${c.effort} effort` : ""}, at most ${c.ceilings.max_turns ?? "—"} turn${c.ceilings.max_turns === 1 ? "" : "s"} and ${c.ceilings.max_budget_usd != null ? money(c.ceilings.max_budget_usd) : "no $ ceiling"}.`;
  return (
    <>
      <datalist id="models">{MODELS.map((m) => <option key={m} value={m} />)}</datalist>
      <div className="sec-h">Who it is</div>
      <div className="card card-b">
        <Part ctx={ctx} path="name" label="Name" hint="Signed on its commits and artifacts.">
          <Text ctx={ctx} path="name" width={200} />
        </Part>
        <Part ctx={ctx} path="glyph" label="Glyph">
          <Text ctx={ctx} path="glyph" width={120} />
        </Part>
        <Part ctx={ctx} path="description" label="Description">
          <Text ctx={ctx} path="description" width={520} />
        </Part>
      </div>

      <div className="sec-h">
        How it runs <span className="faint">{ran(a.config)}</span>
      </div>
      <div className="card card-b">
        <Part ctx={ctx} path="model.id" label="Model" hint={`${modelId ? `Id ${modelId}. ` : ""}${page.cos_model ? `Empty: ${modelName(page.cos_model)}, from COS_MODEL.` : "Empty: the CLI's default."}`}>
          <ModelSelect ctx={ctx} path="model.id" empty="Default" />
        </Part>
        <Part ctx={ctx} path="model.effort" label="Effort">
          <Effort ctx={ctx} path="model.effort" />
        </Part>
        {has("ceilings") && (
          <>
            <Part ctx={ctx} path="ceilings.turns" label="Turns at most">
              <NumberField ctx={ctx} path="ceilings.turns" />
            </Part>
            <Part ctx={ctx} path="ceilings.usd" label="Spend at most">
              <NumberField ctx={ctx} path="ceilings.usd" prefix="$" />
            </Part>
          </>
        )}
        {trial && (
          <Part ctx={ctx} path="model.trial" label="Trial arms" hint="A routine step runs on one of the two, by unit.">
            {[0, 1].map((i) => (
              <span key={i} className="col" style={{ gap: 2 }}>
                <input
                  className="input sm"
                  style={{ width: 260, maxWidth: "100%" }}
                  list="models"
                  aria-label={`Trial arm ${i + 1}`}
                  value={trial[i] ?? ""}
                  disabled={!ctx.editable}
                  onChange={(e) => ctx.edit("model", (now: unknown) => put(now, ["trial"], trial.map((m, j) => (j === i ? e.target.value : m))))}
                />
                <span className="faint" style={{ fontSize: 12 }}>{modelName(trial[i])}</span>
              </span>
            ))}
          </Part>
        )}
        {has("warning") && (
          <Part ctx={ctx} path="warning" label="Said before a run" hint="Shown beside the button that starts it.">
            <Text ctx={ctx} path="warning" width={620} />
          </Part>
        )}
      </div>

      {a.novel && (
        <>
          <div className="sec-h">
            On new ground <span className="faint">a step its plan marks novel. {ran(a.novel)}</span>
            {shadowed.length > 0 && <div className="faint" style={{ fontSize: 12.5, fontWeight: 400 }}>Your edit of {shadowed.join(" and ")} above rules these steps too, until you set them here.</div>}
          </div>
          <div className="card card-b">
            <Part ctx={ctx} path="variants.novel.model.id" label="Model" hint={get(ctx.value("variants"), ["novel", "model", "id"]) ? `Id ${get(ctx.value("variants"), ["novel", "model", "id"])}` : undefined}>
              <ModelSelect ctx={ctx} path="variants.novel.model.id" empty="As above" />
            </Part>
            <Part ctx={ctx} path="variants.novel.model.effort" label="Effort">
              <Effort ctx={ctx} path="variants.novel.model.effort" />
            </Part>
            <Part ctx={ctx} path="variants.novel.ceilings.turns" label="Turns at most">
              <NumberField ctx={ctx} path="variants.novel.ceilings.turns" />
            </Part>
            <Part ctx={ctx} path="variants.novel.ceilings.usd" label="Spend at most">
              <NumberField ctx={ctx} path="variants.novel.ceilings.usd" prefix="$" />
            </Part>
          </div>
        </>
      )}
    </>
  );
}

function Tools(ctx: Ctx) {
  const { a, page } = ctx;
  const tools = (ctx.value("tools") as Record<string, unknown> | undefined) ?? {};
  const output = (ctx.value("output") as { by?: string; kind?: string } | undefined) ?? {};
  const readsOnly = output.by === "app";
  const helper = a.group === "helper";
  const helpers = page.rows.filter((r) => r.group === "helper");
  const chosen = (ctx.value("helpers") as string[] | undefined) ?? [];
  const triggered = a.group === "triggered";
  const set = (t: string, p: unknown) => {
    const next = { ...tools };
    if (p === "off") delete next[t];
    else next[t] = p;
    ctx.edit("tools", next);
  };
  // A row a trigger starts holds Bash only inside the OS sandbox.
  // What the row can never hold, from the rule saving it uses (`pack.reads_only`): no one to ask, no writes.
  const silent = a.reads_only;
  const choices = (t: CatalogTool) => (triggered && t.name === "Bash" ? ["sandboxed", "off"] : silent ? ["allow", "off"] : ["allow", "ask", "off"]);
  const why = (t: CatalogTool) =>
    readsOnly && t.effect !== "read" ? "The app writes this agent's output from its reply, so it holds only reading tools." : helper && t.name === "Agent" ? "A helper starts no helper." : "";
  const shown = page.catalog.filter((t) => !(silent && t.effect !== "read" && t.name !== "Bash" && !tools[t.name]));
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {silent
          ? "This agent starts on its own, so it has no one to ask and only reads: tools that change things are not offered. Allow gives a tool to its runs; Off leaves it out."
          : "Allow gives the tool to its runs; Ask offers it and refuses every call until a person can be asked; Off leaves it out. No setting here lifts the app's critical blocks."}
      </p>
      {(readsOnly || helper) && (
        <p className="muted">{readsOnly ? "The app writes this agent's output from its reply, so it holds only reading tools." : "A helper starts no helper."}</p>
      )}
      <div className="card">
        {shown.map((t) => {
          const box = sandboxOf(tools[t.name]);
          const p = box ? "sandboxed" : String(tools[t.name] ?? "off");
          const no = why(t);
          return (
            <div key={t.name} className="tool-line">
              <span className="tname">
                <b className="mono">{t.name}</b>
                {t.feature && (
                  <span className="faint"> · {t.feature === t.name ? "feature" : `from ${t.feature}`}{t.on ? "" : ", off in this workspace"}</span>
                )}
              </span>
              <span className="tmeta">
                <Chip square tone="plain">{EFFECT[t.effect] ?? t.effect}</Chip>
                <Chip square tone={TIER[t.tier] ?? "plain"}>{t.tier} risk</Chip>
                {!same(tools[t.name] ?? "off", ((ctx.builtin.tools as Record<string, unknown> | undefined) ?? {})[t.name] ?? "off") && <Chip square tone="accent">edited</Chip>}
              </span>
              <span className="seg">
                {choices(t).map((x) => (
                  <button key={x} className={p === x ? "on" : ""} disabled={!ctx.editable || (x !== "off" && !!no)} onClick={() => set(t.name, x === "sandboxed" ? sandboxed(box?.join(", ") ?? "") : x)}>
                    {x}
                  </button>
                ))}
              </span>
              {box && (
                <span className="tool-why">
                  <div>{sandboxLine(box)}</div>
                  <label>
                    Network{" "}
                    <input
                      key={box.join(", ")}
                      className="mono"
                      defaultValue={box.join(", ")}
                      placeholder="127.0.0.1:3000"
                      disabled={!ctx.editable}
                      onBlur={(e) => e.target.value !== box.join(", ") && set(t.name, sandboxed(e.target.value))}
                    />
                  </label>
                </span>
              )}
              {t.name === "Agent" && p !== "off" && (
                <span className="tool-why">
                  Starts:{" "}
                  {helpers.map((h) => (
                    <label key={h.key} className="check">
                      <input
                        type="checkbox"
                        checked={chosen.includes(h.key)}
                        disabled={!ctx.editable}
                        onChange={(e) => ctx.edit("helpers", e.target.checked ? [...chosen, h.key] : chosen.filter((x) => x !== h.key))}
                      />{" "}
                      {h.row.name ?? h.key}
                    </label>
                  ))}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {ctx.error && ["tools", "helpers"].includes(ctx.error.field) && <div className="field-err">{ctx.error.message}</div>}
    </>
  );
}

type Input = { artifacts: string[]; outputs: string[]; answers: boolean; findings: boolean; data: string[] };

function InputOutput(ctx: Ctx) {
  const { withAgent } = useIndex();
  const input = ctx.value("input") as Input | undefined;
  const base = (ctx.builtin.input as Input | undefined) ?? { artifacts: [], outputs: [], answers: false, findings: false, data: [] };
  const set = (k: keyof Input, v: unknown) => input && ctx.edit("input", { ...input, [k]: v });
  const named = (list: string[], n: string) => list.find((x) => x.replace(/\?$/, "") === n);
  const toggleArtifact = (n: string, on: boolean) => {
    if (!input) return;
    const rest = input.artifacts.filter((x) => x.replace(/\?$/, "") !== n);
    set("artifacts", on ? [...rest, named(base.artifacts, n) ?? n] : rest);
  };
  return (
    <>
      <div className="sec-h">What it is handed</div>
      <div className="card card-b">
        {!input ? (
          <div className="faint">Nothing of a unit: this agent is given what its engine hands it.</div>
        ) : (
          <>
            <Part ctx={ctx} path="input.artifacts" label="Artifacts" hint="Whole, as the unit's folder holds them.">
              {withAgent.map((n) => (
                <label key={n} className="check">
                  <input type="checkbox" checked={!!named(input.artifacts, n)} disabled={!ctx.editable} onChange={(e) => toggleArtifact(n, e.target.checked)} /> {n}
                </label>
              ))}
            </Part>
            <Part ctx={ctx} path="input.data" label="The app's data">
              {Object.entries(DATA).map(([k, words]) => (
                <label key={k} className="check" title={k}>
                  <input type="checkbox" checked={input.data.includes(k)} disabled={!ctx.editable} onChange={(e) => set("data", e.target.checked ? [...input.data, k] : input.data.filter((x) => x !== k))} /> {words}
                </label>
              ))}
            </Part>
            <Part ctx={ctx} path="input.answers" label="Answers and findings">
              <label className="check">
                <input type="checkbox" checked={input.answers} disabled={!ctx.editable} onChange={(e) => set("answers", e.target.checked)} /> the unit's answers
              </label>
              <label className="check">
                <input type="checkbox" checked={input.findings} disabled={!ctx.editable} onChange={(e) => set("findings", e.target.checked)} /> open review findings
              </label>
            </Part>
          </>
        )}
      </div>
      <div className="sec-h">What it hands back</div>
      <div className="card card-b">
        <OutputForm ctx={ctx} />
      </div>
    </>
  );
}

const KIND_WORDS: Record<string, string> = {
  artifact: "A file in the unit's folder",
  review: "A review round of a pull request",
  session: "A working session on the branch",
  proposal: "Proposals for the Backlog",
  verdict: "A verdict, graded criterion by criterion",
  draft: "A draft of an agent or a process",
  reply: "A written reply",
  helper: "A helper's answer to the agent that started it",
};
const WRITER_WORDS: Record<string, string> = {
  app: "The app writes it from the agent's reply",
  scratch: "The agent writes it in its scratch folder",
  session: "The agent commits it on the unit's branch",
};

/** One field of an output, as a short phrase: its enum values, "a list", or "text". */
function fieldWords(spec: unknown): string {
  if (spec && typeof spec === "object") {
    const o = spec as Record<string, unknown>;
    if (Array.isArray(o.enum)) return (o.enum as string[]).join(" / ");
    if (o.list) return "a list";
  }
  return "text";
}

/** What an agent hands back, as a form: its kind and who writes it in words, what it is for, and
 * its fields; the row's own JSON stays one click away for an agent of yours. */
function OutputForm({ ctx }: { ctx: Ctx }) {
  const [raw, setRaw] = useState(false);
  const out = (ctx.value("output") as { kind?: string; by?: string; purpose?: string; fields?: Record<string, unknown> } | undefined) ?? {};
  const fields = Object.entries(out.fields ?? {});
  return (
    <>
      <Part ctx={ctx} path="output.kind" label="Kind">
        <div>{KIND_WORDS[out.kind ?? ""] ?? out.kind ?? "—"}</div>
      </Part>
      {out.by && (
        <Part ctx={ctx} path="output.by" label="Written by">
          <div>{WRITER_WORDS[out.by] ?? out.by}</div>
        </Part>
      )}
      <Part ctx={ctx} path="output.purpose" label="What it is for" hint="Told to the agent beside its fields.">
        <input
          className="input"
          style={{ width: "100%", maxWidth: 560 }}
          value={out.purpose ?? ""}
          disabled={!ctx.editable}
          onChange={(e) => ctx.edit("output", { ...out, purpose: e.target.value })}
        />
      </Part>
      {fields.length > 0 && (
        <Part ctx={ctx} path="output.fields" label="Fields" hint="A field another stage reads cannot be removed.">
          <ul className="fields">
            {fields.map(([k, v]) => (
              <li key={k}>
                <b className="mono">{k}</b> <span className="faint">{fieldWords(v)}</span>
              </li>
            ))}
          </ul>
        </Part>
      )}
      {ctx.a.own && (
        <div style={{ marginTop: 8 }}>
          <Button size="sm" kind="ghost" onClick={() => setRaw(!raw)}>{raw ? "Hide the row's JSON" : "Edit as JSON"}</Button>
          {raw && <JsonPart ctx={ctx} field="output" label="Output" hint="Its kind, version and fields." />}
        </div>
      )}
    </>
  );
}

function JsonPart({ ctx, field, label, hint }: { ctx: Ctx; field: string; label: string; hint: string }) {
  const v = ctx.value(field);
  const [text, setText] = useState(() => JSON.stringify(v ?? null, null, 2));
  const [bad, setBad] = useState("");
  const shown = bad ? text : JSON.stringify(v ?? null, null, 2);
  return (
    <Part ctx={ctx} path={field} label={label} hint={hint}>
      <textarea
        className="editor mono"
        rows={Math.min(24, shown.split("\n").length + 1)}
        value={bad ? text : shown}
        disabled={!ctx.editable}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          try {
            ctx.edit(field, JSON.parse(e.target.value));
            setBad("");
          } catch (err) {
            setBad((err as Error).message);
          }
        }}
      />
      {bad && <div className="field-err">Not JSON yet: {bad}</div>}
    </Part>
  );
}

function Trigger(ctx: Ctx) {
  const { a, page } = ctx;
  const t = a.row.trigger ?? {};
  if (a.group !== "triggered")
    return (
      <div className="card card-b">
        <div className="field">
          <div>
            <div className="lab">Runs</div>
            <div className="hint"><Chip square tone="plain">read-only</Chip></div>
          </div>
          <div>
            <b>{triggerWords(a, page.rows)}</b>
            <div className="muted" style={{ marginTop: 6 }}>
              {t.state
                ? `A unit that reaches that state, in a process that has it, runs this agent when you, or the autopilot, press Run.`
                : t.engine
                  ? "The app opens it itself; no unit state starts it."
                  : a.group === "helper"
                  ? "Another agent starts it inside its own run."
                  : "Nothing runs it yet: put it in a step of a process (What Leif may do, New process)."}{" "}
              {a.group === "helper" ? "" : "Which agent a built-in state runs is fixed for now."}
            </div>
          </div>
        </div>
      </div>
    );
  return (
    <div className="card card-b">
      <div className="field">
        <div>
          <div className="lab">Runs</div>
        </div>
        <div>
          <b>{triggerWords(a, page.rows)}</b>
          <div className="muted" style={{ marginTop: 6 }}>It only reads; what it hands back waits for you on Up next.</div>
        </div>
      </div>
      {t.schedule && (
        <Part ctx={ctx} path="trigger.schedule.hours" label="Every" hint="Hours between two runs here, counted from the last.">
          <NumberField ctx={ctx} path="trigger.schedule.hours" />
          <span className="muted" style={{ alignSelf: "center" }}>hours</span>
        </Part>
      )}
      {t.event?.after_hours !== undefined && (
        <Part ctx={ctx} path="trigger.event.after_hours" label="Wait" hint="Hours after the event before it runs.">
          <NumberField ctx={ctx} path="trigger.event.after_hours" />
          <span className="muted" style={{ alignSelf: "center" }}>hours</span>
        </Part>
      )}
    </div>
  );
}

/** The two things an owner does with an agent that runs by itself: turn it on or off here, and run it now. */
function Controls(ctx: Ctx) {
  const { a } = ctx;
  const input = ctx.value("input") as Input | undefined;
  const readsUnit = Boolean(input?.artifacts.length || input?.outputs.length);
  const t = a.row.trigger ?? {};
  return (
    <div className="row agent-controls" style={{ gap: 12, marginTop: 14, flexWrap: "wrap" }}>
      {a.on !== null && <OnHere {...ctx} />}
      {t.manual && !readsUnit && <RunNowButton {...ctx} />}
      {t.manual && readsUnit && <span className="faint" style={{ fontSize: 12.5 }}>It reads one unit: run it from that unit's page.</span>}
    </div>
  );
}

/** On or off in this workspace: whether its schedule or event runs it here. */
function OnHere({ a, cwd, setPage }: Ctx) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = async (on: boolean) => {
    setBusy(true);
    setError("");
    try {
      setPage(await api.post<Page>("/api/agents/state", { cwd, key: a.key, on }));
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="seg" role="group" aria-label="On or off in this workspace" title={a.on ? "Runs on its own here." : "Runs here only when you press Run now."}>
        <button className={a.on ? "on" : ""} aria-pressed={!!a.on} disabled={busy} onClick={() => set(true)}>On</button>
        <button className={a.on ? "" : "on"} aria-pressed={!a.on} disabled={busy} onClick={() => set(false)}>Off</button>
      </span>
      {error && <span className="field-err">{error}</span>}
    </span>
  );
}

/** One paid run now, asked once with its ceiling named and an optional note; once started, a link
 * to its live page. With no run going, a link to ask its last run a question. */
function RunNowButton({ a, cwd, workspace }: Ctx) {
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState("");
  const [error, setError] = useState("");
  const usd = a.config.ceilings.max_budget_usd;
  const run = async () => {
    if (!asking) return setAsking(true);
    setBusy(true);
    setError("");
    try {
      const got = await api.post<{ run: string }>("/api/agents/run", { cwd, key: a.key, text: note.trim() });
      setStarted(got.run);
      setAsking(false);
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };
  const live = a.running ? a.running.run : started;
  // The newest run that kept a session and was not stopped: the one a question can be put to.
  const last = live ? "" : (a.groups.flatMap((g) => g.runs).filter((r) => r.run && r.session && r.outcome !== "cancelled").sort((x, y) => y.at.localeCompare(x.at))[0]?.run ?? "");
  return (
    <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
      {asking && (
        <input
          className="input"
          style={{ width: 280 }}
          aria-label="A note for this run"
          placeholder="A note for this run (optional)"
          value={note}
          maxLength={4000}
          disabled={busy}
          onChange={(e) => setNote(e.target.value)}
        />
      )}
      <Button size="sm" kind={asking ? "primary" : ""} icon="bolt" disabled={busy || !!a.running} onClick={run}>
        {busy ? "Starting…" : a.running ? "Running" : asking ? `Spend up to ${money(usd ?? 0)}?` : "Run now"}
      </Button>
      {asking && !busy && (
        <Button size="sm" kind="ghost" onClick={() => setAsking(false)}>
          Cancel
        </Button>
      )}
      {live && workspace && (
        <Link to={`/run/${workspace}/${live}`} className="live-link">
          {a.running ? <><span className="dot live" /> Watch it live ▸</> : "Open its run ▸"}
        </Link>
      )}
      {last && workspace && !asking && (
        <Link to={`/run/${workspace}/${last}?ask=1`} className="live-link">
          Ask its last run ▸
        </Link>
      )}
      {error && <span className="field-err">{error}</span>}
    </span>
  );
}

/** What a run of the agent would be given now: its role and what its input declares, read from
 * the app. A preview; it starts nothing and costs nothing. */
function Receives({ a, cwd }: Ctx) {
  const got = useResource("/api/agents/{key}/prompt", { key: a.key, cwd });
  return (
    <>
      <div className="sec-h">What it receives <span className="faint">a preview, not a run</span></div>
      <div className="card card-b">
        {got.state === "error" ? (
          <div className="field-err">Could not read it: {String((got.error as Error)?.message ?? "")}</div>
        ) : !got.data ? (
          <div className="faint">Reading…</div>
        ) : (
          <>
            <div className="faint" style={{ marginBottom: 4 }}>Its role, given first</div>
            <pre className="prompt-preview">{got.data.system || "(nothing)"}</pre>
            {got.data.task && (
              <>
                <div className="faint" style={{ margin: "10px 0 4px" }}>What it is handed to work on</div>
                <pre className="prompt-preview">{got.data.task}</pre>
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}

function Prompt(ctx: Ctx) {
  const { a } = ctx;
  return (
    <>
      <Receives {...ctx} />
      <div className="sec-h">System prompt</div>
      <div className="card card-b">
        <TextPart ctx={ctx} field="body" label="Its role" hint="Given to every run before anything else." rows={6} />
      </div>
      <div className="sec-h">
        Skills <span className="faint">the rules it is given for its stage; a skill's text is shared by every agent that names it</span>
      </div>
      <div className="card card-b">
        {a.skills.length === 0 && <div className="faint">It names no skill.</div>}
        {a.skills.map((s) => (
          <TextPart key={s.name} ctx={ctx} field={`skill:${s.name}`} label={s.name} rows={18} mono />
        ))}
      </div>
    </>
  );
}

function TextPart({ ctx, field, label, hint, rows, mono }: { ctx: Ctx; field: string; label: string; hint?: string; rows: number; mono?: boolean }) {
  const v = ctx.value(field);
  return (
    <Part ctx={ctx} path={field} label={label} hint={hint}>
      <textarea
        className={`editor ${mono ? "mono" : ""}`}
        rows={rows}
        value={typeof v === "string" ? v : ""}
        disabled={!ctx.editable}
        spellCheck={!mono}
        onChange={(e) => ctx.edit(field, e.target.value)}
      />
    </Part>
  );
}
