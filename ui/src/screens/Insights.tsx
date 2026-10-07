// How each project did over 30 days against the owner's targets: what a shipped unit cost, how
// many review rounds it took, where the money went by day and by agent, and what was spent again.
// Every figure names the units behind it, so a number can be checked, not only read.

import { useState } from "react";
import type { AgentSpend, Outcomes, Insights as View, Target } from "../api.gen";
import { useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { stageLabel } from "../lib/pack";
import { ago, money, unitCode, unitTitle } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Link } from "../lib/router";
import { Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const WASTE: Record<string, string> = {
  failed: "Runs that failed",
  "run-again": "Stages run again",
  "changes-requested": "Review rounds that asked for changes",
  "integrate-conflict": "Integrations with a conflict",
  "integrate-other": "Other integrations",
  "integrate-not-recorded": "Integrations not recorded",
};

const number = (unit: string) => Number(unit.slice(0, 4));
// Units a target card names; the shipped list below has every one.
const WORST = 5;

/** The last `n` days, oldest first, each with its spend or zero. */
export function lastDays(by: { day: string; usd: number | null }[], n: number, today = new Date()): { day: string; usd: number }[] {
  const spent = Object.fromEntries(by.map((d) => [d.day, d.usd ?? 0]));
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (n - 1 - i));
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { day, usd: spent[day] ?? 0 };
  });
}

export function Insights() {
  const { boards, loading } = useBoards();
  const [project, setProject] = useState("");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  return (
    <div className="page mid">
      <PageHead title="Insights" lede="The last 30 days against your targets: $15 a shipped unit, 1.5 review rounds, every outcome graded and met. Every figure names its units." />
      {loading ? (
        <SkeletonRows rows={5} />
      ) : (
        <>
          <div className="seg" style={{ marginTop: 16 }}>
            {boards.map((b) => (
              <button key={b.workspace.path} className={workspace?.path === b.workspace.path ? "on" : ""} onClick={() => setProject(b.workspace.name)}>
                {b.workspace.name}
              </button>
            ))}
          </div>
          {workspace && <Project key={workspace.path} workspace={workspace} />}
        </>
      )}
    </div>
  );
}

function Project({ workspace }: { workspace: Workspace }) {
  const view = useResource("/api/insights", { cwd: workspace.path }, { on: ["step.ended", "integration.ended"] });
  if (view.state === "error") return <ErrorState error={view.error} onRetry={view.reload} />;
  if (!view.data) return <SkeletonRows rows={5} />;
  const v = view.data;
  if (!v.recording)
    return (
      <Empty icon="chart" title="Nothing recorded">
        {workspace.name} has no run log yet.
      </Empty>
    );
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 20 }}>
        {v.targets.map((t) => (
          <TargetCard key={t.name} target={t} shipped={v.shipped.length} workspace={workspace.name} />
        ))}
      </div>
      <OutcomeCards o={v.outcomes} workspace={workspace.name} />
      <Days view={v} />
      <div className="sec-h">
        By agent <span className="faint">open one to see its runs</span>
      </div>
      <Agents rows={v.by_agent} workspace={workspace} />
      <div className="sec-h">Spent again</div>
      <div className="card">
        {v.waste.map((w) => (
          <div key={w.kind} className="ny">
            <div className="grow" style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{WASTE[w.kind] ?? w.kind}</div>
              {w.not_recorded > 0 && <div className="faint" style={{ fontSize: 12 }}>{w.not_recorded} with no money recorded</div>}
            </div>
            <span className="faint nowrap">{w.count}</span>
            <b className="nowrap" style={{ width: 72, textAlign: "right" }}>{money(w.usd)}</b>
          </div>
        ))}
      </div>
      <div className="sec-h">
        Shipped <span className="faint">{v.shipped.length || ""}</span>
      </div>
      <div className="card">
        {v.shipped.map((s) => (
          <Link key={s.unit} to={`/unit/${workspace.name}/${number(s.unit)}`} className="lrow">
            <span className="id">{unitCode(workspace.name, number(s.unit))}</span>
            <span className="t">{unitTitle(s.unit)}</span>
            <span className="meta">
              {s.outcome && <Chip square tone={s.outcome === "met" ? "green" : s.outcome === "not-met" ? "red" : "amber"}>{OUTCOME[s.outcome] ?? s.outcome}</Chip>}
              <span style={{ color: (s.usd ?? 0) > 15 ? "var(--amber)" : undefined }}>{money(s.usd)}</span> · {s.rounds} round{s.rounds === 1 ? "" : "s"}
            </span>
          </Link>
        ))}
        {!v.shipped.length && <div className="card-b faint">Nothing shipped in {v.days} days.</div>}
      </div>
    </>
  );
}

const OUTCOME: Record<string, string> = { met: "met", "not-met": "not met", unclear: "unclear" };
const pct = (n: number, of: number) => (of ? `${Math.round((n / of) * 100)}%` : "—");

/** The two outcome targets: every shipped unit graded within 7 days of its week of waiting, and
 * most of those graded met. The units that missed are named. */
function OutcomeCards({ o, workspace }: { o: Outcomes; workspace: string }) {
  const late = o.due > o.on_time;
  const short = o.graded > 0 && o.met / o.graded < o.target_met;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
      <div className="card card-b">
        <div className="faint" style={{ fontSize: 12.5 }}>Outcome graded within 7 days</div>
        <div className="row" style={{ alignItems: "baseline", gap: 8, marginTop: 4 }}>
          <b style={{ fontSize: 22, color: late ? "var(--amber)" : undefined }}>{pct(o.on_time, o.due)}</b>
          <span className="faint">
            {o.on_time} of {o.due} due · target {pct(o.target_graded, 1)}
          </span>
        </div>
        <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
          {o.due ? (late ? "Grade the rest from each unit's page." : "Every unit due was graded in time.") : "No unit has waited its week yet."}
        </div>
      </div>
      <div className="card card-b">
        <div className="faint" style={{ fontSize: 12.5 }}>Outcome met</div>
        <div className="row" style={{ alignItems: "baseline", gap: 8, marginTop: 4 }}>
          <b style={{ fontSize: 22, color: short ? "var(--amber)" : undefined }}>{pct(o.met, o.graded)}</b>
          <span className="faint">
            {o.met} of {o.graded} graded · target {pct(o.target_met, 1)}
          </span>
        </div>
        <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
          {o.missed.length ? (
            <>
              {o.missed.length} short:{" "}
              {o.missed.slice(0, WORST).map((u, i) => (
                <span key={u}>
                  {i > 0 && ", "}
                  <Link to={`/unit/${workspace}/${number(u)}`}>{unitCode(workspace, number(u))}</Link>
                </span>
              ))}
              {o.missed.length > WORST && "…"}
            </>
          ) : o.graded ? (
            "Every graded unit met its outcome."
          ) : (
            "Nothing graded yet."
          )}
        </div>
      </div>
    </div>
  );
}

function TargetCard({ target: t, shipped, workspace }: { target: Target; shipped: number; workspace: string }) {
  const cost = t.name === "cost";
  const over = t.value != null && t.value > t.target;
  return (
    <div className="card card-b">
      <div className="faint" style={{ fontSize: 12.5 }}>{cost ? "A shipped unit costs" : "Review rounds a unit takes"}</div>
      <div className="row" style={{ alignItems: "baseline", gap: 8, marginTop: 4 }}>
        <b style={{ fontSize: 22, color: over ? "var(--amber)" : undefined }}>{t.value == null ? "—" : cost ? money(t.value) : t.value}</b>
        <span className="faint">
          median of {shipped} · target {cost ? money(t.target, 0) : t.target}
        </span>
      </div>
      <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
        {t.over.length ? (
          <>
            {t.over.length} of {shipped} over; worst:{" "}
            {t.over.slice(0, WORST).map((u, i) => (
              <span key={u}>
                {i > 0 && ", "}
                <Link to={`/unit/${workspace}/${number(u)}`}>{unitCode(workspace, number(u))}</Link>
              </span>
            ))}
            {t.over.length > WORST && "…"}
          </>
        ) : shipped ? (
          "Every shipped unit is within it."
        ) : (
          "Nothing shipped yet."
        )}
      </div>
    </div>
  );
}

function Days({ view }: { view: View }) {
  const days = lastDays(view.by_day, view.days);
  const top = Math.max(1, ...days.map((d) => d.usd));
  const total = days.reduce((a, d) => a + d.usd, 0);
  return (
    <>
      <div className="sec-h">
        Spend by day <span className="faint">{money(total, 0)} in {view.days} days</span>
      </div>
      <div className="card card-b">
        <div className="days">
          {days.map((d) => (
            <div key={d.day} className="day" title={`${d.day}: ${money(d.usd)}`}>
              <i style={{ height: `${Math.round((d.usd / top) * 100)}%` }} />
            </div>
          ))}
        </div>
        <div className="row faint" style={{ fontSize: 11.5, marginTop: 4 }}>
          <span className="grow">{days[0].day}</span>
          <span>today</span>
        </div>
      </div>
    </>
  );
}

/** Why an agent's row lists no runs: its steps kept a cost but no run log to open. */
export function noRuns(r: Pick<AgentSpend, "steps">): string {
  return `${r.steps} of its steps recorded their cost but not what they did, so there is no run to open.`;
}

function Agents({ rows, workspace }: { rows: AgentSpend[]; workspace: Workspace }) {
  const top = Math.max(1, ...rows.map((r) => r.usd ?? 0));
  if (!rows.length) return <div className="card card-b faint">No agent has run in these days.</div>;
  return (
    <div className="card">
      {rows.map((r) => (
        <details key={r.agent} id={`agent-${r.agent}`} className="agent">
          <summary className="lrow">
            <span style={{ width: 96 }}>{stageLabel(r.agent)}</span>
            <div className="grow" style={{ background: "var(--bg-sunk)", borderRadius: 3, height: 8 }}>
              <div style={{ width: `${((r.usd ?? 0) / top) * 100}%`, background: "var(--accent)", height: 8, borderRadius: 3 }} />
            </div>
            <b className="nowrap" style={{ width: 72, textAlign: "right" }}>{money(r.usd ?? 0)}</b>
            <span className="faint nowrap" style={{ width: 150, fontSize: 12 }}>
              {r.steps} run{r.steps === 1 ? "" : "s"}{r.unknown ? `, ${r.unknown} cost unknown` : ""}
            </span>
          </summary>
          <div className="agent-runs">
            {r.runs.map((run) => (
              <Link key={run.run} to={`/run/${workspace.name}/${run.run}`} className="lrow">
                <span className="id">{run.unit ? unitCode(workspace.name, number(run.unit)) : "—"}</span>
                <span className="t">{run.unit ? unitTitle(run.unit) : "No unit"}</span>
                <span className="meta">
                  {run.outcome !== "done" && (
                    <Chip square tone="red">
                      {run.outcome}
                    </Chip>
                  )}
                  <span>{money(run.usd)}</span>
                  <span title={run.at}>{ago(run.at)}</span>
                </span>
              </Link>
            ))}
            {!r.runs.length && <div className="card-b faint">{noRuns(r)}</div>}
          </div>
        </details>
      ))}
    </div>
  );
}
