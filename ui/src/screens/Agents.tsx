// The team: every agent the app runs, one line each, grouped by what opens it. A line says when
// the agent runs, on what, what it cost in 30 days and whether anything needs a look; the agent's
// own page (`AgentPage.tsx`) changes any part of it.

import type { AgentRow } from "../api.gen";
import { useResource } from "../lib/api";
import { LeifAvatar, Rune } from "../lib/icons";
import { modelName, money } from "../lib/format";
import { Link } from "../lib/router";
import { Chip, ErrorState, PageHead, SkeletonRows } from "../components/ui";

export const GROUPS: { key: AgentRow["group"]; title: string; lede: string }[] = [
  { key: "stage", title: "Stage agents", lede: "Each opens when a unit reaches its state." },
  { key: "engine", title: "Engine agents", lede: "The app opens these itself, or on your press." },
  { key: "helper", title: "Helpers", lede: "Started by another agent inside its run." },
  { key: "triggered", title: "Periodic and on request", lede: "A schedule, an event, your press or Leif starts these; they only read." },
];

const ENGINE_WORDS: Record<string, string> = {
  integrate: "when a pull request conflicts or its CI goes red",
  estimate: "when you press Propose estimates",
  chat: "when you talk to Leif",
};

const EVENT_WORDS: Record<string, string> = { "unit.shipped": "a ship" };

/** `168` is "7 days", `24` "1 day", `6` "6 h". */
export function hoursWords(h: number): string {
  if (h % 24) return `${h} h`;
  return h === 24 ? "1 day" : `${h / 24} days`;
}

/** When an agent runs, in words: its trigger, or who starts a helper. */
export function triggerWords(a: AgentRow, rows: AgentRow[] = []): string {
  const t = a.row.trigger ?? {};
  if (t.state) return `on state ${t.state}`;
  if (t.engine) return ENGINE_WORDS[t.engine] ?? `by the engine (${t.engine})`;
  const said: string[] = [];
  if (t.schedule) said.push(`every ${t.schedule.hours} h`);
  if (t.event) {
    const what = EVENT_WORDS[t.event.name ?? ""] ?? t.event.name;
    said.push(t.event.after_hours ? `${hoursWords(t.event.after_hours)} after ${what}` : `on ${what}`);
  }
  if (t.manual || t.leif) said.push("on request");
  if (said.length) return said.join(", ");
  if (a.group === "helper") {
    const by = rows.filter((r) => (r.row.helpers ?? []).includes(a.key)).map((r) => r.row.name ?? r.key);
    return by.length ? `started by ${by.join(", ")}` : "started by no agent";
  }
  return "—";
}

/** What needs a look on a line, worst first: a problem stops its runs, then the last run's chip. */
export function attention(a: AgentRow): { tone: "red" | "amber"; label: string } | null {
  if (a.problems.length) return { tone: "red", label: "Cannot run" };
  if (a.chip === "failed") return { tone: "red", label: "Last run failed" };
  if (a.chip === "costly") return { tone: "amber", label: "Near its $ ceiling" };
  return null;
}

export function AgentGlyph({ a, size = "" }: { a: AgentRow; size?: "" | "lg" | "xl" }) {
  if (a.key === "leif") return <LeifAvatar size={size} />;
  return (
    <span className={`av ${size}`} title={a.row.name}>
      <Rune glyph={a.row.glyph || (a.row.name ?? a.key).slice(0, 1)} size={size === "xl" ? 20 : size === "lg" ? 16 : 12} />
    </span>
  );
}

/** The agents of the first workspace's view: one read gives every row and the catalog. */
export function useAgents() {
  const ws = useResource("/api/workspaces");
  const first = ws.data?.workspaces[0];
  const agents = useResource("/api/agents", first ? { cwd: first.path } : {});
  return { ws, cwd: first?.path ?? "", agents };
}

export function Agents() {
  const { agents } = useAgents();
  const rows = agents.data?.rows ?? [];
  const look = rows.filter((a) => attention(a));
  return (
    <div className="page" style={{ maxWidth: 1040 }}>
      <PageHead
        title="Agents"
        lede="Every agent the app runs: when it runs, on what model, what it may do and what it cost. Open one to change any part; its next run uses the change."
      />
      {agents.state === "error" ? (
        <ErrorState error={agents.error} onRetry={agents.reload} />
      ) : !agents.data ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          {look.length > 0 && (
            <div className="card card-b attn" style={{ marginTop: 16 }}>
              <b>Needs a look</b>
              {look.map((a) => (
                <Link key={a.key} to={`/agents/${a.key}`} className="attn-row">
                  <Chip square tone={attention(a)!.tone}>{attention(a)!.label}</Chip>
                  <span>{a.row.name ?? a.key}</span>
                  <span className="faint">{a.problems[0] ?? "open it to see its runs"}</span>
                </Link>
              ))}
            </div>
          )}
          {GROUPS.map((g) => {
            const mine = rows.filter((a) => a.group === g.key);
            if (!mine.length) return null;
            return (
              <section key={g.key}>
                <div className="sec-h">
                  {g.title} <span className="faint">{g.lede}</span>
                </div>
                <div className="card">
                  {mine.map((a) => (
                    <AgentLine key={a.key} a={a} rows={rows} />
                  ))}
                </div>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

function AgentLine({ a, rows }: { a: AgentRow; rows: AgentRow[] }) {
  const look = attention(a);
  return (
    <Link to={`/agents/${a.key}`} className="agent-line">
      <AgentGlyph a={a} />
      <span className="who">
        <b>{a.row.name ?? a.key}</b> <span className="faint mono">{a.key}</span>
        <span className="when">{triggerWords(a, rows)}</span>
      </span>
      <span className="what">
        {modelName(a.config.model)}
        {a.config.effort ? ` · ${a.config.effort}` : ""}
      </span>
      <span className="cost">
        {a.runs_30d ? (
          <>
            <b>{money(a.cost_30d)}</b> <span className="faint">· {a.runs_30d} run{a.runs_30d === 1 ? "" : "s"}</span>
          </>
        ) : (
          <span className="faint">no runs in 30 d</span>
        )}
      </span>
      <span className="marks">
        {look && <Chip square tone={look.tone}>{look.label}</Chip>}
        {a.edited.length > 0 && <Chip square tone="accent">edited</Chip>}
      </span>
    </Link>
  );
}
