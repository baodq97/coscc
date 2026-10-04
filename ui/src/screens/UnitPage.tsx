// One unit: what it is, where it stands, what happened on it and what happens next. The side
// panel holds the facts; the timeline holds every run and answer, newest first.

import { Fragment, useState, type ReactNode } from "react";
import type { Answer, Detail, StageView, UnitRun } from "../api.gen";
import { api, useResource } from "../lib/api";
import type { PlacedUnit } from "../lib/boards";
import { allUnits, useBoards } from "../lib/boards";
import { STAGE_LABEL, ago, modelName, money, unitCode, unitTitle } from "../lib/format";
import { AgentAvatar, Icon, LeifMark } from "../lib/icons";
import { unitState } from "../lib/model";
import { Link } from "../lib/router";
import { Button, Chip, Dot, Empty, ErrorState, Meter, SkeletonRows } from "../components/ui";

const TRACK = ["intent", "spec", "plan", "impl", "pr", "review", "ship"];
const GROUP: Record<string, string> = { intent: "Shape", spec: "Shape", plan: "Shape", impl: "Build", pr: "Check", review: "Check", ship: "Ship" };
// The unit's own target, from the owner's standing goal: a merged unit costs $15 at most.
const TARGET_USD = 15;

export function UnitPage({ workspace, number }: { workspace: string; number: string }) {
  const { boards, loading } = useBoards();
  const placed = allUnits(boards).find((u) => u.workspace.name === workspace && u.number === Number(number));
  const query: Record<string, string> = placed ? { cwd: placed.workspace.path, name: placed.name } : {};
  const detail = useResource(placed ? "/api/units/{name}" : null, query, { on: [""] });
  const next = useResource(placed ? "/api/units/next" : null, placed ? { cwd: placed.workspace.path, unit: placed.name } : {}, { on: [""] });
  const agents = useResource(placed ? "/api/agents" : null, placed ? { cwd: placed.workspace.path } : {});
  const names = Object.fromEntries((agents.data?.rows ?? []).map((a) => [a.key, a.name]));
  const running = boards.flatMap((b) => b.board?.running ?? []).find((r) => r.unit === placed?.name);

  if (loading) return <div className="page"><SkeletonRows rows={5} /></div>;
  if (!placed)
    return (
      <div className="page">
        <Empty icon="search" title="No such unit">
          {workspace} has no unit {number}.
        </Empty>
      </div>
    );

  const state = unitState(placed);
  const d = detail.data;
  const open = d?.questions.filter((q) => !q.answered) ?? [];
  const now = running ? running.stage : next.data?.stage || "";
  const shipped = state.group === "Shipped";

  return (
    <div className="split">
      <div className="body">
        <div className="row" style={{ gap: 6 }}>
          <span className="faint">{unitCode(workspace, number)}</span>
          {placed.type && <Chip square tone="plain">{placed.type}</Chip>}
          {running ? (
            <Chip square tone="accent">
              <Dot tone="live" />
              {[names[running.stage] ?? running.agent, STAGE_LABEL[running.stage] ?? running.stage].filter(Boolean).join(" · ")}
            </Chip>
          ) : (
            <Chip square tone={state.group === "Needs you" ? "amber" : shipped ? "accent" : ""}>{state.label}</Chip>
          )}
        </div>
        <h1 className="title" style={{ marginTop: 10 }}>{unitTitle(placed.name)}</h1>
        {state.group !== "Dropped" && state.group !== "Ideas" && (
          <div style={{ marginTop: 20 }}>
            <Track stages={d?.stages ?? []} now={now} done={shipped} waiting={open.length > 0} />
          </div>
        )}
        {open.length > 0 && (
          <div className="callout amber" style={{ marginTop: 18 }}>
            <Icon name="chat" size={15} />
            <div className="grow">
              <b>
                {open.length} question{open.length > 1 ? "s" : ""} wait{open.length > 1 ? "" : "s"} on you
              </b>
              <div className="muted">{open[0].text.replace(/\*\*/g, "").slice(0, 140)}</div>
            </div>
            <Link to={`/inbox/${workspace}/${number}`} className="btn primary sm">
              Answer
            </Link>
          </div>
        )}
        {next.data && !shipped && !open.length && (
          <div className="callout" style={{ marginTop: 18 }}>
            <Icon name="arrow" size={15} />
            <div className="grow">
              <b>Next: {STAGE_LABEL[next.data.stage ?? ""] ?? "nothing to run"}</b>
              <div className="muted">{next.data.action}</div>
            </div>
          </div>
        )}
        <div className="tabs" style={{ marginTop: 22 }}>
          <a className="on">Activity</a>
        </div>
        {detail.state === "error" ? (
          <ErrorState error={detail.error} onRetry={detail.reload} />
        ) : !d ? (
          <SkeletonRows rows={4} />
        ) : (
          <Timeline detail={d} names={names} live={running?.stage} />
        )}
      </div>
      <aside className="props">
        <Prop k="Status">{running ? "Running" : state.label}</Prop>
        <Prop k="Project">{workspace}</Prop>
        <Prop k="Type">{placed.type || "—"}</Prop>
        <Prop k="Agent">
          {running && (names[running.stage] ?? running.agent) ? (
            <>
              <AgentAvatar stage={running.stage} />
              {names[running.stage] ?? running.agent}
            </>
          ) : running ? (
            <span className="faint">the app itself</span>
          ) : (
            <span className="faint">none working</span>
          )}
        </Prop>
        <h3>Cost</h3>
        <div className="row" style={{ alignItems: "baseline" }}>
          <b style={{ fontSize: 18 }}>{money(placed.cost_usd)}</b>
          <span className="grow" />
          <span className="faint" style={{ fontSize: 12, color: placed.cost_usd > TARGET_USD ? "var(--red)" : undefined }}>
            target {money(TARGET_USD, 0)}
          </span>
        </div>
        <Meter value={placed.cost_usd} max={TARGET_USD} />
        <h3>Pull request</h3>
        {placed.pr ? (
          <>
            <Prop k="PR">
              <a href={placed.pr.url} target="_blank" rel="noreferrer">
                #{placed.pr.number}
              </a>
            </Prop>
            <Prop k="Reviews">
              {d?.rounds.length ?? 0} round{d?.rounds.length === 1 ? "" : "s"}
              {d?.rounds.length ? (
                <Chip square tone={d.rounds[d.rounds.length - 1].verdict === "pass" ? "green" : "amber"}>
                  {d.rounds[d.rounds.length - 1].verdict === "pass" ? "pass" : "changes"}
                </Chip>
              ) : null}
            </Prop>
          </>
        ) : (
          <div className="faint" style={{ fontSize: 12.5 }}>Opens after build.</div>
        )}
        {d?.worktree?.branch && (
          <>
            <h3>Branch</h3>
            <div className="faint" style={{ fontSize: 12.5, wordBreak: "break-all" }}>{d.worktree.branch}</div>
          </>
        )}
        <h3>Actions</h3>
        <Actions
          unit={placed}
          running={Boolean(running)}
          stage={next.data && !next.data.blocked ? next.data.stage ?? "" : ""}
          moves={d?.hold_moves ?? []}
          onDone={() => {
            detail.reload();
            next.reload();
          }}
        />
        <h3>Depends on</h3>
        <div className="col gap4">
          {d?.depends_on.length ? (
            d.depends_on.map((dep) => (
              <div key={dep.ref} className="row" style={{ fontSize: 12.5 }}>
                <Icon name={dep.merged ? "check" : "clock"} size={13} />
                <span className="ellipsis">{dep.ref}</span>
              </div>
            ))
          ) : (
            <span className="faint" style={{ fontSize: 12.5 }}>Nothing</span>
          )}
        </div>
      </aside>
    </div>
  );
}

function Prop({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="prop">
      <span className="k">{k}</span>
      <span className="v">{children}</span>
    </div>
  );
}

/** The seven stages under their four phases; each says done, now, waiting or not yet. */
export function Track({ stages, now, done, waiting }: { stages: StageView[]; now: string; done: boolean; waiting: boolean }) {
  const status = Object.fromEntries(stages.map((s) => [s.stage, s.status]));
  const at = done ? TRACK.length : TRACK.indexOf(now);
  let last = "";
  return (
    <div className="track">
      {TRACK.map((s, i) => {
        // Where the unit is now wins over a stage's status: a stage sent back makes later ones not done.
        const finished = done || (at >= 0 ? i < at : ["accepted", "done", "skipped"].includes(status[s] ?? ""));
        const current = !done && i === at;
        const group = GROUP[s] !== last ? GROUP[s] : "";
        last = GROUP[s];
        return (
          <div key={s} className={`st ${finished ? "done" : current ? (waiting ? "wait" : "now") : ""}`}>
            <div className="grp">{group}</div>
            <div className="bar" />
            <div className="lbl">
              {finished ? <Icon name="check" size={11} /> : current ? waiting ? <Icon name="clock" size={11} /> : <Dot tone="live" /> : null}
              {STAGE_LABEL[s]}
            </div>
          </div>
        );
      })}
    </div>
  );
}

type Item = { at: string; key: string; node: ReactNode };

function Timeline({ detail, names, live }: { detail: Detail; names: Record<string, string>; live?: string }) {
  const items: Item[] = [
    ...detail.runs.map((r, i) => ({ at: r.ended || r.started, key: `run-${i}`, node: <RunItem run={r} name={names[r.stage] ?? r.stage} live={!r.ended && r.stage === live} /> })),
    ...answeredGroups(detail.answers).map((g, i) => ({ at: g.date, key: `ans-${i}`, node: <AnswerItem group={g} /> })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  if (!items.length)
    return (
      <Empty icon="clock" title="No activity yet">
        Runs and answers on this unit will appear here.
      </Empty>
    );
  return (
    <div className="tl">
      {items.map((it) => (
        <Fragment key={it.key}>{it.node}</Fragment>
      ))}
    </div>
  );
}

function RunItem({ run, name, live }: { run: UnitRun; name: string; live: boolean }) {
  const stopped = run.ended && run.outcome !== "done";
  const verb = live ? "is working on" : stopped ? run.outcome : "finished";
  return (
    <div className="tl-i">
      <span className="tl-ic">
        <AgentAvatar stage={run.stage} />
      </span>
      <div className="tl-c">
        <div className="tl-h">
          <b>{name}</b>
          <span>
            {stopped ? <Chip square tone="red">{verb}</Chip> : verb} {(STAGE_LABEL[run.stage] ?? run.stage).toLowerCase()}
          </span>
          {live && <Dot tone="live" />}
          {run.cost_usd != null && <span className="faint">· {money(run.cost_usd)}</span>}
          <span className="tl-time" title={run.ended || run.started}>{ago(run.ended || run.started)}</span>
        </div>
        <div className="faint" style={{ fontSize: 12 }}>
          {[run.turns != null ? `${run.turns} turns` : "", run.model ? modelName(run.model) : "", run.artifact].filter(Boolean).join(" · ")}
        </div>
        {stopped && run.detail && <div className="muted" style={{ marginTop: 2 }}>{run.detail}</div>}
      </div>
    </div>
  );
}

type AnswerGroup = { by: string; authority: string; artifact: string; date: string; answers: Answer[] };

/** Answers given together (same artifact, same person, same day) read as one item. */
function answeredGroups(answers: Answer[]): AnswerGroup[] {
  const groups: AnswerGroup[] = [];
  for (const a of answers) {
    const g = groups.find((x) => x.by === a.by && x.artifact === a.artifact && x.date === a.date);
    if (g) g.answers.push(a);
    else groups.push({ by: a.by, authority: a.authority, artifact: a.artifact, date: a.date, answers: [a] });
  }
  return groups;
}

function AnswerItem({ group }: { group: AnswerGroup }) {
  const leif = /^leif\b/i.test(group.by);
  const who = leif ? "Leif" : /owner|originator/i.test(group.by) || !group.by ? "You" : group.by;
  return (
    <div className="tl-i">
      {leif ? (
        <span className="tl-ic" style={{ background: "var(--accent)", borderColor: "transparent", color: "#fff" }}>
          <LeifMark size={10} />
        </span>
      ) : (
        <span className="tl-ic">
          <Icon name="chat" size={12} />
        </span>
      )}
      <div className="tl-c">
        <div className="tl-h">
          <b>{who}</b>
          <span>
            answered {group.answers.length} question{group.answers.length > 1 ? "s" : ""} on {group.artifact}
          </span>
          <span className="tl-time">{group.date}</span>
        </div>
        <div style={{ marginTop: 4, padding: "8px 12px", background: leif ? "var(--accent-soft)" : "var(--bg-sunk)", borderRadius: "var(--r2)" }}>
          {group.answers.map((a) => (
            <div key={a.n} style={{ marginBottom: 4 }}>
              <b>{a.n}.</b> {a.text}
              {group.authority && group.authority !== "person" && <span className="faint"> · {group.authority}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * What a person may do to the unit now. A paid or lasting action asks once more before it acts:
 * a step spends quota, a drop closes the pull request.
 */
function Actions({ unit, running, stage, moves, onDone }: { unit: PlacedUnit; running: boolean; stage: string; moves: string[]; onDone: () => void }) {
  const [asking, setAsking] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const at = { cwd: unit.workspace.path, unit: unit.name };

  const act = async (what: string, run: () => Promise<unknown>) => {
    if (asking !== what) return setAsking(what);
    setBusy(true);
    setError(null);
    try {
      await run();
      setAsking("");
      setReason("");
      onDone();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const hold = (to: string) => api.post("/api/units/hold", { ...at, to, reason: reason.trim() });

  return (
    <div className="col gap6">
      {running ? (
        <Button icon="x" disabled={busy} onClick={() => act("stop", () => api.post("/api/board/stop", { ...at, by: "owner" }))}>
          {asking === "stop" ? "Stop it? Nothing is pushed" : "Stop the run"}
        </Button>
      ) : stage ? (
        <Button kind="primary" icon="arrow" disabled={busy} title="Runs a real Claude session and spends account quota." onClick={() => act("run", () => api.start("/api/board/run", { ...at, stage }))}>
          {asking === "run" ? `Spend quota on ${STAGE_LABEL[stage] ?? stage}?` : `Run ${STAGE_LABEL[stage] ?? stage}`}
        </Button>
      ) : null}
      {moves.includes("active") && (
        <Button icon="refresh" disabled={busy} onClick={() => act("active", () => hold("active"))}>
          {asking === "active" ? "Resume it? Nothing starts by itself" : "Resume"}
        </Button>
      )}
      {moves.includes("paused") && (
        <Button icon="pause" disabled={busy} onClick={() => act("paused", () => hold("paused"))}>
          {asking === "paused" ? "Pause it?" : "Pause"}
        </Button>
      )}
      {moves.includes("dropped") &&
        (asking === "dropped" ? (
          <>
            <textarea className="ta" rows={2} style={{ fontSize: 12.5 }} placeholder="Why drop it? (kept with the unit)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button kind="danger" disabled={busy || !reason.trim()} onClick={() => act("dropped", () => hold("dropped"))}>
              Drop, and close its pull request
            </Button>
          </>
        ) : (
          <Button kind="ghost" icon="x" disabled={busy} onClick={() => setAsking("dropped")}>
            Drop…
          </Button>
        ))}
      {asking && !busy && (
        <button className="btn ghost sm" onClick={() => setAsking("")}>
          Cancel
        </button>
      )}
      {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error.message}</div>}
    </div>
  );
}
