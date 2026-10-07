// What one run did, call by call: the agent's words, each tool it used and on what, what was
// refused or failed, and how it ended. Any agent's run, with a unit or none (an estimate, a scan,
// a chat turn). A running run is followed live; an ended one is read once.

import { useEffect, useRef, useState } from "react";
import type { Asked, EventsPage, Followup, StepEvent } from "../api.gen";
import { api, useResource } from "../lib/api";
import { agentFace, stageLabel } from "../lib/pack";
import { OUTCOME, resultWords, shallowWords } from "./AgentActivity";
import { failureWords, modelName, startedBy, money, toolName, unitCode, unitTitle } from "../lib/format";
import { Link, useQuery } from "../lib/router";
import { Button, Chip, ErrorState, Markdown, PageHead, SkeletonRows } from "../components/ui";

const PAGE = "200";

/** What a tool call reads as: its command, file, pattern or description, whole. */
function toolText(input: unknown): string {
  if (!input || typeof input !== "object") return String(input ?? "");
  const i = input as Record<string, unknown>;
  for (const key of ["command", "file_path", "path", "pattern", "url", "query", "description", "prompt"]) {
    if (typeof i[key] === "string" && i[key]) return i[key] as string;
  }
  return JSON.stringify(input).slice(0, 160);
}

/** The one line a tool call reads as. */
export function toolSummary(input: unknown): string {
  return toolText(input).split("\n")[0];
}

/** The first line of a tool call, and how many lines follow it (0 for a one-line call). */
export function toolLines(input: unknown): { first: string; more: number; full: string } {
  const full = toolText(input).replace(/\s+$/, "");
  const lines = full.split("\n");
  return { first: lines[0], more: lines.length - 1, full };
}

/** `text` with every path into the unit's own worktree read from that worktree: `…/0001_x/a.py` is `a.py`. */
export function inUnit(text: string, unit: string): string {
  return unit ? text.split(new RegExp(`[^\\s'"]*/${unit}/`)).join("") : text;
}

/** `events` with `more` added in order, none twice. */
export function merged(events: StepEvent[], more: StepEvent[]): StepEvent[] {
  const seen = new Set(events.map((e) => e.seq));
  return [...events, ...more.filter((e) => !seen.has(e.seq))].sort((a, b) => a.seq - b.seq);
}

/** What a run's header says, from its events and its first page: how it ended and what it took. */
export function runFacts(events: StepEvent[], page: Pick<EventsPage, "status" | "outcome" | "started_by">): { label: string; value: string }[] {
  const config = events.find((e) => e.kind === "config");
  const result = [...events].reverse().find((e) => e.kind === "result");
  const end = [...events].reverse().find((e) => e.kind === "end");
  const outcome = page.status === "running" ? "running" : (page.outcome || end?.outcome || "").replace("paused-budget", "paused at its ceiling");
  const secs = result?.duration_ms != null ? Math.round(result.duration_ms / 1000) : null;
  const took = secs == null ? "" : secs >= 60 ? `${Math.floor(secs / 60)} min ${secs % 60} s` : `${secs} s`;
  const facts = [
    { label: "Outcome", value: outcome },
    { label: "Cost", value: result?.cost_usd != null ? money(result.cost_usd) : "" },
    { label: "Turns", value: result?.num_turns != null ? String(result.num_turns) : "" },
    { label: "Took", value: took },
    { label: "Model", value: config?.model ? [modelName(config.model), config.effort].filter(Boolean).join(" · ") : "" },
    { label: "Started by", value: startedBy(page.started_by) },
  ];
  return facts.filter((f) => f.value);
}

function firstLine(content: unknown): string {
  const text = typeof content === "string" ? content : Array.isArray(content) ? content.map((c) => (c && typeof c === "object" && "text" in c ? String(c.text) : "")).join(" ") : "";
  return text.trim().split("\n")[0].slice(0, 200);
}

/** One run on a page of its own, any agent's: what ran, for which unit if any, and what it did. */
export function RunPage({ workspace, run }: { workspace: string; run: string }) {
  const list = useResource("/api/workspaces");
  const cwd = list.data?.workspaces.find((w) => w.name === workspace)?.path ?? "";
  const head = useResource(cwd ? "/api/runs/{run}" : null, { cwd, run, limit: "1" });
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  if (list.state === "error") return <ErrorState error={list.error} onRetry={list.reload} />;
  if (head.state === "error") return <ErrorState error={head.error} onRetry={head.reload} />;
  const page = head.data;
  const number = page?.unit ? Number(page.unit.slice(0, 4)) : 0;
  // A board step is stopped from its unit; an agent's run or a question here.
  const stoppable = page?.status === "running" && !page.unit;
  const stop = async () => {
    setStopping(true);
    try {
      await api.post("/api/runs/" + encodeURIComponent(run) + "/stop", { cwd });
      // "Stopping…" stays until the run's `end` arrives (the log's `onEnd` reloads the page).
      setTimeout(head.reload, 1500);
    } catch (e) {
      setStopError((e as Error).message);
      setStopping(false);
    }
  };
  return (
    <div className="page mid">
      <PageHead
        title={page ? (page.stage === "ask" ? "A question asked of a run" : `${page.unit ? stageLabel(page.stage) : agentFace(page.stage).name} run`) : "Run"}
        actions={
          stoppable ? (
            <Button size="sm" kind="danger" icon="x" disabled={stopping} onClick={stop}>
              {stopping ? "Stopping…" : "Stop this run"}
            </Button>
          ) : undefined
        }
        lede={
          page ? (
            <>
              {page.unit ? (
                <Link to={`/unit/${workspace}/${number}`}>
                  {unitCode(workspace, number)} {unitTitle(page.unit)}
                </Link>
              ) : (
                `A run of the workspace, not of a unit${page.started_by ? `; started by ${startedBy(page.started_by)}` : ""}.`
              )}{" "}
              {page.status === "running" ? <Chip tone="accent">{stopping ? "stopping…" : "running"}</Chip> : null}
            </>
          ) : undefined
        }
      />
      {page && !page.unit && page.stage !== "ask" && page.stage !== "chat" && (
        <div className="crumb-line faint">
          <Link to={`/agents?ws=${encodeURIComponent(workspace)}`}>Agents</Link> / <Link to={`/agents/${page.stage}?ws=${encodeURIComponent(workspace)}`}>{agentFace(page.stage).name}</Link>
        </div>
      )}
      {page && page.outcome && <RunResult page={page} />}
      {stopError && <div className="rl-bad" style={{ fontSize: 12.5 }}>{stopError}</div>}
      <div style={{ marginTop: 16 }}>{cwd ? <RunLog cwd={cwd} run={run} live={false} whole onEnd={head.reload} /> : <SkeletonRows rows={3} />}</div>
      {cwd && page && page.status !== "running" && page.stage !== "chat" && <AskRun cwd={cwd} run={run} workspace={workspace} />}
    </div>
  );
}

/** What an ended run came to, in one line: how it ended, what it made, and whether it was only partly checked. */
function RunResult({ page }: { page: EventsPage }) {
  const made = resultWords({ made: page.made ?? null, verdict: page.verdict ?? "" });
  const thin = shallowWords({ refused: page.refused, verdict: page.verdict });
  const said = failureWords(page.detail);
  return (
    <div className="row run-result" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
      {page.outcome !== "done" && <Chip square tone={page.outcome === "failed" ? "red" : "amber"}>{OUTCOME[page.outcome ?? ""] ?? page.outcome}</Chip>}
      {made && <Chip square tone="accent">{made}</Chip>}
      {thin && <Chip square tone="amber">partly checked: {thin}</Chip>}
      {page.helpers ? <span className="faint">{page.helpers} helper{page.helpers === 1 ? "" : "s"}</span> : null}
      {said.plain && <span className="faint">{said.plain}</span>}
      {said.raw && (
        <details className="faint" style={{ flexBasis: "100%" }}>
          <summary>details</summary>
          <pre className="rl-full">{said.raw}</pre>
        </details>
      )}
    </div>
  );
}

/** What a question cost and how it was answered: in the run's warm session, or afresh and why. */
function howAnswered(f: Pick<Followup, "resumed" | "why" | "cost_usd">): string {
  const how = f.resumed ? "Answered in the same session, still warm" : `Answered afresh: ${f.why}`;
  return f.cost_usd != null ? `${how} · ${money(f.cost_usd)}` : how;
}

/** Questions asked of an ended run, their answers, and the box to ask one more. */
function AskRun({ cwd, run, workspace }: { cwd: string; run: string; workspace: string }) {
  // Read again every 2 s while a question is being answered: its answer lands with its `end`.
  const [waiting, setWaiting] = useState(false);
  const thread = useResource("/api/runs/{run}/thread", { cwd, run }, { every: waiting ? 2000 : 0 });
  const wanted = useQuery("ask");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asked, setAsked] = useState<Asked | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const followups = thread.data?.followups ?? [];
  const known = followups.find((f) => f.run === asked?.run);
  // The question being answered: the one just asked until the thread lists it ended, else any still running.
  const live = asked && (!known || known.outcome === "running") ? asked.run : followups.find((f) => f.outcome === "running")?.run;
  useEffect(() => {
    setWaiting(!!live);
  }, [live]);
  useEffect(() => {
    if (wanted) box.current?.focus();
  }, [wanted, thread.state]);
  if (thread.state === "error") return <ErrorState error={thread.error} onRetry={thread.reload} />;
  if (!thread.data) return <SkeletonRows rows={2} />;
  const { ask } = thread.data;
  const send = async () => {
    const said = text.trim();
    if (!said || busy) return;
    setBusy(true);
    setError("");
    try {
      setAsked(await api.post<Asked>("/api/runs/" + encodeURIComponent(run) + "/ask", { cwd, text: said }));
      setText("");
      thread.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const answering = (r: string) => (
    <div>
      <div className="faint ask-meta">
        Answering
        {asked && asked.run === r ? (asked.resumed ? " in the same session" : `, afresh: ${asked.why}`) : ""}…
      </div>
      <RunLog cwd={cwd} run={r} live />
    </div>
  );
  return (
    <div className="ask">
      <div className="sec-h">Ask this run</div>
      {followups.map((f) => (
        <div key={f.run} className="col" style={{ gap: 6 }}>
          <div className="ask-q">{f.question}</div>
          {f.outcome === "running" ? (
            answering(f.run)
          ) : (
            <>
              <div className="ask-a">{f.answer ? <Markdown text={f.answer} /> : <span className="faint">No answer: it {f.outcome}.</span>}</div>
              <div className="faint ask-meta">
                {howAnswered(f)} · <Link to={`/run/${workspace}/${f.run}`}>its log</Link>
              </div>
            </>
          )}
        </div>
      ))}
      {live && !known && answering(live)}
      {!live && (
        <div className="composer">
          <textarea
            ref={box}
            rows={2}
            placeholder="Why did it propose this? What did it read?"
            value={text}
            disabled={busy || !ask.may}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="cf">
            <span className="faint grow" style={{ fontSize: 12 }}>
              {!ask.may ? ask.why : ask.resume ? "Goes on in the same session while it is warm. A paid, read-only answer, up to $0.50." : `Starts afresh (${ask.why}). A paid, read-only answer, up to $0.50.`}
            </span>
            <Button size="sm" kind="primary" icon="send" disabled={busy || !ask.may || !text.trim()} onClick={send}>
              {busy ? "Asking…" : "Ask"}
            </Button>
          </div>
        </div>
      )}
      {error && <div className="rl-bad" style={{ fontSize: 12.5 }}>{error}</div>}
    </div>
  );
}

/** `whole`: the run on a page of its own, with a header; an ended run opens at its start. */
export function RunLog({ cwd, run, live, whole = false, onEnd }: { cwd: string; run: string; live: boolean; whole?: boolean; onEnd?: () => void }) {
  const [page, setPage] = useState<EventsPage | null>(null);
  const [events, setEvents] = useState<StepEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [following, setFollowing] = useState(live);

  useEffect(() => {
    api
      .get("/api/runs/{run}", { cwd, run, limit: PAGE })
      .then((p) => {
        setPage(p);
        setEvents((now) => merged(p.events, now));
        setFollowing(p.status === "running");
      })
      .catch(setError);
  }, [cwd, run]);

  const last = useRef(0);
  last.current = events.length ? events[events.length - 1].seq : 0;
  const [round, setRound] = useState(0);
  const ready = page !== null;
  useEffect(() => {
    if (!following || !ready) return;
    const query = new URLSearchParams({ cwd, after: String(last.current) });
    const source = new EventSource(`/api/runs/${encodeURIComponent(run)}/follow?${query}`);
    source.onmessage = (m) => setEvents((now) => merged(now, JSON.parse(m.data) as StepEvent[]));
    // The server ends each stream after a while, or cuts a reader that fell behind: follow again
    // from what has arrived. `done` and `status` mean the run is no longer running here.
    const again = () => {
      source.close();
      setRound((r) => r + 1);
    };
    // Ended, stopped or gone: read its head again, so how it ended shows without a reload.
    const over = () => {
      source.close();
      setFollowing(false);
      api
        .get("/api/runs/{run}", { cwd, run, limit: PAGE })
        .then((p) => {
          setPage(p);
          setEvents((now) => merged(now, p.events));
        })
        .catch(setError);
      onEnd?.();
    };
    source.addEventListener("end", again);
    source.addEventListener("cut", again);
    source.addEventListener("done", over);
    source.addEventListener("status", over);
    return () => source.close();
  }, [following, ready, round, cwd, run]);

  // Opens at the end, where a reader looks first; while following, stays there unless scrolled up.
  const box = useRef<HTMLDivElement>(null);
  const atStart = whole && page?.status !== "running";
  const pinned = useRef(!atStart);
  useEffect(() => {
    pinned.current = !atStart;
  }, [atStart]);
  useEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, [ready, events.length > 0]);

  const older = async () => {
    try {
      const p = await api.get("/api/runs/{run}", { cwd, run, limit: PAGE, before: String(events[0]?.seq ?? "") });
      setEvents((now) => merged(p.events, now));
      setPage((was) => (was ? { ...was, has_older: p.has_older } : p));
    } catch (e) {
      setError(e as Error);
    }
  };

  if (error) return <ErrorState error={error} />;
  if (!page) return <SkeletonRows rows={3} />;
  const unit = page.unit;
  if (page.status === "purged") return <div className="faint rl-note">The events of this run were cleared on {page.purged_at?.slice(0, 10)}.</div>;
  if (!events.length) return <div className="faint rl-note">{page.status === "none" ? "This run recorded nothing: the app went down before its first event." : "No events yet."}</div>;
  const toEnd = () => box.current?.scrollTo({ top: box.current.scrollHeight });
  return (
    <>
      {whole && (
        <div className="row" style={{ gap: 18, flexWrap: "wrap", marginBottom: 6 }}>
          {runFacts(events, page).map((f) => (
            <div key={f.label}>
              <div className="faint" style={{ fontSize: 11.5 }}>{f.label}</div>
              <b>{f.value}</b>
            </div>
          ))}
        </div>
      )}
    <div className="rl" ref={box} style={whole ? { maxHeight: "70vh" } : undefined}>
      {page.has_older && (
        <Button size="sm" kind="ghost" onClick={older}>
          Earlier events
        </Button>
      )}
      {events.map((e) => (
        <Line key={e.seq} event={e} unit={unit} whole={whole} />
      ))}
      {following && <div className="faint rl-note">Following…</div>}
      {page.events_lost > 0 && <div className="faint rl-note">{page.events_lost} events were not recorded.</div>}
    </div>
      {atStart && (
        <div className="faint rl-note">
          Shown from the start. <button className="link-btn" onClick={toEnd}>Jump to the end</button>
        </div>
      )}
    </>
  );
}

/** A tool call: its first line, and when more follow, a "+N lines" button that shows the whole of it. */
function ToolUse({ event: e, unit, who }: { event: StepEvent; unit: string; who: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { first, more, full } = toolLines(e.input);
  return (
    <div className="rl-l mono">
      {who}
      <span className="rl-tool">{toolName(e.name ?? "")}</span>{" "}
      {open ? null : inUnit(first, unit)}
      {more > 0 && (
        <>
          {" "}
          <button className="link-btn rl-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "show less" : `+${more} line${more > 1 ? "s" : ""}`}
          </button>
        </>
      )}
      {open && <pre className="rl-full">{inUnit(full, unit)}</pre>}
    </div>
  );
}

function Line({ event: e, unit, whole }: { event: StepEvent; unit: string; whole: boolean }) {
  // A helper's call reads as the helper's.
  const who = e.agent_id ? <span className="faint">helper · </span> : null;
  switch (e.kind) {
    case "config":
      return (
        <>
          <div className="rl-l faint">opened with {[e.model, e.effort].filter(Boolean).join(" · ") || "the defaults"}</div>
          <div className="rl-l faint">granted: {e.granted?.length ? e.granted.join(" · ") : "nothing beyond reading"}</div>
        </>
      );
    case "text":
      return e.role === "user" ? null : (
        <div className="rl-l rl-say">
          {who}
          <Markdown text={inUnit(e.text ?? "", unit)} />
        </div>
      );
    case "tool_use":
      return <ToolUse event={e} unit={unit} who={who} />;
    case "tool_result":
      return e.is_error ? <div className="rl-l mono rl-bad">failed: {inUnit(firstLine(e.content), unit)}</div> : null;
    case "denied":
      return (
        <div className="rl-l mono rl-bad">
          refused {e.tool}
          {e.lacked ? (e.lacked === "never granted" ? " (never granted)" : ` (no ${e.lacked} grant)`) : ""}: {e.reason}
        </div>
      );
    case "result":
      return (
        <div className="rl-l faint">
          {[e.num_turns != null ? `${e.num_turns} turns` : "", e.cost_usd != null ? money(e.cost_usd) : "", e.terminal_reason].filter(Boolean).join(" · ")}
        </div>
      );
    case "end":
      return (
        <div className={`rl-l ${e.outcome === "done" ? "faint" : e.outcome === "paused-budget" ? "rl-warn" : "rl-bad"}`}>
          ended: {e.outcome === "paused-budget" ? "paused at its ceiling" : e.outcome}
          {e.detail && !whole ? ` · ${failureWords(e.detail).plain}` : ""}
        </div>
      );
    default:
      return null;
  }
}
