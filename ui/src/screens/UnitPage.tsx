// One unit: what it is, where it stands, what happened on it and what happens next. The side
// panel holds the facts; the timeline holds every run and answer, newest first.

import { Fragment, useState, type ReactNode } from "react";
import type { Answer, Decision, Detail, OutputRecord, Paused, StageView, UnitRun } from "../api.gen";
import { api, useResource } from "../lib/api";
import type { PlacedUnit } from "../lib/boards";
import { allUnits, findUnit, useBoards } from "../lib/boards";
import { FeatureSlots } from "../lib/feature";
import { STAGE_LABEL, ago, modelName, money, pausedAt, unitCode, unitTitle } from "../lib/format";
import { AgentAvatar, Icon, LeifMark } from "../lib/icons";
import { unitState } from "../lib/model";
import { Link } from "../lib/router";
import { Button, Chip, Dot, Empty, ErrorState, Meter, SkeletonRows } from "../components/ui";
import { RunLog } from "./RunLog";

const TRACK = ["intent", "spec", "plan", "impl", "pr", "review", "ship"];
const GROUP: Record<string, string> = { intent: "Shape", spec: "Shape", plan: "Shape", impl: "Build", pr: "Check", review: "Check", ship: "Ship" };
// The unit's own target, from the owner's standing goal: a merged unit costs $15 at most.
const TARGET_USD = 15;

export function UnitPage({ workspace, number }: { workspace: string; number: string }) {
  const { boards, loading } = useBoards();
  // `#outputs` in the address opens the second tab, so a link can point at what an agent handed back.
  const [tab, setTab] = useState<"activity" | "outputs">(location.hash === "#outputs" ? "outputs" : "activity");
  const placed = findUnit(allUnits(boards), workspace, number);
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
          <span className="faint">{unitCode(workspace, placed.number)}</span>
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
        {placed.paused && <PausedBanner unit={placed} paused={placed.paused} onDone={() => detail.reload()} />}
        {open.length > 0 && (
          <div className="callout amber" style={{ marginTop: 18 }}>
            <Icon name="chat" size={15} />
            <div className="grow">
              <b>
                {open.length} question{open.length > 1 ? "s" : ""} wait{open.length > 1 ? "" : "s"} on you
              </b>
              <div className="muted">{open[0].text.replace(/\*\*/g, "").slice(0, 140)}</div>
            </div>
            <Link to={`/inbox/${workspace}/${placed.number}`} className="btn primary sm">
              Answer
            </Link>
          </div>
        )}
        {next.data && !shipped && !open.length && !placed.paused && (
          <div className="callout" style={{ marginTop: 18 }}>
            <Icon name="arrow" size={15} />
            <div className="grow">
              <b>Next: {STAGE_LABEL[next.data.stage ?? ""] ?? "nothing to run"}</b>
              <div className="muted">{next.data.action}</div>
            </div>
          </div>
        )}
        <FeatureSlots at="unit" workspace={placed.workspace} unit={placed.name} />
        <div className="tabs" style={{ marginTop: 22 }}>
          <a className={tab === "activity" ? "on" : ""} onClick={() => setTab("activity")}>Activity</a>
          <a className={tab === "outputs" ? "on" : ""} onClick={() => setTab("outputs")}>Outputs</a>
        </div>
        {detail.state === "error" ? (
          <ErrorState error={detail.error} onRetry={detail.reload} />
        ) : !d ? (
          <SkeletonRows rows={4} />
        ) : tab === "outputs" ? (
          <Outputs outputs={d.outputs} names={names} />
        ) : (
          <Timeline detail={d} names={names} live={running?.stage} cwd={placed.workspace.path} />
        )}
      </div>
      <aside className="props">
        <Prop k="Status">{running ? "Running" : state.label}</Prop>
        <Prop k="Project">{workspace}</Prop>
        <Prop k="Type">{placed.type || "—"}</Prop>
        <Prop k="Idea">{placed.idea ? placed.idea.replace(/^.*\/ideas\//, "").replace(/\.md$/, "") : "—"}</Prop>
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
          <div className="faint" style={{ fontSize: 12.5 }}>{state.group === "Shipped" ? "No pull request on record." : "Opens after build."}</div>
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
          stage={next.data && !next.data.blocked && !placed.paused ? next.data.stage ?? "" : ""}
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

/** What a run held at its ceiling needs: a higher ceiling to go on in the same session, or a rerun from scratch. */
function PausedBanner({ unit, paused, onDone }: { unit: PlacedUnit; paused: Paused; onDone: () => void }) {
  const turns = paused.ceiling === "turns";
  const [value, setValue] = useState(String(turns ? (paused.max_turns ?? 0) * 2 : Math.round((paused.max_usd ?? 0) * 2 * 100) / 100));
  const [asking, setAsking] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const at = { cwd: unit.workspace.path, unit: unit.name, stage: paused.stage };
  const stage = STAGE_LABEL[paused.stage] ?? paused.stage;
  const act = async (what: string, run: () => Promise<unknown>) => {
    if (asking !== what) return setAsking(what);
    setBusy(true);
    setError(null);
    try {
      await run();
      setAsking("");
      onDone();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="callout amber" style={{ marginTop: 18, flexWrap: "wrap" }}>
      <Icon name="pause" size={15} />
      <div className="grow" style={{ minWidth: 260 }}>
        <b>
          {stage} hit its {turns ? `${paused.max_turns}-turn` : money(paused.max_usd)} ceiling
        </b>
        <div className="muted">
          Spent {money(paused.usd)} of {money(paused.max_usd)} and {paused.turns ?? "?"} of {paused.max_turns ?? "?"} turns. It kept its session and its work: raise the ceiling and it goes on where it stopped, or rerun the stage from scratch.
        </div>
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <label className="faint" style={{ fontSize: 12.5 }}>
            New {turns ? "turn" : "$"} ceiling
          </label>
          <input className="input sm" type="number" min={0} step={turns ? 1 : 0.5} value={value} aria-label={`New ${turns ? "turn" : "dollar"} ceiling`} onChange={(e) => setValue(e.target.value)} />
          <Button
            kind="primary"
            size="sm"
            icon="arrow"
            disabled={busy || !(Number(value) > ((turns ? paused.max_turns : paused.max_usd) ?? 0))}
            title="Goes on in the same session and spends quota."
            onClick={() => act("raise", () => api.start("/api/board/run", { ...at, raise: turns ? { turns: Number(value) } : { usd: Number(value) } }))}
          >
            {asking === "raise" ? "Spend quota and go on?" : "Raise and continue"}
          </Button>
          <Button size="sm" icon="refresh" disabled={busy} onClick={() => (asking === "rerun" ? act("rerun", () => api.start("/api/board/run", { ...at, rerun: true, note: note.trim() })) : setAsking("rerun"))}>
            {asking === "rerun" ? "Spend quota on a fresh start?" : "Rerun from scratch"}
          </Button>
          {asking === "drop" ? (
            <Button size="sm" kind="danger" disabled={busy || !reason.trim()} onClick={() => act("drop", () => api.post("/api/units/hold", { cwd: at.cwd, unit: at.unit, to: "dropped", reason: reason.trim() }))}>
              Drop, and close its pull request
            </Button>
          ) : (
            <Button size="sm" kind="ghost" icon="x" disabled={busy} onClick={() => setAsking("drop")}>
              Drop…
            </Button>
          )}
          {asking && !busy && (
            <Button size="sm" kind="ghost" onClick={() => setAsking("")}>
              Cancel
            </Button>
          )}
        </div>
        {asking === "rerun" && <input className="input" style={{ marginTop: 8 }} placeholder="A note for the new run (optional)" value={note} onChange={(e) => setNote(e.target.value)} />}
        {asking === "drop" && <textarea className="ta" rows={2} style={{ fontSize: 12.5, marginTop: 8 }} placeholder="Why drop it? (kept with the unit)" value={reason} onChange={(e) => setReason(e.target.value)} />}
        {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
      </div>
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
        const finished = done || (at >= 0 ? i < at : ["accepted", "skipped"].includes(status[s] ?? ""));
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

/** What each agent last handed back: one block per agent, its contract version, then every field. */
function Outputs({ outputs, names }: { outputs: OutputRecord[]; names: Record<string, string> }) {
  if (!outputs.length)
    return (
      <Empty icon="clock" title="No outputs yet">
        No agent has handed back an output yet.
      </Empty>
    );
  return (
    <div id="outputs" className="col gap6">
      {outputs.map((o) => (
        <div key={o.agent} style={{ padding: "10px 12px", background: "var(--bg-sunk)", borderRadius: "var(--r2)" }}>
          <div className="row" style={{ gap: 6 }}>
            <AgentAvatar stage={o.agent} />
            <b>{names[o.agent] ?? o.agent}</b>
            <Chip square tone="plain">v{o.version}</Chip>
            <span className="faint" title={o.at}>{ago(o.at)}</span>
          </div>
          {Object.entries(o.fields).map(([k, v]) => (
            <div key={k} style={{ marginTop: 6 }}>
              <span className="faint">{k}</span>
              <FieldValue value={v} />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** A list is one line per item; an object is each field under its name, a list field (a plan
 * step's paths) as a short list; anything else is text. */
function FieldValue({ value }: { value: unknown }) {
  if (Array.isArray(value))
    return value.length ? (
      <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
        {value.map((item, i) => (
          <li key={i}>
            <FieldValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <div className="faint">none</div>
    );
  if (value && typeof value === "object")
    return (
      <div>
        {Object.entries(value).map(([k, v]) => (
          <div key={k}>
            <span className="faint">{k}: </span>
            {v && typeof v === "object" ? <FieldValue value={v} /> : String(v)}
          </div>
        ))}
      </div>
    );
  return <div>{String(value)}</div>;
}

type Item = { at: string; key: string; node: ReactNode };

function Timeline({ detail, names, live, cwd }: { detail: Detail; names: Record<string, string>; live?: string; cwd: string }) {
  const items: Item[] = [
    ...detail.runs.map((r, i) => ({ at: r.ended || r.started, key: `run-${i}`, node: <RunItem run={r} name={names[r.stage] ?? r.stage} live={!r.ended && r.stage === live} cwd={cwd} /> })),
    ...answeredGroups(detail.answers).map((g, i) => ({ at: g.date, key: `ans-${i}`, node: <AnswerItem group={g} /> })),
    ...detail.decisions.map((d, i) => ({ at: d.date, key: `dec-${i}`, node: <DecisionItem decision={d} /> })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  if (!items.length)
    return (
      <Empty icon="clock" title="No activity yet">
        Runs, answers and your decisions on this unit will appear here.
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

function RunItem({ run, name, live, cwd }: { run: UnitRun; name: string; live: boolean; cwd: string }) {
  const [shown, setShown] = useState(live);
  const [part, setPart] = useState("");
  const paused = run.outcome === "paused-budget";
  const stopped = run.ended && run.outcome !== "done";
  const verb = live ? "is working on" : paused ? "paused" : stopped ? run.outcome : "finished";
  return (
    <div className="tl-i">
      <span className="tl-ic">
        <AgentAvatar stage={run.stage} />
      </span>
      <div className="tl-c">
        <div className="tl-h">
          <b>{name}</b>
          <span>
            {stopped ? <Chip square tone={paused ? "amber" : "red"}>{verb}</Chip> : verb} {(STAGE_LABEL[run.stage] ?? run.stage).toLowerCase()}
          </span>
          {live && <Dot tone="live" />}
          {run.cost_usd != null && <span className="faint">· {money(run.cost_usd)}</span>}
          <span className="tl-time" title={run.ended || run.started}>{ago(run.ended || run.started)}</span>
        </div>
        <div className="faint" style={{ fontSize: 12 }}>
          {[run.turns != null ? `${run.turns} turns` : "", run.model ? modelName(run.model) : "", run.artifact].filter(Boolean).join(" · ")}
        </div>
        {paused && run.paused && <div className="muted" style={{ marginTop: 2 }}>{pausedAt(run.paused)}; a raised ceiling goes on in this session.</div>}
        {stopped && !paused && run.detail && <div className="muted" style={{ marginTop: 2 }}>{run.detail}</div>}
        {run.parts.length > 0 && (
          <div className="faint" style={{ fontSize: 12, marginTop: 2 }} title="One session: a ceiling paused it and you raised it">
            One session in {run.parts.length + 1} parts{run.raised_by ? `, raised by ${run.raised_by}` : ""}
          </div>
        )}
        {run.parts.map((p, i) => (
          <div key={p.run || i} style={{ marginTop: 4 }}>
            <button className="link-btn faint" style={{ fontSize: 12 }} onClick={() => setPart(part === p.run ? "" : p.run)}>
              Part {i + 1}: {p.paused ? pausedAt(p.paused) : "ended"} · {ago(p.ended)}
              {part === p.run ? " (hide)" : ""}
            </button>
            {part === p.run && p.run && <RunLog cwd={cwd} run={p.run} live={false} />}
          </div>
        ))}
        {run.envelope.length > 0 && (
          <div className="faint" style={{ fontSize: 12 }} title="What its prompt held, as its row declares">
            Given: {run.envelope.join(" · ")}
          </div>
        )}
        {run.run && (
          <button className="link-btn faint" style={{ fontSize: 12, marginTop: 4 }} onClick={() => setShown(!shown)}>
            {shown ? "Hide what it did" : run.parts.length ? `What part ${run.parts.length + 1} did` : "What it did"}
          </button>
        )}
        {run.run && shown && <RunLog cwd={cwd} run={run.run} live={live} />}
      </div>
    </div>
  );
}

type AnswerGroup = { by: Answer["by"]; name: string; artifact: string; date: string; answers: Answer[] };

/** Answers given together (same artifact, same `by` and name, same day) read as one item. */
function answeredGroups(answers: Answer[]): AnswerGroup[] {
  const groups: AnswerGroup[] = [];
  for (const a of answers) {
    const g = groups.find((x) => x.by === a.by && x.name === a.name && x.artifact === a.artifact && x.date === a.date);
    if (g) g.answers.push(a);
    else groups.push({ by: a.by, name: a.name, artifact: a.artifact, date: a.date, answers: [a] });
  }
  return groups;
}

const DECISION_ICON = { rerun: "refresh", "more-rounds": "plus", outcome: "check" } as const;

/** A person's rerun, extra review round or outcome: their own decision, beside the runs. */
function DecisionItem({ decision }: { decision: Decision }) {
  return (
    <div className="tl-i">
      <span className="tl-ic">
        <Icon name={DECISION_ICON[decision.kind]} size={12} />
      </span>
      <div className="tl-c">
        <div className="tl-h">
          <b title={decision.by}>You</b>
          <span>{decision.text}</span>
          <span className="tl-time">{decision.date}</span>
        </div>
      </div>
    </div>
  );
}

function AnswerItem({ group }: { group: AnswerGroup }) {
  const leif = group.by === "delegated";
  const who = leif ? "Leif" : "You";
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
          <b title={group.name}>{who}</b>
          <span>
            answered {group.answers.length} question{group.answers.length > 1 ? "s" : ""} on {group.artifact}
          </span>
          <span className="tl-time">{group.date}</span>
        </div>
        <div style={{ marginTop: 4, padding: "8px 12px", background: leif ? "var(--accent-soft)" : "var(--bg-sunk)", borderRadius: "var(--r2)" }}>
          {group.answers.map((a) => (
            <div key={a.n} style={{ marginBottom: 4 }}>
              <b>{a.n}.</b> {a.text}
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
