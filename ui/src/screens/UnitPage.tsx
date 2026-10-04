// One unit: what it is, where it stands, and what happens next. The timeline, live run and
// cost come in the next step; the frame shows what the app already answers.

import { useResource } from "../lib/api";
import { allUnits, useBoards } from "../lib/boards";
import { PHASE, STAGE_LABEL, unitCode, unitTitle } from "../lib/format";
import { unitState } from "../lib/model";
import { Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

type Next = { stage: string; action: string; reasons: string[]; blocked: boolean };

const PHASES = ["Shape", "Build", "Check", "Ship"] as const;

export function UnitPage({ workspace, number }: { workspace: string; number: string }) {
  const { boards, loading } = useBoards();
  const unit = allUnits(boards).find((u) => u.workspace.name === workspace && u.number === Number(number));
  const next = useResource<Next>(unit ? "/api/units/next" : null, unit ? { cwd: unit.workspace.path, unit: unit.name } : {}, { on: [""] });

  if (loading) return <div className="page"><SkeletonRows rows={5} /></div>;
  if (!unit)
    return (
      <div className="page">
        <Empty icon="search" title="No such unit">
          {workspace} has no unit {number}.
        </Empty>
      </div>
    );

  const state = unitState(unit);
  const phase = next.data?.stage ? PHASE[next.data.stage] : state.group === "Shipped" ? "Ship" : undefined;
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 6 }}>
        <Chip square>{unitCode(workspace, number)}</Chip>
        {unit.type && <Chip square tone="plain">{unit.type}</Chip>}
        <Chip tone={state.group === "Needs you" ? "amber" : state.group === "Shipped" ? "green" : "accent"}>{state.label}</Chip>
      </div>
      <PageHead title={unitTitle(unit.name)} />
      <div className="track" style={{ margin: "18px 0 24px" }}>
        {PHASES.map((p) => {
          const at = phase ? PHASES.indexOf(phase) : -1;
          const i = PHASES.indexOf(p);
          return (
            <div key={p} className={`st ${state.group === "Shipped" || i < at ? "done" : i === at ? "now" : ""}`}>
              <div className="grp">{p}</div>
              <div className="bar" />
            </div>
          );
        })}
      </div>
      <div className="sec-h">What happens next</div>
      {next.state === "error" ? (
        <ErrorState error={next.error} onRetry={next.reload} />
      ) : next.state === "loading" ? (
        <SkeletonRows rows={1} />
      ) : (
        <div className="card" style={{ padding: "12px 16px" }}>
          <b>{next.data.stage ? STAGE_LABEL[next.data.stage] : state.label}</b>
          <div className="muted" style={{ marginTop: 4 }}>
            {next.data.action}
          </div>
        </div>
      )}
    </div>
  );
}
