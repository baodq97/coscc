// What waits on the owner, across every project: today, the questions agents asked. One item a
// unit, newest first; the right pane answers them where they stand.

import { useState } from "react";
import type { Question } from "../api.gen";
import { api, useResource } from "../lib/api";
import { allUnits, failedLink, needsYou, proposalLink, useBoards, useLive, type PlacedUnit } from "../lib/boards";
import { liveQuestions, unitState } from "../lib/model";
import { ago, unitCode, unitTitle } from "../lib/format";
import { Icon } from "../lib/icons";
import { stageLabel } from "../lib/pack";
import { Link, navigate } from "../lib/router";
import { Button, Chip, Empty, ErrorState, SkeletonRows } from "../components/ui";

/** What the page shows: a link to an item that is not waiting is "missing", whether or not others wait. */
export function inboxView(waiting: number, asked: boolean, found: boolean): "empty" | "missing" | "list" {
  if (asked && !found) return "missing";
  return waiting ? "list" : "empty";
}

export function Inbox({ workspace, number }: { workspace?: string; number?: string }) {
  const { boards, loading: loadingBoards } = useBoards();
  const { proposals, failed, loading: loadingLive } = useLive();
  const loading = loadingBoards || loadingLive;
  const needs = needsYou(allUnits(boards), proposals, failed);
  const newest = (a: PlacedUnit, b: PlacedUnit) => b.updated.localeCompare(a.updated);
  const waiting = needs.units.filter((u) => liveQuestions(u) > 0).sort(newest);
  // Units that need a person without a question: a pause at a ceiling, a missing file.
  const other = needs.units.filter((u) => liveQuestions(u) === 0).sort(newest);
  const asked = workspace !== undefined && number !== undefined;
  const found = waiting.find((u) => u.workspace.name === workspace && u.number === Number(number));
  const chosen = found ?? (asked ? undefined : waiting[0]);

  if (loading) return <div className="page"><SkeletonRows rows={4} /></div>;
  const view = inboxView(needs.total, asked, Boolean(found));
  if (view === "missing")
    return (
      <div className="page mid">
        <Empty icon="search" title="Not found in what needs you" actions={<Button kind="primary" onClick={() => navigate("/inbox")}>Back to Needs you</Button>}>
          {unitCode(workspace ?? "", Number(number))} has no question waiting on you: it may be answered, dropped or shipped.
        </Empty>
      </div>
    );
  if (view === "empty")
    return (
      <div className="page mid">
        <Empty icon="inbox" title="Nothing needs you">
          No agent is waiting on an answer in any project.
        </Empty>
      </div>
    );

  return (
    <div className="two">
      <div className="lp">
        <div className="lgroup" style={{ background: "var(--panel)" }}>
          Needs you <span className="n">{needs.total}</span>
          <span className="r">newest first</span>
        </div>
        {waiting.map((u) => (
          <div
            key={`${u.workspace.name}/${u.name}`}
            className={`ny ${u === chosen ? "sel" : ""}`}
            style={{ cursor: "pointer" }}
            onClick={() => navigate(`/inbox/${u.workspace.name}/${u.number}`)}
          >
            <div className="ny-ic q">
              <Icon name="chat" size={15} />
            </div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="row">
                <span className="ny-t ellipsis" style={{ fontSize: 13 }}>
                  {u.open} question{u.open > 1 ? "s" : ""} on {unitCode(u.workspace.name, u.number)}
                </span>
                <span className="faint nowrap" style={{ fontSize: 12, marginLeft: "auto" }}>{u.updated ? ago(u.updated) : ""}</span>
              </div>
              <div className="ny-s ellipsis" style={{ fontSize: 12.5 }}>{unitTitle(u.name)}</div>
            </div>
          </div>
        ))}
        {other.map((u) => (
          <Link key={`${u.workspace.name}/${u.name}`} to={`/unit/${u.workspace.name}/${u.number}`} className="ny">
            <div className="ny-ic">
              <Icon name="arrow" size={15} />
            </div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="row">
                <span className="ny-t ellipsis" style={{ fontSize: 13 }}>{unitState(u).label} on {unitCode(u.workspace.name, u.number)}</span>
                <span className="faint nowrap" style={{ fontSize: 12, marginLeft: "auto" }}>{u.updated ? ago(u.updated) : ""}</span>
              </div>
              <div className="ny-s ellipsis" style={{ fontSize: 12.5 }}>{unitTitle(u.name)}</div>
            </div>
          </Link>
        ))}
        {failed.length > 0 && (waiting.length > 0 || other.length > 0) && (
          <div className="lgroup" style={{ background: "var(--panel)" }}>
            Agent runs that failed <span className="n">{failed.length}</span>
          </div>
        )}
        {failed.map((f) => (
          <Link key={f.workspace + f.agent} to={failedLink(f)} className="ny">
            <div className="ny-ic">
              <Icon name="warn" size={15} />
            </div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="row">
                <span className="ny-t ellipsis" style={{ fontSize: 13 }}>{f.name} failed on {f.workspace}</span>
                <span className="faint nowrap" style={{ fontSize: 12, marginLeft: "auto" }}>{ago(f.at)}</span>
              </div>
              <div className="ny-s ellipsis" style={{ fontSize: 12.5 }}>{(f.detail || "Open the run to see why.").split("\n")[0]}</div>
            </div>
          </Link>
        ))}
        {proposals.length > 0 && (waiting.length > 0 || other.length > 0) && (
          <div className="lgroup" style={{ background: "var(--panel)" }}>
            Proposals to decide <span className="n">{proposals.length}</span>
          </div>
        )}
        {proposals.map((p) => (
          <Link key={p.workspace + p.id} to={proposalLink(p)} className="ny">
            <div className="ny-ic">
              <Icon name="arrow" size={15} />
            </div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="row">
                <span className="ny-t ellipsis" style={{ fontSize: 13 }}>{p.agent_name} proposed on {p.workspace}</span>
                <span className="faint nowrap" style={{ fontSize: 12, marginLeft: "auto" }}>{ago(p.at)}</span>
              </div>
              <div className="ny-s ellipsis" style={{ fontSize: 12.5 }}>{p.title}</div>
            </div>
          </Link>
        ))}
      </div>
      <div className="rp">
        {chosen ? (
          <Questions unit={chosen} />
        ) : (
          <Empty
            icon="inbox"
            title={proposals.length || failed.length ? `${proposals.length + failed.length} thing${proposals.length + failed.length === 1 ? "" : "s"} to look at` : "No question waits for you"}
          >
            {proposals.length || failed.length ? "No question waits for you. Open a proposal or a failed run on the left." : "Units that need you are listed on the left."}
          </Empty>
        )}
      </div>
    </div>
  );
}

function Questions({ unit }: { unit: PlacedUnit }) {
  const detail = useResource("/api/units/{name}", { cwd: unit.workspace.path, name: unit.name }, { on: ["answer.", "step."] });
  const d = detail.data;
  const open = d?.questions.filter((q) => !q.answered) ?? [];
  const stage = d?.stages.find((s) => s.file === open[0]?.artifact)?.stage ?? unit.next_stage;

  return (
    <div style={{ padding: "24px 36px 48px", maxWidth: 800 }}>
      <div className="row" style={{ gap: 6 }}>
        <Link className="faint" to={`/unit/${unit.workspace.name}/${unit.number}`}>
          {unitCode(unit.workspace.name, unit.number)}
        </Link>
        {unit.type && <Chip square tone="plain">{unit.type}</Chip>}
        {stage && <Chip square>{stageLabel(stage)}</Chip>}
      </div>
      <h1 className="title" style={{ fontSize: 19, marginTop: 12 }}>{unitTitle(unit.name)}</h1>
      {detail.state === "error" ? (
        <ErrorState error={detail.error} onRetry={detail.reload} />
      ) : !d ? (
        <SkeletonRows rows={3} />
      ) : !open.length ? (
        <div className="callout accent" style={{ marginTop: 20 }}>
          <Icon name="check" size={15} />
          <div className="grow">
            <b>All answered.</b>
            <div className="muted">The unit moves on when its next step runs.</div>
          </div>
        </div>
      ) : (
        open.map((q, i) => (
          <QuestionCard key={`${q.artifact}-${q.n}`} unit={unit} question={q} index={i + 1} of={open.length} onAnswered={detail.reload} />
        ))
      )}
    </div>
  );
}

function QuestionCard({ unit, question, index, of, onAnswered }: { unit: PlacedUnit; question: Question; index: number; of: number; onAnswered: () => void }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const recommended = question.recommendation.trim();
  // Your press: the typed answer, or the recommendation taken as it is.
  const send = async (answer: string) => {
    setSending(true);
    setError(null);
    try {
      await api.post("/api/units/answer", { cwd: unit.workspace.path, unit: unit.name, artifact: question.artifact, question: question.n, answer, by: "person" });
      setText("");
      onAnswered();
    } catch (e) {
      setError(e as Error);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-b" style={{ padding: 18 }}>
        <div className="faint" style={{ fontSize: 12, fontWeight: 500, marginBottom: 6 }}>
          Question {index} of {of} · {question.artifact}
        </div>
        <div className="qtext">{question.text.replace(/\*\*/g, "")}</div>
        {recommended && (
          <div className="callout accent" style={{ marginTop: 12 }}>
            <Icon name="wand" size={15} />
            <div className="grow">
              <b>Recommended</b>
              <div style={{ marginTop: 2 }}>{recommended}</div>
            </div>
            <Button disabled={sending} onClick={() => send(recommended)}>
              Take it
            </Button>
          </div>
        )}
        <textarea
          className="ta"
          rows={3}
          style={{ marginTop: 14, fontSize: 13 }}
          placeholder={recommended ? "Or your own answer…" : "Your answer…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) send(text.trim());
          }}
        />
        {error && <div style={{ color: "var(--red)", marginTop: 8, fontSize: 12.5 }}>{error.message}</div>}
        <div className="row" style={{ marginTop: 12 }}>
          <span className="faint" style={{ fontSize: 12 }}>The agent reads it as your decision.</span>
          <span className="grow" />
          <Button kind="primary" disabled={!text.trim() || sending} onClick={() => send(text.trim())}>
            {sending ? "Answering…" : "Answer"}
          </Button>
        </div>
      </div>
    </div>
  );
}
