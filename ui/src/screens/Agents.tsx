// The team: one card an agent, with what it does and how it has done for 30 days.

import { useResource } from "../lib/api";
import { AgentAvatar } from "../lib/icons";
import { STAGE_LABEL, modelName, money } from "../lib/format";
import { ErrorState, PageHead, SkeletonRows } from "../components/ui";

export function Agents() {
  const ws = useResource("/api/workspaces");
  const first = ws.data?.workspaces[0];
  const agents = useResource(first ? "/api/agents" : null, first ? { cwd: first.path } : {});

  return (
    <div className="page" style={{ maxWidth: 1160 }}>
      <PageHead title="Agents" lede="Your team. Leif runs it: one agent a stage, in the order a unit moves." />
      {agents.state === "error" ? (
        <ErrorState error={agents.error} onRetry={agents.reload} />
      ) : agents.state === "loading" ? (
        <SkeletonRows rows={4} />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginTop: 20 }}>
          {agents.data.rows.map((a) => (
            <div key={a.key} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              <div className="row">
                <AgentAvatar stage={a.key} size="lg" title={a.name} />
                <div className="grow">
                  <b>{a.name}</b>
                  <div className="faint" style={{ fontSize: 12 }}>
                    {STAGE_LABEL[a.key] ?? a.key} · {modelName(a.config.model)} · {a.config.effort}
                  </div>
                </div>
              </div>
              <div className="muted" style={{ minHeight: 38 }}>
                {a.role || a.meaning}
              </div>
              <div className="row" style={{ borderTop: "1px solid var(--line)", paddingTop: 8, fontSize: 12 }}>
                <span className="grow">
                  <b>{a.runs_30d}</b> <span className="faint">runs</span>
                </span>
                <span className="grow">
                  <b>{a.runs_30d ? money(a.cost_30d / a.runs_30d) : "—"}</b> <span className="faint">a run</span>
                </span>
                <span>
                  <b>{money(a.cost_30d, 0)}</b> <span className="faint">30 d</span>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
