// The team: every agent the app runs, one line each, grouped by what opens it. A line says when
// the agent runs, on what, what it cost in 30 days and whether anything needs a look; the agent's
// own page (`AgentPage.tsx`) changes any part of it.

import { useEffect, useState } from "react";
import type { AgentRow } from "../api.gen";
import { api, useResource } from "../lib/api";
import { LeifAvatar, Rune } from "../lib/icons";
import { ago, modelName, money, until } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Link, navigate, useQuery } from "../lib/router";
import { isBuiltIn, packTitle, type BuildAgent } from "../lib/build";
import { NewAgent } from "../components/NewAgent";
import { Button, Chip, ErrorState, PageHead, SkeletonRows } from "../components/ui";

export const GROUPS: { key: AgentRow["group"]; title: string; lede: string }[] = [
  { key: "triggered", title: "Periodic and on request", lede: "A schedule, an event, your press or Leif starts these; they only read." },
  { key: "stage", title: "Stage agents", lede: "Each opens when a unit reaches its state." },
  { key: "engine", title: "Engine agents", lede: "The app opens these itself, or on your press." },
  { key: "helper", title: "Helpers", lede: "Started by another agent inside its run." },
];

const ENGINE_WORDS: Record<string, string> = {
  integrate: "when a pull request conflicts or its CI goes red",
  estimate: "when you press Propose estimates",
  chat: "when you talk to Leif",
};

const EVENT_WORDS: Record<string, string> = {
  "unit.shipped": "a ship",
  "unit.merged": "a merge",
  "chat-turn.ended": "a chat turn",
};

/** The name of the agent `key`, from `rows`, else the key. */
const nameOf = (key: string, rows: AgentRow[]) => rows.find((r) => r.key === key)?.row.name ?? key;

/** How often a schedule runs: `24` is "daily", `168` "weekly", `48` "every 2 days", `6` "every 6 hours". */
export function everyWords(h: number): string {
  if (h === 24) return "daily";
  if (h === 168) return "weekly";
  return h % 24 ? `every ${h} hours` : `every ${h / 24} days`;
}

/** `168` is "7 days", `24` "1 day", `6` "6 h". */
export function hoursWords(h: number): string {
  if (h % 24) return `${h} h`;
  return h === 24 ? "1 day" : `${h / 24} days`;
}

/** When an agent runs, in words: its trigger, or who starts a helper. */
export function triggerWords(a: AgentRow, rows: AgentRow[] = []): string {
  const t = a.row.trigger ?? {};
  if (t.state) return `when a unit reaches ${t.state.split(" ")[0]}`;
  if (t.engine) return ENGINE_WORDS[t.engine] ?? `by the engine (${t.engine})`;
  const said: string[] = [];
  if (t.schedule) said.push(everyWords(t.schedule.hours));
  if (t.event?.from) {
    const who = nameOf(t.event.from, rows);
    said.push(t.event.after_hours ? `${hoursWords(t.event.after_hours)} after ${who} ends` : `after ${who}`);
  } else if (t.event) {
    const what = EVENT_WORDS[t.event.name ?? ""] ?? (t.event.name ?? "").replace(/[.-]/g, " ");
    said.push(t.event.after_hours ? `${hoursWords(t.event.after_hours)} after ${what}` : `on ${what}`);
  }
  if (t.manual || t.leif) said.push("when you or Leif ask");
  if (said.length) return said.join(", ");
  if (a.group === "helper") {
    const by = rows.filter((r) => (r.row.helpers ?? []).includes(a.key)).map((r) => r.row.name ?? r.key);
    return by.length ? `started by ${by.join(", ")}` : "started by no agent";
  }
  return "nowhere yet";
}

/** What needs a look on a line, worst first: a problem stops its runs, then the last run's chip. */
export function attention(a: AgentRow): { tone: "red" | "amber"; label: string } | null {
  if (a.problems.length) return { tone: "red", label: "Cannot run" };
  // A run in flight is the news: its last run's chip waits.
  if (a.running) return null;
  if (a.chip === "failed") return { tone: "red", label: "Last run failed" };
  if (a.chip === "paused") return { tone: "amber", label: "Paused at its ceiling" };
  if (a.chip === "costly") return { tone: "amber", label: "Near its $ ceiling" };
  return null;
}

/** Whether the agent is on or off here, and why: the first part of `statusWords`. */
export function onHere(a: AgentRow, here: string): string {
  if (a.on === null) return "Always on";
  const held = a.on && a.off_reason ? ` · ${a.off_reason}` : "";
  const elsewhere = a.on_in.filter((n) => n !== here);
  const also = elsewhere.length ? ` (${a.on ? "also on" : "on"} in ${elsewhere.join(", ")})` : "";
  return a.on ? `On here${also}${held}` : `Off here${a.off_reason ? `: ${a.off_reason}` : ""}${also}`;
}

const RAN: Record<string, string> = { failed: "failed", cancelled: "stopped", stopped: "stopped", "paused-budget": "paused" };

/** Where an agent stands, in a line: on or off here, its last run and what it made, its next run. */
export function statusWords(a: AgentRow, here: string): string {
  const said = [onHere(a, here)];
  if (a.running) said.push("running now");
  else if (a.last) said.push(`${a.last.skipped ? "skipped" : RAN[a.last.outcome ?? ""] ?? "ran"} ${ago(a.last.at)}${a.last.made != null ? `, last run proposed ${a.last.made}` : ""}`);
  else said.push("never ran");
  if (a.next_at && !a.running) said.push(`next ${until(a.next_at)}`);
  return said.join(" · ");
}

/** The address of a run the app holds now, which the run page follows live. */
export const liveRun = (a: AgentRow, workspace?: Workspace) => (a.running && workspace ? `/run/${workspace.name}/${a.running.run}` : "");

export function AgentGlyph({ a, size = "" }: { a: AgentRow; size?: "" | "lg" | "xl" }) {
  if (a.key === "leif") return <LeifAvatar size={size} />;
  return (
    <span className={`av ${size}`} title={a.row.name}>
      <Rune glyph={a.row.glyph || (a.row.name ?? a.key).slice(0, 1)} size={size === "xl" ? 20 : size === "lg" ? 16 : 12} />
    </span>
  );
}

/**
 * The workspace a team page reads: the one `?ws=<name>` names, else the one Dagaz's run
 * `?draft=<run>` ran in (`ofRun`, `null` while still asked), else the first.
 */
export function pickWorkspace<W extends { name: string; path: string }>(list: W[], named: string, ofRun: string | null): W | undefined {
  const byName = list.find((w) => w.name === named);
  if (byName || ofRun === null) return byName;
  return list.find((w) => w.path === ofRun) ?? list[0];
}

/** The path of the workspace whose run `run` is: `""` for no run or none found, `null` while asking each. */
export function useRunWorkspace(run: string, list: { path: string }[]): string | null {
  const [found, setFound] = useState<{ run: string; path: string } | null>(null);
  const key = list.map((w) => w.path).join("|");
  useEffect(() => {
    if (!run || !list.length) return;
    let live = true;
    Promise.all(list.map((w) => api.get("/api/runs/{run}", { cwd: w.path, run, limit: "1" }).then(() => w.path, () => ""))).then(
      (got) => live && setFound({ run, path: got.find(Boolean) ?? "" }),
    );
    return () => {
      live = false;
    };
    // `key` stands for the list.
  }, [run, key]);
  if (!run) return "";
  return found?.run === run ? found.path : null;
}

/** `?ws=<name>` for an address of the team pages. */
export const inWorkspace = (to: string, w?: Workspace) => (w ? `${to}?ws=${encodeURIComponent(w.name)}` : to);

/** The agents of the workspace the address names: one read gives every row and the catalog. */
export function useAgents(only = "") {
  const ws = useResource("/api/workspaces");
  const list = ws.data?.workspaces ?? [];
  // The chosen project stays chosen while the person moves between pages.
  const urlNamed = useQuery("ws");
  const draft = useQuery("draft");
  const named = urlNamed || (draft ? "" : sessionStorage.getItem("agents.ws") || "");
  const ofRun = useRunWorkspace(named ? "" : draft, list);
  const workspace = pickWorkspace(list, named, ofRun);
  useEffect(() => {
    if (workspace) sessionStorage.setItem("agents.ws", workspace.name);
  }, [workspace?.name]);
  const agents = useResource(workspace ? "/api/agents" : null, workspace ? { cwd: workspace.path, ...(only ? { agent: only } : {}) } : {}, { on: ["agent-run."], every: 60_000, wait: 0 });
  return { ws, list, workspace, cwd: workspace?.path ?? "", agents };
}

/** Which project's agents the page shows; drawn only when there is more than one. */
export function WorkspaceSwitch({ list, workspace, to }: { list: Workspace[]; workspace?: Workspace; to: string }) {
  if (list.length < 2 || !workspace) return null;
  return (
    <label className="row faint" style={{ gap: 6, fontSize: 12.5 }}>
      Project
      <select className="input sm" style={{ width: "auto", maxWidth: 240 }} value={workspace.name} onChange={(e) => navigate(`${to}?ws=${encodeURIComponent(e.target.value)}`)}>
        {list.map((w) => (
          <option key={w.path} value={w.name}>
            {w.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * The section an agent lists under: what opens it, whoever made it. One of yours or of an
 * imported pack that nothing opens yet (no state, trigger or agent starts it) has none: it lists
 * under its pack.
 */
export function groupOf(a: AgentRow): AgentRow["group"] | null {
  if (isBuiltIn(a as BuildAgent) || a.group !== "engine" || a.row.trigger?.engine) return a.group;
  return null;
}

export function Agents() {
  const { list, workspace, cwd, agents } = useAgents();
  // `?draft=<run>` opens the dialog on Dagaz's run, as Leif hands it over.
  const run = useQuery("draft");
  const [adding, setAdding] = useState(Boolean(run));
  const rows = (agents.data?.rows ?? []) as BuildAgent[];
  const look = rows.filter((a) => attention(a));
  const idle = rows.filter((a) => groupOf(a) === null);
  const packs = [...new Set(idle.map(packTitle))].sort((a, b) => (a === "Yours" ? -1 : b === "Yours" ? 1 : a.localeCompare(b)));
  return (
    <div className="page" style={{ maxWidth: 1040 }}>
      <div className="agents-head">
        <PageHead
          title="Agents"
          lede="Every agent the app runs: when it runs, on what model, what it may do and what it cost. Open one to change any part; its next run uses the change."
        />
        <div className="agents-tools">
          <WorkspaceSwitch list={list} workspace={workspace} to="/agents" />
          <Button kind="primary" icon="plus" disabled={!agents.data} onClick={() => setAdding(true)}>New agent</Button>
        </div>
      </div>
      {adding && agents.data && <NewAgent rows={rows} catalog={agents.data.catalog} cwd={cwd} run={run || undefined} onClose={() => setAdding(false)} />}
      {agents.state === "error" ? (
        <ErrorState error={agents.error} onRetry={agents.reload} />
      ) : !agents.data ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          {look.length > 0 && (
            <div className="card card-b attn" style={{ marginTop: 16 }}>
              <b>Needs a look</b>
              {look.map((a) => (
                <Link key={a.key} to={inWorkspace(`/agents/${a.key}`, workspace)} className="attn-row">
                  <Chip square tone={attention(a)!.tone}>{attention(a)!.label}</Chip>
                  <span>{a.row.name ?? a.key}</span>
                  <span className="faint">{a.problems[0] ?? "open it to see its runs"}</span>
                </Link>
              ))}
            </div>
          )}
          {GROUPS.map((g) => {
            const mine = rows.filter((a) => groupOf(a) === g.key);
            if (!mine.length) return null;
            return (
              <section key={g.key}>
                <div className="sec-h">
                  {g.title} <span className="faint">{g.lede}</span>
                </div>
                <div className="card">
                  {mine.map((a) => (
                    <AgentLine key={a.key} a={a} rows={rows} workspace={workspace} />
                  ))}
                </div>
              </section>
            );
          })}
          {packs.map((title) => (
            <section key={title}>
              <div className="sec-h">
                {title} <span className="faint">Nothing runs these yet: put one in a process or give it a trigger.</span>
              </div>
              <div className="card">
                {idle.filter((a) => packTitle(a) === title).map((a) => (
                  <AgentLine key={a.key} a={a} rows={rows} workspace={workspace} />
                ))}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

function AgentLine({ a, rows, workspace }: { a: AgentRow; rows: AgentRow[]; workspace?: Workspace }) {
  const look = attention(a);
  const live = liveRun(a, workspace);
  const whose = isBuiltIn(a as BuildAgent) ? "" : packTitle(a as BuildAgent);
  return (
    <Link to={inWorkspace(`/agents/${a.key}`, workspace)} className="agent-line">
      <AgentGlyph a={a} />
      <span className="who">
        <b>{a.row.name ?? a.key}</b> <span className="faint mono">{a.key}</span>
        <span className="when">{[statusWords(a, workspace?.name ?? ""), triggerWords(a, rows)].filter(Boolean).join(" · ")}</span>
      </span>
      <span className="what">
        {modelName(a.config.model)}
        {a.config.effort ? ` · ${a.config.effort}` : ""}
      </span>
      <span className="cost">
        {a.runs_30d ? (
          <>
            <b>{money(a.cost_30d)}</b> <span className="faint">· {a.runs_30d} run{a.runs_30d === 1 ? "" : "s"}</span>
          </>
        ) : (
          <span className="faint">no runs in 30 d</span>
        )}
      </span>
      <span className="marks">
        {a.running && (
          <Chip square tone="accent">
            <span className="dot live" /> running
            {live && (
              <span
                role="link"
                tabIndex={0}
                className="live-link"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(live);
                }}
              >
                {" "}▸ live
              </span>
            )}
          </Chip>
        )}
        {whose && <Chip square tone={whose === "Yours" ? "accent" : "plain"}>{whose === "Yours" ? "yours" : whose}</Chip>}
        {look && <Chip square tone={look.tone}>{look.label}</Chip>}
        {a.edited.length > 0 && <Chip square tone="accent">edited</Chip>}
      </span>
    </Link>
  );
}
