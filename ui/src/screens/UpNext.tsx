// What the autopilot works on next, per project: the shortlist in its order, the other units in
// the order their estimates give (Leif's suggestion, with the why), and what has no estimate yet.
// Every change saves the whole shortlist again with a reason, so the run log keeps who and why.

import { useState, type ReactNode } from "react";
import type { EstimateBrief } from "../api.gen";
import { api, useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { FeatureSlots } from "../lib/feature";
import { unitCode, unitTitle } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Link } from "../lib/router";
import { Button, Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const number = (unit: string) => Number(unit.slice(0, 4));

/** `list` with `unit` moved by `by` places (−1 up, +1 down), kept inside the list. */
export function moved(list: string[], unit: string, by: number): string[] {
  const from = list.indexOf(unit);
  const to = Math.max(0, Math.min(list.length - 1, from + by));
  if (from < 0 || from === to) return list;
  const out = list.filter((u) => u !== unit);
  out.splice(to, 0, unit);
  return out;
}

export function UpNext() {
  const { boards, loading } = useBoards();
  const [project, setProject] = useState("");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  return (
    <div className="page mid">
      <PageHead title="Up next" lede="The autopilot works only the shortlist, top first. Leif orders the rest by value and effort; add what should go next." />
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
  const cwd = workspace.path;
  const view = useResource("/api/backlog", { cwd }, { on: ["shortlist.", "estimate.", "step.", "hold."] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const v = view.data;

  const save = async (path: string, body: object) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(path, { cwd, by: "owner", ...body });
      view.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const list = v?.shortlist.map((s) => s.unit) ?? [];
  const shortlist = (units: string[], reason: string) => save("/api/backlog/shortlist", { units, reason });

  if (view.state === "error") return <ErrorState error={view.error} onRetry={view.reload} />;
  if (!v) return <SkeletonRows rows={5} />;
  const full = list.length >= v.max;
  return (
    <>
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 12 }}>{error.message}</div>}
      <div className="sec-h">
        Shortlist <span className="faint">{list.length ? `${list.length} of ${v.max}` : ""}</span>
      </div>
      <div className="card">
        {v.shortlist.map((s, i) => (
          <Row key={s.unit} workspace={workspace.name} unit={s.unit} rank={String(s.rank)} estimate={s.estimate}>
            {s.drift && s.computed && <div className="prov">Leif would put it {s.computed === 1 ? "first" : `at ${s.computed}`}</div>}
            {s.warnings.map((w) => (
              <div key={w} className="prov" style={{ color: "var(--amber)" }}>
                {w}
              </div>
            ))}
            <Slot>
              <Button size="sm" kind="ghost" disabled={busy || i === 0} title="Move up" onClick={() => shortlist(moved(list, s.unit, -1), `${s.unit.slice(0, 4)} moved up`)}>
                ↑
              </Button>
              <Button size="sm" kind="ghost" disabled={busy || i === list.length - 1} title="Move down" onClick={() => shortlist(moved(list, s.unit, 1), `${s.unit.slice(0, 4)} moved down`)}>
                ↓
              </Button>
              <Button size="sm" kind="ghost" icon="x" disabled={busy} title="Take off the shortlist" onClick={() => shortlist(list.filter((u) => u !== s.unit), `${s.unit.slice(0, 4)} taken off`)} />
            </Slot>
          </Row>
        ))}
        {!list.length && (
          <Empty icon="pause" title="The shortlist is empty">
            The autopilot has nothing to work on in {workspace.name}. Add from Leif's order below.
          </Empty>
        )}
      </div>
      {v.shortlist_record && (
        <div className="prov" style={{ marginTop: 6 }}>
          Last saved by {v.shortlist_record.by}: {v.shortlist_record.reason}
        </div>
      )}

      <div className="sec-h">
        Leif's order <span className="faint">{v.order.length || ""}</span>
      </div>
      <div className="card">
        {v.order.map((o) => (
          <Row key={o.unit} workspace={workspace.name} unit={o.unit} rank={String(o.computed)} estimate={o.estimate}>
            {o.estimate.basis && <div className="muted ellipsis" style={{ fontSize: 12.5 }} title={o.estimate.basis}>{o.estimate.basis}</div>}
            {o.agent_differs && <div className="prov">An agent estimated {brief(o.agent_differs)}</div>}
            <Slot>
              <Button size="sm" icon="plus" disabled={busy || full} title={full ? `The shortlist holds ${v.max}` : "Add at the end of the shortlist"} onClick={() => shortlist([...list, o.unit], `${o.unit.slice(0, 4)} added`)}>
                Add
              </Button>
            </Slot>
          </Row>
        ))}
        {!v.order.length && <div className="card-b faint">Nothing estimated waits outside the shortlist.</div>}
      </div>

      <FeatureSlots at="backlog" workspace={workspace} />
      {v.unestimated.length > 0 && (
        <>
          <div className="sec-h">
            Not estimated <span className="faint">{v.unestimated.length}</span>
          </div>
          <div className="card">
            {v.unestimated.map((u) => (
              <Row key={u} workspace={workspace.name} unit={u} rank="–" estimate={null}>
                <Estimate disabled={busy} onSave={(value, effort, basis) => save("/api/backlog/estimate", { unit: u, value, effort, basis })} />
              </Row>
            ))}
          </div>
          <Propose cwd={cwd} warning={v.propose_warning} onDone={view.reload} />
        </>
      )}
    </>
  );
}

const brief = (e: EstimateBrief) => `value ${e.value ?? "?"} · ${e.effort ?? "?"}`;

function Row({ workspace, unit, rank, estimate, children }: { workspace: string; unit: string; rank: string; estimate: EstimateBrief | null; children?: ReactNode }) {
  return (
    <div className="ny">
      <span className="faint" style={{ width: 22, textAlign: "right", paddingTop: 1 }}>{rank}</span>
      <div className="grow" style={{ minWidth: 0 }}>
        <div className="row" style={{ gap: 6 }}>
          <Link to={`/unit/${workspace}/${number(unit)}`} className="faint nowrap">
            {unitCode(workspace, number(unit))}
          </Link>
          <span className="ellipsis" style={{ fontWeight: 500 }}>{unitTitle(unit)}</span>
          {estimate && (
            <span title={estimate.effort_source === "measured" ? "Effort measured from similar units" : `Estimated by ${estimate.by}`}>
              <Chip square tone="plain">{brief(estimate)}</Chip>
            </span>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

/** Buttons at the end of a row, beside the title. */
function Slot({ children }: { children: ReactNode }) {
  return (
    <div className="row" style={{ gap: 2, position: "absolute", right: 12, top: 10 }}>
      {children}
    </div>
  );
}

function Estimate({ disabled, onSave }: { disabled: boolean; onSave: (value: number, effort: string, basis: string) => void }) {
  const [value, setValue] = useState(0);
  const [effort, setEffort] = useState("");
  const [basis, setBasis] = useState("");
  return (
    <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
      <div className="seg" title="Value: 1 low to 5 high">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} className={value === n ? "on" : ""} onClick={() => setValue(n)}>
            {n}
          </button>
        ))}
      </div>
      <div className="seg" title="Effort">
        {["S", "M", "L"].map((e) => (
          <button key={e} className={effort === e ? "on" : ""} onClick={() => setEffort(e)}>
            {e}
          </button>
        ))}
      </div>
      <input className="input sm grow" placeholder="Why this value?" value={basis} onChange={(e) => setBasis(e.target.value)} />
      <Button size="sm" kind="primary" disabled={disabled || !value || !effort || !basis.trim()} onClick={() => onSave(value, effort, basis.trim())}>
        Save
      </Button>
    </div>
  );
}

/** One paid session proposes estimates for every unit without one; asked once before it runs. */
function Propose({ cwd, warning, onDone }: { cwd: string; warning: string; onDone: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const run = async () => {
    if (!asking) return setAsking(true);
    setBusy(true);
    setError(null);
    try {
      await api.start("/api/backlog/propose", { cwd });
      setAsking(false);
      onDone();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ marginTop: 10 }}>
      <div className="row" style={{ gap: 8 }}>
        <Button size="sm" icon="wand" disabled={busy} title={warning} onClick={run}>
          {asking ? "Spend quota on an estimate session?" : "Ask an agent to estimate them"}
        </Button>
        {asking && !busy && (
          <Button size="sm" kind="ghost" onClick={() => setAsking(false)}>
            Cancel
          </Button>
        )}
      </div>
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 6 }}>{error.message}</div>}
    </div>
  );
}
