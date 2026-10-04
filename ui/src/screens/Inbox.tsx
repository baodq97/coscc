// What waits on the owner, across every project: today, the questions agents asked. One item a
// unit, newest first; the right pane answers them where they stand.

import { useState } from "react";
import type { Question } from "../api.gen";
import { api, useResource } from "../lib/api";
import { allUnits, useBoards, type PlacedUnit } from "../lib/boards";
import { STAGE_LABEL, ago, unitCode, unitTitle } from "../lib/format";
import { Icon } from "../lib/icons";
import { Link, navigate } from "../lib/router";
import { Button, Chip, Empty, ErrorState, SkeletonRows } from "../components/ui";
import { Track } from "./UnitPage";

export function Inbox({ workspace, number }: { workspace?: string; number?: string }) {
  const { boards, loading } = useBoards();
  const waiting = allUnits(boards)
    .filter((u) => u.open > 0)
    .sort((a, b) => b.at.localeCompare(a.at));
  const chosen = waiting.find((u) => u.workspace.name === workspace && u.number === Number(number)) ?? waiting[0];

  if (loading) return <div className="page"><SkeletonRows rows={4} /></div>;
  if (!waiting.length)
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
          Needs you <span className="n">{waiting.length}</span>
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
                <span className="faint nowrap" style={{ fontSize: 12, marginLeft: "auto" }}>{ago(u.at)}</span>
              </div>
              <div className="ny-s ellipsis" style={{ fontSize: 12.5 }}>{unitTitle(u.name)}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="rp">{chosen && <Questions unit={chosen} />}</div>
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
        {stage && <Chip square>{STAGE_LABEL[stage] ?? stage}</Chip>}
      </div>
      <h1 className="title" style={{ fontSize: 19, marginTop: 12 }}>{unitTitle(unit.name)}</h1>
      {d && (
        <div style={{ marginTop: 14 }}>
          <Track stages={d.stages} now={stage} done={false} waiting />
        </div>
      )}
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

  const send = async () => {
    setSending(true);
    setError(null);
    try {
      await api.post("/api/units/answer", { cwd: unit.workspace.path, unit: unit.name, artifact: question.artifact, question: question.n, answer: text.trim() });
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
        <textarea
          className="ta"
          rows={3}
          style={{ marginTop: 14, fontSize: 13 }}
          placeholder="Your answer…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) send();
          }}
        />
        {error && <div style={{ color: "var(--red)", marginTop: 8, fontSize: 12.5 }}>{error.message}</div>}
        <div className="row" style={{ marginTop: 12 }}>
          <span className="faint" style={{ fontSize: 12 }}>The agent reads it as your decision.</span>
          <span className="grow" />
          <Button kind="primary" disabled={!text.trim() || sending} onClick={send}>
            {sending ? "Answering…" : "Answer"}
          </Button>
        </div>
      </div>
    </div>
  );
}
