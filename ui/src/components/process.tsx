// One process drawn as a diagram: its states in walk order down the main line, each with its agent
// (glyph and name) or the engine's action, and every other way on as a labelled arrow. Plain SVG.

import { useEffect, useId, useState } from "react";
import type { Condition, PackShown, ProcessShown, State } from "../api.gen";
import { api, ApiError } from "../lib/api";
import {
  ACTION_WORDS,
  GUARDS,
  GUARD_WORDS,
  addStep,
  artifactNames,
  missingInput,
  renameStep,
  fieldOptions,
  fromProcess,
  moveStep,
  nameProblem,
  reasonsByStep,
  removeStep,
  setAgent,
  setStep,
  stateAgents,
  toProcess,
  type BuildAgent,
  type Draft,
  type Step,
  type Way,
} from "../lib/build";
import { Rune } from "../lib/icons";
import { agentFace, stageLabel } from "../lib/pack";
import { Button } from "./ui";

const NODE_W = 176;
const NODE_H = 42;
const GAP = 30;
const ROW = NODE_H + GAP;
const LANE0 = NODE_W + 22;
const LANE_STEP = 30;
const SIDE_W = 124;

const ACTIONS: Record<string, string> = { "open-pr": "Opens the pull request", merge: "Merges it" };

/** A `when` in words: `ready`, `unmeasured is non-empty`, `ship-ready`; a list reads "and". */
export function whenWords(when: State["when"]): string {
  const list: Condition[] = when === undefined ? [] : Array.isArray(when) ? when : [when];
  return list
    .map((c) => (c.guard ? c.guard.replace(/-/g, " ") : c.is === "non-empty" || c.is === "empty" ? `${c.field} is ${c.is}` : (c.is ?? "")))
    .join(" and ");
}

type Edge = { from: string; to: string; label: string };

function walk(p: ProcessShown): { main: string[]; side: string[]; edges: Edge[] } {
  const main: string[] = [];
  for (let at = p.start; at && !main.includes(at); ) {
    main.push(at);
    const ways = p.states[at]?.next ?? [];
    at = ways.length ? ways[ways.length - 1].to : "";
  }
  const edges = Object.entries(p.states).flatMap(([from, st]) =>
    (st.next ?? []).map((w, i, all) => ({ from, to: w.to, label: whenWords(w.when) || (i < all.length - 1 || !main.includes(w.to) ? "otherwise" : "") })),
  );
  return { main, side: Object.keys(p.states).filter((k) => !main.includes(k)), edges };
}

export function ProcessDiagram({ process, current, done = [] }: { process: ProcessShown; current?: string; done?: string[] }) {
  const id = useId();
  const { main, side, edges } = walk(process);
  const y = (k: string) => main.indexOf(k) * ROW;
  const mid = (k: string) => y(k) + NODE_H / 2;

  // A side state sits beside the main states that lead to it or come back from it.
  const sideY: Record<string, number> = {};
  for (const s of side) {
    const near = edges.filter((e) => e.to === s || e.from === s).map((e) => (e.to === s ? e.from : e.to)).filter((k) => main.includes(k));
    sideY[s] = near.length ? near.reduce((n, k) => n + mid(k), 0) / near.length - NODE_H / 2 : 0;
  }
  // Jumps along the main line: forward ones bulge to the right, backward ones to the left, where
  // their words have room; the longest is outermost.
  const span = (e: Edge) => Math.abs(main.indexOf(e.to) - main.indexOf(e.from));
  const jumps = edges.filter((e) => main.includes(e.from) && main.includes(e.to) && main.indexOf(e.to) !== main.indexOf(e.from) + 1);
  const fwd = jumps.filter((e) => main.indexOf(e.to) > main.indexOf(e.from)).sort((a, b) => span(b) - span(a));
  const back = jumps.filter((e) => main.indexOf(e.to) < main.indexOf(e.from)).sort((a, b) => span(b) - span(a));
  const left = back.length ? 118 + back.length * LANE_STEP : 0;
  const sideX = LANE0 + Math.max(1, fwd.length) * LANE_STEP + 140;
  const width = left + (side.length ? sideX + SIDE_W + 8 : LANE0 + Math.max(1, fwd.length) * LANE_STEP + 70);
  const height = main.length * ROW - GAP + 4;

  const node = (k: string, x: number, top: number, w: number) => {
    const st = process.states[k];
    const cls = ["pd-node", k === current ? "now" : "", done.includes(k) ? "done" : "", st.optional ? "opt" : ""].join(" ");
    return (
      <g key={k} className={cls} transform={`translate(${x} ${top})`}>
        <rect width={w} height={NODE_H} rx={8} />
        {st.agent ? (
          <>
            <foreignObject x={10} y={9} width={16} height={16}><Rune glyph={agentFace(st.agent).glyph} size={14} /></foreignObject>
            <text x={32} y={17} className="pd-name">{agentFace(st.agent).name}</text>
          </>
        ) : (
          <text x={12} y={17} className="pd-name">{ACTIONS[st.action ?? ""] ?? st.action}</text>
        )}
        <text x={st.agent ? 32 : 12} y={32} className="pd-sub">{[stageLabel(k), st.agent ? "" : "the app", st.optional ? "optional" : ""].filter(Boolean).join(" · ")}</text>
        {done.includes(k) && <path d="m-14 0 3 3 6-6" transform={`translate(${w - 10} ${NODE_H / 2})`} className="pd-check" />}
      </g>
    );
  };

  const label = (x: number, yy: number, text: string, anchor: "start" | "middle" = "start") =>
    text ? (
      <text x={x} y={yy} textAnchor={anchor} className="pd-edge">
        {text}
      </text>
    ) : null;

  return (
    <div className="pd-wrap">
      <svg className="pd" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={`The ${process.name} process`}>
        <defs>
          <marker id={`${id}a`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M1 1 7 4 1 7" className="pd-head" />
          </marker>
        </defs>
        <g transform={`translate(${left} 0)`}>
        {main.slice(1).map((k, i) => {
          const way = edges.find((e) => e.from === main[i] && e.to === k);
          return (
            <g key={`m${k}`}>
              <path d={`M${NODE_W / 2} ${y(main[i]) + NODE_H} V${y(k) - 1}`} className="pd-line" markerEnd={`url(#${id}a)`} />
              {label(NODE_W / 2 + 8, y(k) - GAP / 2 + 4, way?.label ?? "")}
            </g>
          );
        })}
        {fwd.map((e, i) => {
          const lx = LANE0 + i * LANE_STEP;
          const ya = mid(e.from);
          const yb = mid(e.to);
          return (
            <g key={`f${e.from}${e.to}${i}`}>
              <path d={`M${NODE_W} ${ya} H${lx} V${yb} H${NODE_W + 2}`} className="pd-line side" markerEnd={`url(#${id}a)`} />
              {label(lx + 7, ya + 18, e.label)}
            </g>
          );
        })}
        {back.map((e, i) => {
          const lx = -18 - i * LANE_STEP;
          const ya = mid(e.from);
          const yb = mid(e.to);
          return (
            <g key={`b${e.from}${e.to}${i}`}>
              <path d={`M0 ${ya} H${lx} V${yb} H-2`} className="pd-line side" markerEnd={`url(#${id}a)`} />
              <text x={lx - 7} y={(ya + yb) / 2 + 4} textAnchor="end" className="pd-edge">{e.label}</text>
            </g>
          );
        })}
        {edges
          .filter((e) => side.includes(e.from) !== side.includes(e.to))
          .map((e, i) => {
            const out = side.includes(e.to);
            const m = out ? e.from : e.to;
            const s = out ? e.to : e.from;
            const ym = mid(m) + (out ? -5 : 5);
            const ys = sideY[s] + NODE_H / 2 + (out ? -5 : 5);
            const d = out ? `M${NODE_W} ${ym} L${sideX - 1} ${ys}` : `M${sideX} ${ys} L${NODE_W + 2} ${ym}`;
            return (
              <g key={`s${e.from}${e.to}${i}`}>
                <path d={d} className="pd-line side" markerEnd={`url(#${id}a)`} />
                {label(sideX - 96, (ym + ys) / 2 + (out ? -7 : 14), e.label, "middle")}
              </g>
            );
          })}
        {main.map((k) => node(k, 0, y(k), NODE_W))}
        {side.map((k) => node(k, sideX, sideY[k], SIDE_W))}
        </g>
      </svg>
    </div>
  );
}

type Kind = "always" | "field" | "guard";
const kindOf = (w: Way): Kind => (!w.cond ? "always" : w.cond.guard ? "guard" : "field");
const what = (s: Step) => (s.agent ? `agent:${s.agent}` : `action:${s.action}`);

/**
 * Build or change a process of your own beside its diagram: steps in order, each run by an agent
 * or by the app, and the ways on from a step. Saving sends it to the app, whose check's reasons
 * show next to the step they name; the diagram redraws with every edit.
 */
export function ProcessEditor({
  rows,
  cwd,
  taken,
  initial,
  onClose,
  onSaved,
}: {
  rows: BuildAgent[];
  cwd: string;
  taken: string[];
  initial?: { name: string; process: Pick<ProcessShown, "start" | "states"> };
  onClose: () => void;
  onSaved: (packs: PackShown[]) => void;
}) {
  const editing = Boolean(initial);
  const [draft, setDraft] = useState<Draft>(() => (initial ? fromProcess(initial.name, initial.process) : { name: "", steps: [] }));
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState<string[]>([]);
  const [asking, setAsking] = useState(false);
  const keys = draft.steps.map((s) => s.key);
  const { byStep, rest } = reasonsByStep(reasons, keys);
  const problem = editing ? null : nameProblem(draft.name, taken);
  const agents = stateAgents(rows);
  const edit = (f: (d: Draft) => Draft) => (setReasons([]), setDraft(f));

  const send = async (process: unknown) => {
    setBusy(true);
    setReasons([]);
    try {
      onSaved(await api.post<PackShown[]>("/api/packs/process", { cwd, name: draft.name, process }));
    } catch (e) {
      setAsking(false);
      setReasons(e instanceof ApiError && e.reasons.length ? e.reasons : [(e as Error).message]);
    } finally {
      setBusy(false);
    }
  };
  const process = toProcess(draft);
  const drawn: ProcessShown = { ...process, own: true, ref: `local/${draft.name}`, name: draft.name || "new process" };
  const why = !draft.steps.length ? "Add a step first." : problem;

  return (
    <div className="pe">
      <div className="pe-form">
        <label className="f">
          <span className="lab">Process name</span>
          <input className="input mono" value={draft.name} disabled={editing} placeholder="tiny" onChange={(e) => edit((d) => ({ ...d, name: e.target.value }))} />
          {draft.name && problem && <span className="field-err">{problem}</span>}
        </label>

        <div className="lab" style={{ marginTop: 14 }}>Steps, in the order a unit walks them</div>
        {!draft.steps.length && <div className="faint" style={{ margin: "6px 0 10px", fontSize: 13 }}>Nothing yet. Add the first step below.</div>}
        <datalist id="pe-names">{artifactNames(rows).map((n) => <option key={n} value={n} />)}</datalist>
        <ol className="pe-steps">
          {draft.steps.map((s, i) => (
            <li key={s.key} className={`pe-step ${byStep[s.key] ? "bad" : ""}`}>
              <div className="pe-head">
                <span className="pe-n">{i + 1}</span>
                <select
                  className="input"
                  aria-label={`Step ${i + 1} runs`}
                  value={what(s)}
                  onChange={(e) => {
                    const [kind, v] = e.target.value.split(":");
                    edit((d) => (kind === "agent" ? (s.agent ? setAgent(d, s.key, v) : setStep(d, s.key, { agent: v, action: "", ways: s.ways.map((w) => ({ ...w, cond: null, rest: [] })) })) : setStep(d, s.key, { agent: "", action: v, ways: s.ways.map((w) => ({ ...w, cond: null, rest: [] })) })));
                  }}
                >
                  <StepOptions agents={agents} />
                </select>
                <StepName value={s.key} onCommit={(to) => edit((d) => renameStep(d, s.key, to))} />
                <button className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => edit((d) => moveStep(d, s.key, -1))}>↑</button>
                <button className="icon-btn" aria-label="Move down" disabled={i === draft.steps.length - 1} onClick={() => edit((d) => moveStep(d, s.key, 1))}>↓</button>
                <button className="icon-btn" aria-label={`Remove step ${i + 1}`} onClick={() => edit((d) => removeStep(d, s.key))}>✕</button>
              </div>
              {s.action && <div className="faint pe-sub">The app {ACTION_WORDS[s.action]}.</div>}
              {byStep[s.key]?.map((r) => (
                <div key={r} className="field-err">
                  {r}
                  {missingInput(r) && <> Name the step that makes it {missingInput(r)}, in its name box.</>}
                </div>
              ))}
              {s.ways.map((w, wi) => (
                <WayRow key={wi} way={w} step={s} keys={keys} rows={rows} onChange={(nw) => edit((d) => setStep(d, s.key, { ways: s.ways.map((x, n) => (n === wi ? nw : x)) }))} onRemove={() => edit((d) => setStep(d, s.key, { ways: s.ways.filter((_, n) => n !== wi) }))} />
              ))}
              <div className="pe-foot">
                {i < draft.steps.length - 1 && (
                  <label className="pe-then">
                    <input type="checkbox" checked={s.then} onChange={(e) => edit((d) => setStep(d, s.key, { then: e.target.checked }))} /> otherwise go on to {stageLabel(draft.steps[i + 1].key)}
                  </label>
                )}
                {draft.steps.length > 1 && (
                  <button className="linkish" onClick={() => edit((d) => setStep(d, s.key, { ways: [...s.ways, { to: keys.find((k) => k !== s.key) ?? s.key, cond: null, rest: [] }] }))}>
                    + Add a way on
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
        <select
          className="input"
          aria-label="Add a step"
          value=""
          onChange={(e) => {
            const [kind, v] = e.target.value.split(":");
            if (v) edit((d) => addStep(d, kind === "agent" ? { agent: v } : { action: v }));
          }}
        >
          <option value="">+ Add a step…</option>
          <StepOptions agents={agents} />
        </select>

        {rest.length > 0 && (
          <div className="pe-refused" role="alert">
            <b>Not saved</b>
            <ul>{rest.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
        )}
        {Object.keys(byStep).length > 0 && <div className="field-err" style={{ marginTop: 8 }}>Not saved: fix what is marked on the steps.</div>}
        <div className="pe-actions">
          {editing &&
            (asking ? (
              <>
                <span className="faint" style={{ fontSize: 12.5 }}>Delete {draft.name} for good?</span>
                <Button size="sm" kind="danger" disabled={busy} onClick={() => send(null)}>Yes, delete</Button>
                <Button size="sm" kind="ghost" onClick={() => setAsking(false)}>Keep it</Button>
              </>
            ) : (
              <Button size="sm" kind="ghost" onClick={() => setAsking(true)}>Delete process</Button>
            ))}
          <span className="grow" />
          {why && <span className="faint" style={{ fontSize: 12.5 }}>{why}</span>}
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" disabled={busy || Boolean(why)} onClick={() => send(process)}>{busy ? "Saving…" : "Save process"}</Button>
        </div>
      </div>
      <div className="pe-draw">
        <div className="lab" style={{ marginBottom: 8 }}>How a unit walks it</div>
        {draft.steps.length ? <ProcessDiagram process={drawn} /> : <div className="faint" style={{ fontSize: 13 }}>The diagram appears as you add steps.</div>}
      </div>
    </div>
  );
}

/** A step's name: what other agents' inputs call its result (`impl`). Kept when it is a fresh, valid name. */
function StepName({ value, onCommit }: { value: string; onCommit: (to: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <>
      <input className="input mono pe-key" aria-label="Step name" list="pe-names" value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={() => (onCommit(draft), setDraft(value))} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
    </>
  );
}

function StepOptions({ agents }: { agents: BuildAgent[] }) {
  return (
    <>
      <optgroup label="An agent runs it">
        {agents.map((a) => (
          <option key={a.key} value={`agent:${a.key}`}>{a.row.name && a.row.name.toLowerCase() !== a.key ? `${a.row.name} (${a.key})` : a.key}</option>
        ))}
      </optgroup>
      <optgroup label="The app does it">
        <option value="action:open-pr">Opens the PR</option>
        <option value="action:merge">Merges it</option>
      </optgroup>
    </>
  );
}

function WayRow({ way, step, keys, rows, onChange, onRemove }: { way: Way; step: Step; keys: string[]; rows: BuildAgent[]; onChange: (w: Way) => void; onRemove: () => void }) {
  const options = fieldOptions(rows.find((r) => r.key === step.agent));
  const kind = kindOf(way);
  const field = options.find((o) => o.field === way.cond?.field);
  const kinds = (["always", "field", "guard"] as Kind[]).filter((k) => k !== "field" || options.length);
  const setKind = (k: Kind) => onChange({ ...way, cond: k === "always" ? null : k === "guard" ? { guard: GUARDS[0] } : { field: options[0].field, is: options[0].values[0] }, rest: [] });
  return (
    <div className="pe-way">
      <span className="faint">go to</span>
      <select className="input" aria-label="Goes to" value={way.to} onChange={(e) => onChange({ ...way, to: e.target.value })}>
        {keys.map((k) => <option key={k} value={k}>{stageLabel(k)}</option>)}
      </select>
      <span className="faint">when</span>
      <select className="input" aria-label="When" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
        {kinds.map((k) => <option key={k} value={k}>{k === "always" ? "always" : k === "field" ? "its answer says" : "a check holds"}</option>)}
      </select>
      {kind === "field" && way.cond && (
        <>
          <select className="input" aria-label="Field" value={way.cond.field} onChange={(e) => { const o = options.find((x) => x.field === e.target.value); if (o) onChange({ ...way, cond: { field: o.field, is: o.values[0] } }); }}>
            {options.map((o) => <option key={o.field} value={o.field}>{o.field}</option>)}
          </select>
          <span className="faint">is</span>
          <select className="input" aria-label="Value" value={way.cond.is} onChange={(e) => onChange({ ...way, cond: { field: way.cond?.field, is: e.target.value } })}>
            {(field?.values ?? [way.cond.is ?? ""]).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </>
      )}
      {kind === "guard" && way.cond && (
        <select className="input" aria-label="Check" value={way.cond.guard} onChange={(e) => onChange({ ...way, cond: { guard: e.target.value } })}>
          {GUARDS.map((g) => <option key={g} value={g}>{GUARD_WORDS[g]}</option>)}
        </select>
      )}
      {way.rest.length > 0 && <span className="faint">and {way.rest.length} more</span>}
      <button className="icon-btn" aria-label="Remove this way" onClick={onRemove}>✕</button>
    </div>
  );
}
