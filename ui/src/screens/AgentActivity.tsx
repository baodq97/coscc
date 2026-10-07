// What an agent has done: is it on and working, what did it make, what did it cost. The first tab
// of an agent's page; the runs of the last 30 days follow, newest first, grouped by the definition
// they ran so an edit's effect can be compared.

import type { AgentPage as Page, AgentRow, RunGroup, RunView } from "../api.gen";
import { ago, money, until, startedBy, unitCode, unitTitle } from "../lib/format";
import { Link } from "../lib/router";
import { Chip, Empty } from "../components/ui";
import { onHere } from "./Agents";

const VERDICT: Record<string, string> = { met: "met", "not-met": "not met", unclear: "unclear" };
export const OUTCOME: Record<string, string> = { failed: "failed", "paused-budget": "paused at its ceiling", cancelled: "stopped", stopped: "stopped" };

/** What a run made, in words: "proposed 2", "verdict: met"; `""` for a run that makes nothing. */
/** "12 runs, 3 skipped": a skipped run is counted apart, as the tile counts it. */
export function runCount(runs: number | { skipped: boolean }[], skips = 0): string {
  const ran = typeof runs === "number" ? runs : runs.filter((r) => !r.skipped).length;
  const skipped = typeof runs === "number" ? skips : runs.length - ran;
  return `${ran} run${ran === 1 ? "" : "s"}${skipped ? `, ${skipped} skipped` : ""}`;
}

export function resultWords(r: Pick<RunView, "verdict" | "made">): string {
  if (r.verdict) return `verdict: ${VERDICT[r.verdict] ?? r.verdict}`;
  if (r.made == null) return "";
  return r.made === 0 ? "proposed nothing" : `proposed ${r.made}`;
}

/** Why a run counts as only partly checked, or `""`: calls it was refused, or a verdict it left unclear. */
export function shallowWords(r: { refused?: number | null; verdict?: string }): string {
  if (r.refused) return `${r.refused} call${r.refused === 1 ? " was" : "s were"} refused`;
  return r.verdict === "unclear" ? "left a criterion unclear" : "";
}

/** One run's row: where it opens (only a run with a kept log, or its unit), and what it says. A
 * skip and a run without a log are not links. */
export function runRow(r: RunView, ws: string): { to: string; code: string; title: string; muted: boolean } {
  const n = Number(r.unit.slice(0, 4));
  const code = r.unit ? unitCode(ws, n) : "—";
  if (r.skipped) return { to: "", code, title: `Skipped — ${r.detail || "nothing to do"}`, muted: true };
  const title = r.unit ? unitTitle(r.unit) : r.started_by ? `Run by ${startedBy(r.started_by)}` : "Run";
  if (r.run) return { to: `/run/${ws}/${r.run}`, code, title, muted: false };
  if (r.unit) return { to: `/unit/${ws}/${n}`, code, title, muted: false };
  // Before runs kept a log (or recorded who started them), who started it is not told.
  return { to: "", code, title: r.unit ? `${title} — no log kept` : "An earlier run — no log kept", muted: true };
}

/** What the agent made over the window: counts of its proposals by what became of them. */
export function madeWords(a: AgentRow): string {
  const said = [
    a.accepted_30d ? `${a.accepted_30d} accepted` : "",
    a.dismissed_30d ? `${a.dismissed_30d} dismissed` : "",
    a.pending ? `${a.pending} waiting for you` : "",
  ].filter(Boolean);
  return said.join(" · ");
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="stat">
      <div className="faint stat-l">{label}</div>
      <div className="stat-v">{children}</div>
    </div>
  );
}

const PART: Record<string, string> = { new: "agent", body: "role", ceilings: "ceilings", model: "model", tools: "tools", input: "input", output: "output", trigger: "trigger", skills: "skills", helpers: "helpers" };

function settingWords(s: RunGroup["settings"][number]): string {
  if (s.field === "new") return "created";
  if (s.field === "delete") return "deleted";
  const part = PART[s.field] ?? (s.field.startsWith("skill:") ? `${s.field.slice(6)} skill` : s.field);
  return s.new == null ? `${part} reset to built-in` : `${part} edited`;
}

/** Whether the agent is on and when it runs next, without its last run (the next stat has that). */
export function onWords(a: AgentRow, here: string): string {
  if (a.running) return "running now";
  if (a.on === null) return "Always on";
  return [onHere(a, here), a.next_at ? `next ${until(a.next_at)}` : ""].filter(Boolean).join(" · ");
}

export function Activity({ a, page, names, workspace }: { a: AgentRow; page: Page; names: Record<string, string>; workspace: string }) {
  const proposes = (a.row.output as { kind?: string } | undefined)?.kind === "proposal";
  const made = madeWords(a);
  const where = page.scope === "workspace" ? "this workspace" : "all workspaces";
  const last = a.last;
  return (
    <>
      <div className="stats">
        <Stat label="Status">
          {onWords(a, workspace)}
          {a.running && <> <span className="dot live" /></>}
        </Stat>
        <Stat label="Last run">
          {a.running ? "running now" : last ? `${last.skipped ? "skipped" : OUTCOME[last.outcome] ?? "done"} ${ago(last.at)}` : "never ran"}
          {last && !last.skipped && resultWords(last) && <div className="faint">{resultWords(last)}</div>}
        </Stat>
        {(proposes || made) && (
          <Stat label="Proposals in 30 days">
            {made || "no proposals yet"}
          </Stat>
        )}
        <Stat label="Cost in 30 days">
          <b>{money(a.cost_30d)}</b>
          <div className="faint">
            {runCount(a.runs_30d, a.skips_30d)} · {where}
          </div>
        </Stat>
      </div>
      {!a.groups.length ? (
        <Empty icon="clock" title="No runs in 30 days">
          A run shows here with what it made and the version of the agent it ran, so an edit's effect can be compared.
        </Empty>
      ) : (
        a.groups.map((g, i) => (
          <div key={i} className="card run-group">
            <div className="card-h">
              <span className="grow">
                {g.row_hash === a.row_hash ? "Current version" : g.row_hash ? "An earlier version" : g.runs.length ? "Earlier version, not recorded" : "Saved, not run yet"}{" "}
                {g.row_hash && <span className="faint mono" title={g.row_hash}>#{g.row_hash.slice(0, 6)}</span>}
              </span>
              <span className="faint" style={{ fontWeight: 500 }}>
                {runCount(g.runs)} · {money(g.cost_usd)} · {g.turns} turn{g.turns === 1 ? "" : "s"}
              </span>
            </div>
            {g.settings.length > 0 && (
              <div className="run-settings">
                {g.settings.map((s, j) => (
                  <span key={j} className="faint">
                    {settingWords(s)} {ago(s.at)}
                    {j < g.settings.length - 1 ? " · " : ""}
                  </span>
                ))}
              </div>
            )}
            {g.runs.map((r) => {
              const row = runRow(r, names[r.workspace] ?? "");
              const result = r.skipped ? "" : resultWords(r);
              const thin = r.skipped ? "" : shallowWords(r);
              const body = (
                <>
                  {r.unit && <span className="id">{row.code}</span>}
                  <span className="t" style={row.muted ? { color: "var(--text-3)", fontWeight: 400 } : undefined}>
                    {row.title}
                    {result && <span className="faint"> · {result}</span>}
                  </span>
                  <span className="meta">
                    {r.outcome !== "done" && <Chip square tone={r.outcome === "failed" ? "red" : "amber"}>{OUTCOME[r.outcome] ?? r.outcome}</Chip>}
                    {thin && <Chip square tone="amber" >partly checked: {thin}</Chip>}
                    {r.helpers ? <span>{r.helpers} helper{r.helpers === 1 ? "" : "s"}</span> : null}
                    {r.cost_usd != null ? money(r.cost_usd) : ""} · {ago(r.at)}
                  </span>
                </>
              );
              const key = r.workspace + r.unit + r.at + r.run;
              return row.to ? (
                <Link key={key} to={row.to} className="lrow stack">{body}</Link>
              ) : (
                <div key={key} className="lrow stack">{body}</div>
              );
            })}
          </div>
        ))
      )}
    </>
  );
}
