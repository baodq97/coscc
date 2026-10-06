// The team: one card an agent, with what it does and how it has done for 30 days; one page an
// agent, where what it runs on is changed. What it may do (its grant) is shown, never written.

import { useState, type ReactNode } from "react";
import type { AgentRow, ConfigRow } from "../api.gen";
import { api, useResource } from "../lib/api";
import { AgentAvatar } from "../lib/icons";
import { STAGE_LABEL, ago, modelName, money, unitCode, unitTitle } from "../lib/format";
import { Link } from "../lib/router";
import { Button, Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

// The models an owner picks from, as the defaults name them (`[1m]`: the long context). A row on
// another model shows its id beside them.
const MODELS = ["claude-opus-5-5[1m]", "claude-sonnet-5-5[1m]"];
const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

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
            <Link key={a.key} to={`/agents/${a.key}`} className="card agent-card">
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
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentPage({ name }: { name: string }) {
  const ws = useResource("/api/workspaces");
  const first = ws.data?.workspaces[0];
  const agents = useResource(first ? "/api/agents" : null, first ? { cwd: first.path } : {});
  const a = agents.data?.rows.find((r) => r.key === name);
  if (agents.state === "error") return <ErrorState error={agents.error} onRetry={agents.reload} />;
  if (!agents.data) return <div className="page"><SkeletonRows rows={5} /></div>;
  if (!a)
    return (
      <div className="page">
        <Empty icon="team" title="No such agent">
          The team has no {name}.
        </Empty>
      </div>
    );
  return (
    <div className="page mid">
      <div className="row" style={{ gap: 12 }}>
        <AgentAvatar stage={a.key} size="xl" title={a.name} />
        <div className="grow">
          <h1 className="title">{a.name}</h1>
          <div className="faint">
            {STAGE_LABEL[a.key] ?? a.key} · {a.meaning}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <b>{money(a.cost_30d, 0)}</b> <span className="faint">in 30 days</span>
          <div className="faint" style={{ fontSize: 12 }}>
            {a.runs_30d} runs{a.runs_30d ? ` · ${money(a.cost_30d / a.runs_30d)} a run` : ""}
          </div>
        </div>
      </div>
      {a.role && <p className="muted" style={{ marginTop: 14 }}>{a.role}</p>}

      <div className="sec-h">How it runs</div>
      <Settings row={a.config} cos={agents.data.cos_model} onSaved={agents.reload} />
      {a.variants.map((v) => (
        <div key={v.key}>
          <div className="sec-h">
            On new ground <span className="faint">{v.key}</span>
          </div>
          <Settings row={v} cos={agents.data.cos_model} onSaved={agents.reload} />
        </div>
      ))}

      <div className="sec-h">Recent runs</div>
      <Runs agent={a} names={Object.fromEntries((ws.data?.workspaces ?? []).map((w) => [w.path, w.name]))} />

      <div className="sec-h">What it may do</div>
      <Grant agent={a} />
    </div>
  );
}

/** One row's model, effort and two ceilings: each with where it comes from, saved on its own. */
function Settings({ row, cos, onSaved }: { row: ConfigRow; cos: string | null; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const set = async (field: string, value: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/agents/field", { key: row.key, field, value });
      onSaved();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const c = row.ceilings;
  const has = (f: string) => row.fields.includes(f);
  const mine = (f: string) => row.overridden[f];
  const reset = (f: string) =>
    mine(f) && (
      <Button size="sm" kind="ghost" disabled={busy} onClick={() => set(f, null)}>
        Back to default
      </Button>
    );
  return (
    <div className="card card-b">
      {has("model") && (
        <Field label="Model" hint={source(row.model_source, cos ? `COS_MODEL is ${modelName(cos)}` : "")}>
          <div className="seg">
            {MODELS.map((m) => (
              <button key={m} className={row.model && modelName(row.model) === modelName(m) ? "on" : ""} disabled={busy} onClick={() => set("model", m)}>
                {modelName(m)}
              </button>
            ))}
          </div>
          {row.model && !MODELS.some((m) => modelName(m) === modelName(row.model)) && <Chip square tone="plain">{row.model}</Chip>}
          {reset("model")}
        </Field>
      )}
      {has("effort") && (
        <Field label="Effort" hint={source(row.effort_source)}>
          <div className="seg">
            {EFFORTS.map((e) => (
              <button key={e} className={row.effort === e ? "on" : ""} disabled={busy} onClick={() => set("effort", e)}>
                {e}
              </button>
            ))}
          </div>
          {reset("effort")}
        </Field>
      )}
      {has("turns") && (
        <Field label="Turns at most" hint={source(c.max_turns_source)}>
          <NumberInput value={c.max_turns} disabled={busy} onSave={(v) => set("turns", v)} />
          {reset("turns")}
        </Field>
      )}
      {has("budget") && (
        <Field label="Spend at most" hint={source(c.max_budget_source)}>
          <span className="faint">$</span>
          <NumberInput value={c.max_budget_usd} disabled={busy} onSave={(v) => set("budget", v)} />
          {reset("budget")}
        </Field>
      )}
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
    </div>
  );
}

/** Where a value comes from, in words: an override is yours, anything else the app's. */
function source(from: string, extra = ""): string {
  const said = from === "override" ? "Yours: changed on this page." : from === "none" ? "No ceiling." : from === "default" ? "The app's default." : `From ${from}.`;
  return [said, extra].filter(Boolean).join(" ");
}

function NumberInput({ value, disabled, onSave }: { value: number | null; disabled: boolean; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const changed = draft.trim() !== (value == null ? "" : String(value)) && draft.trim() !== "";
  return (
    <>
      <input className="input sm" inputMode="decimal" style={{ width: 90 }} value={draft} onChange={(e) => setDraft(e.target.value)} />
      {changed && (
        <Button size="sm" kind="primary" disabled={disabled} onClick={() => onSave(draft.trim())}>
          Save
        </Button>
      )}
    </>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <div>
        <div className="lab">{label}</div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {children}
      </div>
    </div>
  );
}

/** The last runs in every project; `names` turns a run's workspace path into its project. */
function Runs({ agent, names }: { agent: AgentRow; names: Record<string, string> }) {
  if (!agent.runs.length) return <div className="card card-b faint">No runs yet.</div>;
  return (
    <div className="card">
      {agent.runs.map((r) => (
        <Link key={r.workspace + r.unit + r.at} to={`/unit/${names[r.workspace] ?? ""}/${Number(r.unit.slice(0, 4))}`} className="lrow">
          <span className="id">{unitCode(names[r.workspace] ?? "", Number(r.unit.slice(0, 4)))}</span>
          <span className="t">{unitTitle(r.unit)}</span>
          <span className="meta">
            {r.outcome !== "done" && <Chip square tone="red">{r.outcome}</Chip>} {r.cost_usd != null ? money(r.cost_usd) : ""} · {ago(r.at)}
          </span>
        </Link>
      ))}
    </div>
  );
}

function Grant({ agent }: { agent: AgentRow }) {
  const g = agent.grant;
  const list = (title: string, items: string[]) =>
    items.length > 0 && (
      <Field label={title}>
        <span className="mono muted">{items.join(" · ")}</span>
      </Field>
    );
  return (
    <div className="card card-b">
      {list("Tools", g.tools)}
      {list("App tools", g.mcp)}
      <div className="faint" style={{ fontSize: 12.5, marginTop: 8 }}>
        {g.warning || "Set in the code, not here: widening what a step may do is a change reviewed like any other."}
      </div>
    </div>
  );
}
