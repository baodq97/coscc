// The first screen: Leif's briefing. In the frame, the numbers are real and the prose is a
// fixed sentence built from them; Leif writes it once Leif lives in the app.

import { allUnits, useBoards } from "../lib/boards";
import { LeifAvatar } from "../lib/icons";
import { unitState } from "../lib/model";
import { Link } from "../lib/router";
import { ago, unitCode, unitTitle } from "../lib/format";
import { Button, Empty, SkeletonRows } from "../components/ui";

function greeting(now = new Date()): string {
  const h = now.getHours();
  return h < 12 ? "Good morning." : h < 18 ? "Good afternoon." : "Good evening.";
}

export function Briefing() {
  const { boards, loading } = useBoards();
  const units = allUnits(boards);
  const needs = units.filter((u) => unitState(u).group === "Needs you");
  const moving = boards.flatMap((b) => (b.board?.running ?? []).map((run) => ({ ...run, workspace: b.workspace.name, number: Number(run.unit.slice(0, 4)) })));
  const off = boards.filter((b) => b.board && !b.board.autopilot?.on).map((b) => b.workspace.name);

  return (
    <div className="page mid">
      <div className="faint" style={{ fontSize: 12.5 }}>
        {new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 600, margin: "4px 0 16px" }}>{greeting()}</h1>
      <div className="leif-say" style={{ marginBottom: 24 }}>
        <LeifAvatar size="lg" />
        <div className="bubble" style={{ fontSize: 14.5, lineHeight: 1.6 }}>
          <div className="leif-name">
            Leif <span className="faint">your chief of staff</span>
          </div>
          {loading ? (
            <span className="sk" style={{ width: 360, height: 12, display: "inline-block" }} />
          ) : (
            <div>
              <b>{needs.length} {needs.length === 1 ? "thing needs" : "things need"} you</b>, {moving.length} {moving.length === 1 ? "step is" : "steps are"} running
              across {boards.length} projects.{off.length ? ` The autopilot is off in ${off.join(" and ")}.` : ""}
            </div>
          )}
        </div>
      </div>

      <div className="sec-h">
        Needs you <span className="faint">{needs.length || ""}</span>
      </div>
      <div className="card" style={{ overflow: "hidden" }}>
        {loading ? (
          <SkeletonRows rows={3} />
        ) : needs.length ? (
          needs.map((u) => (
            <Link key={u.workspace.name + u.name} to={`/unit/${u.workspace.name}/${u.number}`} className="lrow">
              <span className="id">{unitCode(u.workspace.name, u.number)}</span>
              <span className="t">{unitTitle(u.name)}</span>
              <span className="meta">{unitState(u).label}</span>
            </Link>
          ))
        ) : (
          <Empty icon="check" title="Nothing needs you">
            Leif will bring you the next question or merge.
          </Empty>
        )}
      </div>

      <div className="sec-h">
        Moving now <span className="faint">{moving.length || ""}</span>
        <span className="r">
          <Link to="/work">
            <Button size="sm" kind="ghost" icon="arrow">
              All work
            </Button>
          </Link>
        </span>
      </div>
      <div className="card" style={{ overflow: "hidden" }}>
        {loading ? (
          <SkeletonRows rows={4} />
        ) : moving.length ? (
          moving.slice(0, 8).map((r) => (
            <Link key={r.workspace + r.unit + r.stage} to={`/unit/${r.workspace}/${r.number}`} className="lrow">
              <span className="id">{unitCode(r.workspace, r.number)}</span>
              <span className="t">{unitTitle(r.unit)}</span>
              <span className="meta">
                {r.stage} · {ago(r.started)}
              </span>
            </Link>
          ))
        ) : (
          <Empty icon="board" title="Nothing is moving">
            Hand over new work with <kbd>C</kbd>, or ask Leif what to start.
          </Empty>
        )}
      </div>
    </div>
  );
}
