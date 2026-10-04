// Talking with Leif. For now Leif here is one Claude session in the project picked, which reads
// its code and units; what Leif remembers and recommends comes with Leif's own backend. Past
// conversations are listed beside it; one begun in a terminal is read only.

import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatSession } from "../api.gen";
import { ApiError, api, useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { ago } from "../lib/format";
import { Icon, LeifAvatar } from "../lib/icons";
import { Button, Empty, SkeletonRows } from "../components/ui";

type Shown = { role: string; text: string; tools?: string[] };

/** Reads an NDJSON reply line by line: `chunk` text, `tool` names, then `done` or `error`. */
export async function readReply(body: ReadableStream<Uint8Array>, on: (line: Record<string, unknown>) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const lines = (rest + decoder.decode(value, { stream: true })).split("\n");
    rest = lines.pop() ?? "";
    lines.filter(Boolean).forEach((l) => on(JSON.parse(l)));
  }
  if (rest.trim()) on(JSON.parse(rest));
}

export function Talk() {
  const { boards, loading } = useBoards();
  const [project, setProject] = useState("");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  const cwd = workspace?.path ?? "";
  const sessions = useResource(cwd ? "/api/chat/sessions" : null, { cwd }, { on: ["chat-turn."] });
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<Shown[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages]);

  const open = async (s: ChatSession | null) => {
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

  const send = async () => {
    const said = text.trim();
    if (!said || busy) return;
    setText("");
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", text: said }, { role: "assistant", text: "" }]);
    const add = (f: (last: Shown) => Shown) => setMessages((m) => [...m.slice(0, -1), f(m[m.length - 1])]);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd, text: said, session_id: session?.resumable ? session.session_id : undefined }),
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => null);
        throw new ApiError(res.status, (data && data.error) || res.statusText);
      }
      let id = "";
      await readReply(res.body, (l) => {
        if (l.type === "chunk") add((last) => ({ ...last, text: last.text + String(l.text) }));
        else if (l.type === "tool") add((last) => ({ ...last, tools: [...(last.tools ?? []), String(l.name)] }));
        else if (l.type === "error") throw new Error(String(l.error));
        else if (l.type === "done") id = String(l.session_id ?? "");
      });
      if (id && id !== session?.session_id) setSession({ session_id: id, summary: said, last_modified: Date.now(), created_at: null, git_branch: null, resumable: true });
      sessions.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="page"><SkeletonRows rows={4} /></div>;
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
            <div key={i} className={`msg ${m.role === "user" ? "me" : ""}`}>
              {m.role === "user" ? <span className="av">B</span> : <LeifAvatar />}
              <div className="mb">
                <div className="who">{m.role === "user" ? "You" : "Leif"}</div>
                {m.tools && m.tools.length > 0 && <div className="faint mono" style={{ fontSize: 12 }}>used {m.tools.join(", ")}</div>}
                <div className="tx" style={{ whiteSpace: "pre-wrap" }}>
                  {m.text ||
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
          ))}
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
        <div className="seg" style={{ marginBottom: 12 }}>
          {boards.map((b) => (
            <button
              key={b.workspace.path}
              className={workspace?.path === b.workspace.path ? "on" : ""}
              onClick={() => {
                setProject(b.workspace.name);
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
        ) : !sessions.data?.sessions.length ? (
          <Empty icon="chat" title="No conversations yet">
            They appear here once you send one.
          </Empty>
        ) : (
          <div className="col" style={{ gap: 2 }}>
            {sessions.data.sessions.map((s) => (
              <button key={s.session_id} className={`talk-row ${session?.session_id === s.session_id ? "on" : ""}`} disabled={busy} onClick={() => open(s)}>
                <span className="ellipsis">{s.summary}</span>
                <span className="faint" style={{ fontSize: 11.5 }}>
                  {!s.resumable && <Icon name="lock" size={10} />} {ago(new Date(s.last_modified).toISOString())}
                </span>
              </button>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}
