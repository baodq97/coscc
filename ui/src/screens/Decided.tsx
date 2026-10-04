// What Leif and the agents decided for the owner, newest first, in every project: each answer
// with the question it settled and the unit it moved. Overruling one comes with Leif's backend.

import type { Decided as Row } from "../api.gen";
import { useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { unitCode, unitTitle } from "../lib/format";
import { LeifMark } from "../lib/icons";
import type { Workspace } from "../lib/model";
import { Link } from "../lib/router";
import { Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

export function Decided() {
  const { boards, loading } = useBoards();
  return (
    <div className="page mid">
      <PageHead title="Leif decided" lede="Every question Leif or an agent answered in your place, newest first. Your own answers are not listed." />
      {loading ? <SkeletonRows rows={6} /> : boards.map((b) => <Project key={b.workspace.path} workspace={b.workspace} />)}
    </div>
  );
}

function Project({ workspace }: { workspace: Workspace }) {
  const decided = useResource("/api/decided", { cwd: workspace.path }, { on: ["answer."] });
  const rows = decided.data ?? [];
  const days = [...new Set(rows.map((r) => r.date))];
  return (
    <>
      <div className="sec-h">
        {workspace.name} <span className="faint">{rows.length}</span>
      </div>
      {decided.state === "error" ? (
        <ErrorState error={decided.error} onRetry={decided.reload} />
      ) : decided.state === "loading" ? (
        <SkeletonRows rows={3} />
      ) : !rows.length ? (
        <Empty icon="decided" title="Nothing decided for you here">
          Every answer in {workspace.name} is yours.
        </Empty>
      ) : (
        days.slice(0, 7).map((day) => (
          <div key={day} className="card" style={{ marginBottom: 10 }}>
            <div className="card-h">
              <span className="faint" style={{ fontWeight: 500 }}>{day}</span>
            </div>
            {rows
              .filter((r) => r.date === day)
              .map((r) => (
                <Item key={`${r.unit}-${r.artifact}-${r.n}`} row={r} workspace={workspace.name} />
              ))}
          </div>
        ))
      )}
    </>
  );
}

function Item({ row, workspace }: { row: Row; workspace: string }) {
  const number = Number(row.unit.slice(0, 4));
  return (
    <div className="ny" style={{ alignItems: "flex-start" }}>
      <span className="tl-ic" style={{ width: 21, height: 21, display: "grid", placeItems: "center", borderRadius: "50%", background: "var(--accent)", color: "#fff", flex: "none" }}>
        <LeifMark size={10} />
      </span>
      <div className="grow" style={{ minWidth: 0 }}>
        <div className="row" style={{ gap: 6 }}>
          <Link to={`/unit/${workspace}/${number}`} className="faint nowrap">
            {unitCode(workspace, number)}
          </Link>
          <span className="ellipsis faint">{unitTitle(row.unit)}</span>
          <span className="faint nowrap" style={{ marginLeft: "auto", fontSize: 12 }}>
            {row.artifact} · {row.n}
          </span>
        </div>
        {row.question && <div style={{ fontWeight: 500, marginTop: 4 }}>{row.question.replace(/\*\*/g, "")}</div>}
        <div className="muted" style={{ marginTop: 2 }}>{row.text}</div>
        <div className="prov" style={{ marginTop: 4 }}>{row.by}</div>
      </div>
    </div>
  );
}
