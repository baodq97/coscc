// What Leif and the agents decided for the owner, newest first, in every project: each answer
// with the question it settled and the unit it moved. Overruling one comes with Leif's backend.

import type { Decided as Row } from "../api.gen";
import { useEffect, useState } from "react";
import { useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { unitCode, unitTitle } from "../lib/format";
import { LeifMark } from "../lib/icons";
import type { Workspace } from "../lib/model";
import { Link } from "../lib/router";
import { Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const PAGE = 50;

export function Decided() {
  const { boards, loading } = useBoards();
  const [q, setQ] = useState("");
  return (
    <div className="page mid">
      <PageHead title="Leif decided" lede="Every question Leif or an agent answered in your place, newest first. Your own answers are not listed." />
      <input className="input" style={{ marginBottom: 12, width: "100%", maxWidth: 360 }} type="search" placeholder="Filter by unit, question or answer…" aria-label="Filter decisions" value={q} onChange={(e) => setQ(e.target.value)} />
      {loading ? <SkeletonRows rows={6} /> : boards.map((b) => <Project key={b.workspace.path} workspace={b.workspace} q={q} />)}
    </div>
  );
}

function Project({ workspace, q }: { workspace: Workspace; q: string }) {
  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [q]);
  const decided = useResource("/api/decided", { cwd: workspace.path, q, offset: "0" }, { on: ["answer."] });
  const total = decided.data?.total ?? 0;
  return (
    <>
      <div className="sec-h">
        {workspace.name} <span className="faint">{total}</span>
      </div>
      {decided.state === "error" ? (
        <ErrorState error={decided.error} onRetry={decided.reload} />
      ) : decided.state === "loading" ? (
        <SkeletonRows rows={3} />
      ) : !total ? (
        <Empty icon="decided" title={q ? "Nothing matches" : "Nothing decided for you here"}>
          {q ? "Try fewer words." : `Every answer in ${workspace.name} is yours.`}
        </Empty>
      ) : (
        <>
          <Days rows={decided.data?.rows ?? []} workspace={workspace.name} />
          {Array.from({ length: pages - 1 }, (_, i) => (
            <Later key={i} workspace={workspace} q={q} offset={(i + 1) * PAGE} />
          ))}
          {pages * PAGE < total && (
            <button className="btn" style={{ marginBottom: 10 }} onClick={() => setPages(pages + 1)}>
              Show more
            </button>
          )}
        </>
      )}
    </>
  );
}

function Later({ workspace, q, offset }: { workspace: Workspace; q: string; offset: number }) {
  const page = useResource("/api/decided", { cwd: workspace.path, q, offset: String(offset) }, { on: ["answer."] });
  if (page.state === "error") return <ErrorState error={page.error} onRetry={page.reload} />;
  return page.data ? <Days rows={page.data.rows} workspace={workspace.name} /> : <SkeletonRows rows={3} />;
}

function Days({ rows, workspace }: { rows: Row[]; workspace: string }) {
  return [...new Set(rows.map((r) => r.date))].map((day) => (
    <div key={day} className="card" style={{ marginBottom: 10 }}>
      <div className="card-h">
        <span className="faint" style={{ fontWeight: 500 }}>{day}</span>
      </div>
      {rows
        .filter((r) => r.date === day)
        .map((r) => (
          <Item key={`${r.unit}-${r.artifact}-${r.n}`} row={r} workspace={workspace} />
        ))}
    </div>
  ));
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
        <div className="prov" style={{ marginTop: 4 }}>{row.name}</div>
      </div>
    </div>
  );
}
