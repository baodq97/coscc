// The first screen: Leif's briefing. In the frame, the numbers are real and the prose is a
// fixed sentence built from them; Leif writes it once Leif lives in the app.

import { allUnits, failedLink, needsYou, proposalLink, useBoards, useLive } from "../lib/boards";
import { LeifAvatar } from "../lib/icons";
import { unitState } from "../lib/model";
import { Link } from "../lib/router";
import { ago, failureWords, unitCode, unitTitle } from "../lib/format";
import { Button, Empty, ErrorState, SkeletonRows } from "../components/ui";
import { waitingWhy } from "./UnitPage";

function greeting(now = new Date()): string {
  const h = now.getHours();
  return h < 12 ? "Good morning." : h < 18 ? "Good afternoon." : "Good evening.";
}

export function Briefing() {
  const { boards, loading: loadingBoards } = useBoards();
  const live = useLive();
  const loading = loadingBoards || live.loading;
  const proposals = live.proposals;
  const failed = live.failed;
  const { units: needs, total: waiting } = needsYou(allUnits(boards), proposals, failed);
  const asking = new Set(needs.map((u) => u.workspace.name + u.name));
  const held = allUnits(boards).filter((u) => u.waiting && !asking.has(u.workspace.name + u.name));
  const moving = boards.flatMap((b) => (b.board?.running ?? []).map((run) => ({ ...run, workspace: b.workspace.name, number: Number(run.unit.slice(0, 4)) })));
  const working = moving.length + live.running.length;
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
          ) : live.error ? (
            <div>Leif cannot read what the agents are doing now.</div>
          ) : (
            <div>
              <b>{waiting} {waiting === 1 ? "thing needs" : "things need"} you</b>, {working} {working === 1 ? "run is" : "runs are"} going
              across {boards.length} projects.{off.length ? ` The autopilot is off in ${off.join(" and ")}.` : ""}
            </div>
          )}
        </div>
      </div>

      <div className="sec-h">
        Needs you <span className="faint">{waiting || ""}</span>
      </div>
      <div className="card" style={{ overflow: "hidden" }}>
        {live.error ? (
          <ErrorState error={live.error} onRetry={live.reload} />
        ) : loading ? (
          <SkeletonRows rows={3} />
        ) : waiting ? (
          <>
            {needs.map((u) => (
              <Link key={u.workspace.name + u.name} to={`/unit/${u.workspace.name}/${u.number}`} className="lrow">
                <span className="id">{unitCode(u.workspace.name, u.number)}</span>
                <span className="t">{unitTitle(u.name)}</span>
                <span className="meta">{unitState(u).label}</span>
              </Link>
            ))}
            {failed.length > 0 && (
              <>
                <div className="lgroup">
                  Agent runs that failed <span className="n">{failed.length}</span>
                </div>
                {failed.map((f) => (
                  <Link key={f.workspace + f.agent} to={failedLink(f)} className="lrow stack">
                    <span className="id">{f.name}</span>
                    <span className="t">{failureWords(f.detail || "The last run failed.").plain.split("\n")[0]}</span>
                    <span className="meta">{f.workspace} · {ago(f.at)}</span>
                  </Link>
                ))}
              </>
            )}
            {proposals.length > 0 && (
              <>
                <div className="lgroup">
                  Proposals to decide <span className="n">{proposals.length}</span>
                  <span className="r">newest first</span>
                </div>
                {proposals.slice(0, 5).map((p) => (
                  <Link key={p.workspace + p.id} to={proposalLink(p)} className="lrow stack">
                    <span className="id">{p.agent_name}</span>
                    <span className="t">{p.title}</span>
                    <span className="meta">{p.workspace}</span>
                  </Link>
                ))}
                {proposals.length > 5 && (
                  <Link to="/inbox" className="lrow">
                    <span className="t faint">{proposals.length - 5} more in Needs you</span>
                  </Link>
                )}
              </>
            )}
          </>
        ) : (
          <Empty icon="check" title="Nothing needs you">
            Leif will bring you the next question or merge.
          </Empty>
        )}
      </div>

      {!loading && held.length > 0 && (
        <>
          <div className="sec-h">
            Waiting <span className="faint">{held.length}</span>
          </div>
          <div className="card" style={{ overflow: "hidden" }}>
            {held.map((u) => (
              <Link key={u.workspace.name + u.name} to={`/unit/${u.workspace.name}/${u.number}`} className="lrow stack">
                <span className="id">{unitCode(u.workspace.name, u.number)}</span>
                <span className="t">{unitTitle(u.name)}</span>
                <span className="meta">{u.waiting && waitingWhy(u.waiting)}</span>
              </Link>
            ))}
          </div>
        </>
      )}

      <div className="sec-h">
        Moving now <span className="faint">{working || ""}</span>
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
        ) : working ? (
          <>
            {live.running.map((r) => (
              <Link key={r.run} to={`/run/${r.workspace}/${r.run}`} className="lrow">
                <span className="id nowrap"><span className="dot live" /> {r.workspace}</span>
                <span className="t">{r.name}</span>
                <span className="meta">running · {ago(r.started)} ▸ live</span>
              </Link>
            ))}
            {moving.slice(0, 8).map((r) => (
            <Link key={r.workspace + r.unit + r.stage} to={`/unit/${r.workspace}/${r.number}`} className="lrow">
              <span className="id">{unitCode(r.workspace, r.number)}</span>
              <span className="t">{unitTitle(r.unit)}</span>
              <span className="meta">
                {r.stage} · {ago(r.started)}
              </span>
            </Link>
            ))}
          </>
        ) : (
          <Empty icon="board" title="Nothing is moving">
            Hand over new work with <kbd>C</kbd>, or ask Leif what to start.
          </Empty>
        )}
      </div>
    </div>
  );
}
