// The team: every agent the app runs, one line each, grouped by what opens it. A line says when
// the agent runs, on what, what it cost in 30 days and whether anything needs a look; the agent's
// own page (`AgentPage.tsx`) changes any part of it.

import { useEffect, useState } from "react";
import type { AgentRow } from "../api.gen";
import { api, useResource } from "../lib/api";
import { LeifAvatar, Rune } from "../lib/icons";
import { modelName, money } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Link, navigate, useQuery } from "../lib/router";
import { isBuiltIn, packTitle, type BuildAgent } from "../lib/build";
import { NewAgent } from "../components/NewAgent";
import { Button, Chip, ErrorState, PageHead, SkeletonRows } from "../components/ui";

export const GROUPS: { key: AgentRow["group"]; title: string; lede: string }[] = [
  { key: "stage", title: "Stage agents", lede: "Each opens when a unit reaches its state." },
  { key: "engine", title: "Engine agents", lede: "The app opens these itself, or on your press." },
  { key: "helper", title: "Helpers", lede: "Started by another agent inside its run." },
  { key: "triggered", title: "Periodic and on request", lede: "A schedule, an event, your press or Leif starts these; they only read." },
];

const ENGINE_WORDS: Record<string, string> = {
  integrate: "when a pull request conflicts or its CI goes red",
  estimate: "when you press Propose estimates",
  chat: "when you talk to Leif",
};

const EVENT_WORDS: Record<string, string> = { "unit.shipped": "a ship" };

/** `168` is "7 days", `24` "1 day", `6` "6 h". */
export function hoursWords(h: number): string {
  if (h % 24) return `${h} h`;
  return h === 24 ? "1 day" : `${h / 24} days`;
}

/** When an agent runs, in words: its trigger, or who starts a helper. */
export function triggerWords(a: AgentRow, rows: AgentRow[] = []): string {
  const t = a.row.trigger ?? {};
  if (t.state) return `on state ${t.state}`;
  if (t.engine) return ENGINE_WORDS[t.engine] ?? `by the engine (${t.engine})`;
  const said: string[] = [];
  if (t.schedule) said.push(`every ${t.schedule.hours} h`);
  if (t.event) {
    const what = EVENT_WORDS[t.event.name ?? ""] ?? t.event.name;
    said.push(t.event.after_hours ? `${hoursWords(t.event.after_hours)} after ${what}` : `on ${what}`);
  }
  if (t.manual || t.leif) said.push("on request");
  if (said.length) return said.join(", ");
  if (a.group === "helper") {
    const by = rows.filter((r) => (r.row.helpers ?? []).includes(a.key)).map((r) => r.row.name ?? r.key);
    return by.length ? `started by ${by.join(", ")}` : "started by no agent";
  }
  return "—";
}

/** What needs a look on a line, worst first: a problem stops its runs, then the last run's chip. */
export function attention(a: AgentRow): { tone: "red" | "amber"; label: string } | null {
  if (a.problems.length) return { tone: "red", label: "Cannot run" };
  if (a.chip === "failed") return { tone: "red", label: "Last run failed" };
  if (a.chip === "costly") return { tone: "amber", label: "Near its $ ceiling" };
  return null;
}

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
export function useAgents() {
  const ws = useResource("/api/workspaces");
  const list = ws.data?.workspaces ?? [];
  const named = useQuery("ws");
  const ofRun = useRunWorkspace(named ? "" : useQuery("draft"), list);
  const workspace = pickWorkspace(list, named, ofRun);
  const agents = useResource(workspace ? "/api/agents" : null, workspace ? { cwd: workspace.path } : {});
  return { ws, list, workspace, cwd: workspace?.path ?? "", agents };
}

/** Which project's agents the page shows; drawn only when there is more than one. */
export function WorkspaceSwitch({ list, workspace, to }: { list: Workspace[]; workspace?: Workspace; to: string }) {
  if (list.length < 2 || !workspace) return null;
  return (
    <select className="input sm" aria-label="Project" value={workspace.name} onChange={(e) => navigate(`${to}?ws=${encodeURIComponent(e.target.value)}`)}>
      {list.map((w) => (
        <option key={w.path} value={w.name}>
          {w.name}
        </option>
      ))}
    </select>
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
      <PageHead
        title="Agents"
        lede="Every agent the app runs: when it runs, on what model, what it may do and what it cost. Open one to change any part; its next run uses the change."
        actions={
          <>
            <WorkspaceSwitch list={list} workspace={workspace} to="/agents" />
            <Button kind="primary" icon="plus" disabled={!agents.data} onClick={() => setAdding(true)}>New agent</Button>
          </>
        }
      />
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
  const whose = isBuiltIn(a as BuildAgent) ? "" : packTitle(a as BuildAgent);
  return (
    <Link to={inWorkspace(`/agents/${a.key}`, workspace)} className="agent-line">
      <AgentGlyph a={a} />
      <span className="who">
        <b>{a.row.name ?? a.key}</b> <span className="faint mono">{a.key}</span>
        <span className="when">{triggerWords(a, rows)}</span>
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
        {whose && <Chip square tone={whose === "Yours" ? "accent" : "plain"}>{whose === "Yours" ? "yours" : whose}</Chip>}
        {look && <Chip square tone={look.tone}>{look.label}</Chip>}
        {a.edited.length > 0 && <Chip square tone="accent">edited</Chip>}
      </span>
    </Link>
  );
}
