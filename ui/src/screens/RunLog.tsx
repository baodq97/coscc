// What one run did, call by call: the agent's words, each tool it used and on what, what was
// refused or failed, and how it ended. Any agent's run, with a unit or none (an estimate, a scan,
// a chat turn). A running run is followed live; an ended one is read once.

import { useEffect, useRef, useState } from "react";
import type { EventsPage, StepEvent } from "../api.gen";
import { api, useResource } from "../lib/api";
import { AGENT_LABEL, money, unitCode, unitTitle } from "../lib/format";
import { Link } from "../lib/router";
import { Button, Chip, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const PAGE = "200";

/** The one line a tool call reads as: its command, file, pattern or description. */
export function toolSummary(input: unknown): string {
  if (!input || typeof input !== "object") return String(input ?? "");
  const i = input as Record<string, unknown>;
  for (const key of ["command", "file_path", "path", "pattern", "url", "query", "description", "prompt"]) {
    if (typeof i[key] === "string" && i[key]) return (i[key] as string).split("\n")[0];
  }
  return JSON.stringify(input).slice(0, 160);
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

function firstLine(content: unknown): string {
  const text = typeof content === "string" ? content : Array.isArray(content) ? content.map((c) => (c && typeof c === "object" && "text" in c ? String(c.text) : "")).join(" ") : "";
  return text.trim().split("\n")[0].slice(0, 200);
}

/** One run on a page of its own, any agent's: what ran, for which unit if any, and what it did. */
export function RunPage({ workspace, run }: { workspace: string; run: string }) {
  const list = useResource("/api/workspaces");
  const cwd = list.data?.workspaces.find((w) => w.name === workspace)?.path ?? "";
  const head = useResource(cwd ? "/api/runs/{run}" : null, { cwd, run, limit: "1" });
  if (list.state === "error") return <ErrorState error={list.error} onRetry={list.reload} />;
  if (head.state === "error") return <ErrorState error={head.error} onRetry={head.reload} />;
  const page = head.data;
  const number = page?.unit ? Number(page.unit.slice(0, 4)) : 0;
  return (
    <div className="page mid">
      <PageHead
        title={page ? `${AGENT_LABEL[page.stage] ?? page.stage} run` : "Run"}
        lede={
          page ? (
            <>
              {page.unit ? (
                <Link to={`/unit/${workspace}/${number}`}>
                  {unitCode(workspace, number)} {unitTitle(page.unit)}
                </Link>
              ) : (
                "No unit: a run of the workspace."
              )}{" "}
              {page.status === "running" ? <Chip tone="accent">running</Chip> : null}
            </>
          ) : undefined
        }
      />
      <div style={{ marginTop: 16 }}>{cwd ? <RunLog cwd={cwd} run={run} live={false} /> : <SkeletonRows rows={3} />}</div>
    </div>
  );
}

export function RunLog({ cwd, run, live }: { cwd: string; run: string; live: boolean }) {
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
    const over = () => {
      source.close();
      setFollowing(false);
    };
    source.addEventListener("end", again);
    source.addEventListener("cut", again);
    source.addEventListener("done", over);
    source.addEventListener("status", over);
    return () => source.close();
  }, [following, ready, round, cwd, run]);

  // Opens at the end, where a reader looks first; while following, stays there unless scrolled up.
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
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
  return (
    <div className="rl" ref={box}>
      {page.has_older && (
        <Button size="sm" kind="ghost" onClick={older}>
          Earlier events
        </Button>
      )}
      {events.map((e) => (
        <Line key={e.seq} event={e} unit={unit} />
      ))}
      {following && <div className="faint rl-note">Following…</div>}
      {page.events_lost > 0 && <div className="faint rl-note">{page.events_lost} events were not recorded.</div>}
    </div>
  );
}

function Line({ event: e, unit }: { event: StepEvent; unit: string }) {
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
          {inUnit(e.text ?? "", unit)}
        </div>
      );
    case "tool_use":
      return (
        <div className="rl-l mono">
          {who}
          <span className="rl-tool">{e.name?.replace(/^mcp__\w+?__/, "")}</span> {inUnit(toolSummary(e.input), unit)}
        </div>
      );
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
          {e.detail ? ` · ${e.detail}` : ""}
        </div>
      );
    default:
      return null;
  }
}
