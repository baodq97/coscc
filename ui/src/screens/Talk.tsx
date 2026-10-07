// Talking with Leif. For now Leif here is one Claude session in the project picked, which reads
// its code and units; what Leif remembers and recommends comes with Leif's own backend. Past
// conversations are listed beside it; one begun in a terminal is read only.

import { Fragment, useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatSession, LeifRun } from "../api.gen";
import { api, useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { ago, money, toolName } from "../lib/format";
import { Icon, LeifAvatar } from "../lib/icons";
import { Link, setQuery, useQuery } from "../lib/router";
import { Button, Dot, Markdown, SkeletonRows } from "../components/ui";

type Shown = { role: string; text: string; tools?: string[] };

/** The side list's rows: a conversation just begun is not in the server's list yet, so it stays on top
 *  (the open one, or while its first message is sent a temporary row saying that message). */
export function talkRows(listed: ChatSession[], session: ChatSession | null, sending: string | null): ChatSession[] {
  if (session?.resumable && !listed.some((s) => s.session_id === session.session_id)) return [session, ...listed];
  if (!session && sending) return [{ session_id: "", summary: sending, last_modified: Date.now(), created_at: null, git_branch: null, resumable: true }, ...listed];
  return listed;
}

/** The empty-list hint shows only when nothing is listed, open or being sent. */
export const showHint = (rows: ChatSession[], session: ChatSession | null, busy: boolean) => !rows.length && !session && !busy;

export function Talk() {
  const { boards, loading } = useBoards();
  // The address holds the project and the open conversation, so a reload reopens both.
  const project = useQuery("project");
  const wanted = useQuery("session");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  const cwd = workspace?.path ?? "";
  const sessions = useResource(cwd ? "/api/chat/sessions" : null, { cwd }, { on: ["chat-turn."] });
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<Shown[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // The first message of a conversation while its turn runs, before the stream names the session.
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const [elsewhere, setElsewhere] = useState(false);
  // The runs Leif started from this conversation, read again as any agent run starts or ends.
  const told = useResource(session?.resumable ? "/api/chat/history" : null, { cwd, session_id: session?.session_id ?? "" }, { on: ["agent-run.", "chat-turn."] });
  const runs: LeifRun[] = told.data?.runs ?? [];
  // The app's own chats first; sessions begun in a terminal or by an agent's run are read only.
  const listed = sessions.data?.sessions.filter((s) => s.resumable) ?? [];
  // A conversation just begun is not in the server's list yet: keep it on top so the list does not jump.
  const mine = talkRows(listed, session, sending);
  const others = sessions.data?.sessions.filter((s) => !s.resumable) ?? [];

  useEffect(() => {
    // Braces: `scrollIntoView` returns a Promise in newer browsers, and React would call it as the cleanup.
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, runs.map((r) => r.run + r.outcome).join()]);

  const open = async (s: ChatSession | null) => {
    setQuery("session", s?.session_id ?? "");
    setSession(s);
    setError(null);
    setMessages([]);
    if (!s) return;
    try {
      const h = await api.get("/api/chat/history", { cwd, session_id: s.session_id });
      setMessages(h.messages.filter((m: ChatMessage) => m.text.trim()).map((m) => ({ role: m.role, text: m.text })));
    } catch (e) {
      setError(e as Error);
    }
  };

  useEffect(() => {
    if (!wanted || session?.session_id === wanted || !sessions.data) return;
    const known = sessions.data.sessions.find((s) => s.session_id === wanted);
    open(known ?? { session_id: wanted, summary: "Conversation", last_modified: Date.now(), created_at: null, git_branch: null, resumable: true });
  }, [wanted, sessions.data]);

  const send = async () => {
    const said = text.trim();
    if (!said || busy) return;
    setText("");
    setBusy(true);
    setSending(session ? null : said);
    setError(null);
    setMessages((m) => [...m, { role: "user", text: said }, { role: "assistant", text: "" }]);
    const add = (f: (last: Shown) => Shown) => setMessages((m) => [...m.slice(0, -1), f(m[m.length - 1])]);
    try {
      let id = "";
      await api.stream("/api/chat", { cwd, text: said, session_id: session?.resumable ? session.session_id : undefined }, (l) => {
        if (l.type === "chunk") add((last) => ({ ...last, text: last.text + String(l.text) }));
        else if (l.type === "tool") add((last) => ({ ...last, tools: [...(last.tools ?? []), String(l.name)] }));
        else if (l.type === "done") id = String(l.session_id ?? "");
      });
      if (id && id !== session?.session_id) {
        setQuery("session", id);
        setSession({ session_id: id, summary: said, last_modified: Date.now(), created_at: null, git_branch: null, resumable: true });
      }
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
      setSending(null);
    }
  };

  if (loading) return <div className="page"><SkeletonRows rows={4} /></div>;
  // Where each run's line goes: before the person's next message after the one that started it
  // (found by its words, the last such), else at the end.
  const before = new Map<number, LeifRun[]>();
  const last: LeifRun[] = [];
  for (const r of runs) {
    const said = r.said.trim();
    const at = said ? messages.map((m, i) => (m.role === "user" && m.text.trim().startsWith(said) ? i : -1)).filter((i) => i >= 0).pop() : undefined;
    const next = at === undefined ? -1 : messages.findIndex((m, i) => i > at && m.role === "user");
    if (next < 0) last.push(r);
    else before.set(next, [...(before.get(next) ?? []), r]);
  }
  const readOnly = session !== null && !session.resumable;
  return (
    <div className="split">
      <div className="body" style={{ display: "flex", flexDirection: "column", padding: 0 }}>
        <div className="convo grow" style={{ width: "100%" }}>
          {!messages.length && (
            <div className="leif-say">
              <LeifAvatar size="lg" />
              <div className="bubble">
                <div className="leif-name">Leif</div>
                <div className="muted">
                  Ask about {workspace?.name ?? "a project"}: its code, a unit, why something stopped. I answer in one turn from what is in the project.
                  What I remember across conversations, and what I recommend from it, comes later.
                </div>
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <Fragment key={i}>
            {/* The runs started in the turn before this message, under that turn's answer. */}
            {(before.get(i) ?? []).map((r) => <RunLine key={r.run} r={r} workspace={workspace?.name ?? ""} />)}
            <div className={`msg ${m.role === "user" ? "me" : ""}`}>
              {m.role === "user" ? <span className="av">B</span> : <LeifAvatar />}
              <div className="mb">
                <div className="who">{m.role === "user" ? "You" : "Leif"}</div>
                {m.tools && m.tools.length > 0 && <div className="faint" style={{ fontSize: 12 }}>used {[...new Set(m.tools.map(toolName))].join(", ")}</div>}
                <div className="tx" style={m.role === "user" ? { whiteSpace: "pre-wrap" } : undefined}>
                  {(m.text && (m.role === "user" ? m.text : <Markdown text={m.text} />)) ||
                    (busy && i === messages.length - 1 ? (
                      <span className="typing">
                        <i />
                        <i />
                        <i />
                      </span>
                    ) : (
                      ""
                    ))}
                </div>
              </div>
            </div>
            </Fragment>
          ))}
          {last.map((r) => <RunLine key={r.run} r={r} workspace={workspace?.name ?? ""} />)}
          {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error.message}</div>}
          <div ref={end} />
        </div>
        <div style={{ maxWidth: 760, width: "100%", margin: "0 auto", padding: "0 32px 24px" }}>
          {readOnly ? (
            <div className="faint" style={{ fontSize: 12.5 }}>
              This conversation began in a terminal: the app reads it but does not write to it. <button className="link-btn" onClick={() => open(null)}>Start a new one</button>.
            </div>
          ) : (
            <div className="composer">
              <textarea
                rows={2}
                placeholder={`Ask Leif about ${workspace?.name ?? "a project"}…`}
                value={text}
                disabled={busy}
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
                  Each message is a paid turn. Enter sends, Shift+Enter is a new line.
                </span>
                <Button size="sm" kind="primary" icon="send" disabled={busy || !text.trim()} onClick={send}>
                  Send
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
      <aside className="props" style={{ width: 280 }}>
        <div className="seg" style={{ marginBottom: 12, maxWidth: "100%", flexWrap: "wrap" }}>
          {boards.map((b) => (
            <button
              key={b.workspace.path}
              className={workspace?.path === b.workspace.path ? "on" : ""}
              onClick={() => {
                setQuery("project", b.workspace.name);
                open(null);
              }}
            >
              {b.workspace.name}
            </button>
          ))}
        </div>
        <Button size="sm" icon="plus" disabled={busy} onClick={() => open(null)}>
          New conversation
        </Button>
        <div className="sec-h" style={{ marginTop: 16 }}>
          Earlier
        </div>
        {sessions.state === "loading" ? (
          <SkeletonRows rows={4} />
        ) : (
          <>
            <SessionList sessions={mine} current={session} busy={busy} onOpen={open} />
            {showHint(mine, session, busy) && <div className="faint" style={{ fontSize: 12.5 }}>Your conversations appear here once you send one.</div>}
            {others.length > 0 && (
              <button className="link-btn faint" style={{ fontSize: 12, marginTop: 12 }} onClick={() => setElsewhere(!(elsewhere || readOnly))}>
                {elsewhere || readOnly ? "Hide" : "Show"} {others.length} begun elsewhere (read only)
              </button>
            )}
            {(elsewhere || readOnly) && <SessionList sessions={others} current={session} busy={busy} onOpen={open} />}
          </>
        )}
      </aside>
    </div>
  );
}

function SessionList({ sessions, current, busy, onOpen }: { sessions: ChatSession[]; current: ChatSession | null; busy: boolean; onOpen: (s: ChatSession) => void }) {
  return (
    <div className="col" style={{ gap: 2, marginTop: 4 }}>
      {sessions.map((s) => (
        <button key={s.session_id} className={`talk-row ${current?.session_id === s.session_id ? "on" : ""}`} disabled={busy} onClick={() => onOpen(s)}>
          <span className="ellipsis">{s.summary}</span>
          <span className="faint" style={{ fontSize: 11.5 }}>
            {!s.resumable && <Icon name="lock" size={10} />} {ago(new Date(s.last_modified).toISOString())}
          </span>
        </button>
      ))}
    </div>
  );
}

/** One run Leif started, named as Leif names it, where the turn that started it ended. */
function RunLine({ r, workspace }: { r: LeifRun; workspace: string }) {
  const who = r.name === r.agent ? r.name : `${r.name} (${r.agent})`;
  const running = r.outcome === "running";
  const said = running ? "is running" : r.outcome === "done" ? "finished" : r.outcome;
  return (
    <div className={`callout ${running || r.outcome === "done" ? "accent" : "amber"}`} style={{ alignItems: "center", margin: "4px 0 8px" }}>
      {running ? <Dot tone="live" /> : <Icon name="bolt" size={15} />}
      <div className="grow">
        <b>
          {who} {said}
        </b>
        <div className="muted" style={{ fontSize: 12.5 }}>
          {running ? "Started by Leif from this conversation." : `${r.proposals} proposal${r.proposals === 1 ? "" : "s"} · ${money(r.cost_usd)} · started by Leif`}
        </div>
      </div>
      <Link to={`/run/${workspace}/${r.run}`}>{running ? "Watch it ▸" : "Open the run ▸"}</Link>
    </div>
  );
}
