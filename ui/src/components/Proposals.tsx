// Up next's Proposals: work the agents that propose found worth doing, each naming its agent, to
// accept as a unit or dismiss with a reason. Who proposes, and whether it runs on its own here,
// is one line above; its page runs it now or turns it on. Accepting one opens the unit it made.

import { useEffect, useState } from "react";
import type { ProposalRow } from "../api.gen";
import { api, useResource } from "../lib/api";
import { ago, unitCode } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Link, navigate } from "../lib/router";
import { Button, Chip, ErrorState } from "./ui";

const TONE = { pending: "amber", accepted: "green", dismissed: "plain" } as const;
const KIND: Record<string, string> = {
  refused: "Refused",
  "ci-red": "CI red",
  rerun: "Rerun",
  "review-round": "Review round",
  "impl-draft": "Impl draft",
  integrate: "Integrate",
};
const FILTERS = ["pending", "accepted", "dismissed", "all"] as const;
type Filter = (typeof FILTERS)[number];

export function Proposals({ workspace }: { workspace: Workspace }) {
  const cwd = workspace.path;
  const got = useResource("/api/proposals", { cwd }, { on: [""] });
  const linked = location.hash.match(/^#proposal-(\d+)$/);
  const [filter, setFilter] = useState<Filter>(linked ? "all" : "pending");
  const [open, setOpen] = useState<number | null>(linked ? Number(linked[1]) : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open !== null) document.getElementById(`proposal-${open}`)?.scrollIntoView({ block: "nearest" });
  }, [open, got.data]);

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await run();
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
    got.reload();
  };

  if (got.state === "error" && !got.data) return <ErrorState error={got.error} onRetry={got.reload} />;
  const v = got.data;
  if (!v || (!v.proposals.length && !v.agents.length)) return null;
  // An agent whose proposals are listed but which proposes only on your press (Dagaz's gaps).
  const others = [...new Map(v.proposals.filter((p) => !v.agents.some((a) => a.key === p.agent)).map((p) => [p.agent, p])).values()];
  const count = (f: Filter) => v.proposals.filter((p) => f === "all" || p.state === f).length;
  const shown = v.proposals.filter((p) => filter === "all" || p.state === filter);

  return (
    <section id="proposals">
      <div className="sec-h">
        Proposals <span className="faint">{count("pending") ? `${count("pending")} to decide` : ""}</span>
      </div>
      <div className="row muted" style={{ gap: 12, fontSize: 12.5, marginBottom: 10, flexWrap: "wrap" }}>
        {v.agents.map((a) => (
          <Link key={a.key} to={`/agents/${a.key}/trigger?ws=${encodeURIComponent(workspace.name)}`}>
            {a.name}: {a.on === null ? "on request" : a.on ? "runs on its own here" : "off here, runs when you press Run now"}
          </Link>
        ))}
        {others.map((p) => (
          <span key={p.agent}>{p.agent_name}: when you propose a capability its draft lacks</span>
        ))}
      </div>
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginBottom: 10 }}>{error}</div>}
      <div className="seg" role="group" aria-label="Filter proposals by state" style={{ marginBottom: 10 }}>
        {FILTERS.map((f) => (
          <button key={f} className={filter === f ? "on" : ""} aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f[0].toUpperCase() + f.slice(1)} {count(f)}
          </button>
        ))}
      </div>
      <div className="card">
        {shown.map((p) => (
          <Row key={p.id} p={p} open={open === p.id} onToggle={() => setOpen(open === p.id ? null : p.id)} busy={busy} act={act} workspace={workspace} chip={filter === "all"} />
        ))}
        {!shown.length && <div className="card-b faint">{v.proposals.length ? "No proposal in this state." : "No proposal yet: an agent that proposes adds them here."}</div>}
      </div>
    </section>
  );
}

/** The page of the unit a proposal made: `0165_slug` in `workspace`. */
export const madeAt = (workspace: string, made: string) => `/unit/${workspace}/${Number(made.slice(0, 4))}`;

function Row({ p, open, onToggle, busy, act, workspace, chip }: { p: ProposalRow; open: boolean; onToggle: () => void; busy: boolean; act: (run: () => Promise<unknown>) => void; workspace: Workspace; chip: boolean }) {
  const [slug, setSlug] = useState(p.slug);
  const [why, setWhy] = useState("");
  const cwd = workspace.path;
  const decide = (body: Record<string, string>) => act(() => api.post(`/api/proposals/${p.id}`, { cwd, ...body }));
  const accept = () =>
    act(async () => {
      const made = await api.post<ProposalRow>(`/api/proposals/${p.id}`, { cwd, action: "accept", slug });
      if (made.made) navigate(madeAt(workspace.name, made.made));
    });
  // A citation (a file, a query) has no unit and no time: those columns show only when a source has one.
  const withUnit = p.sources.some((s) => s.unit);
  const withWhen = p.sources.some((s) => s.at);
  return (
    <div id={`proposal-${p.id}`} style={{ borderBottom: "1px solid var(--line)" }}>
      <div className="lrow" style={{ cursor: "pointer", height: 44 }} onClick={onToggle} role="button" aria-expanded={open}>
        {chip && <Chip tone={TONE[p.state as keyof typeof TONE] ?? "plain"}>{p.state}</Chip>}
        <span className="t">{p.title}</span>
        <span className="meta">
          <span>{p.agent_name}</span>
          <span className="wide">{p.type}</span>
          <span>{ago(p.at)}</span>
        </span>
      </div>
      {open && (
        <div className="card-b" style={{ paddingTop: 0 }}>
          <p style={{ margin: "0 0 10px", whiteSpace: "pre-wrap" }}>{p.problem}</p>
          <table className="t">
            <thead>
              <tr>
                <th>Source</th>
                {withUnit && <th>Unit</th>}
                {withWhen && <th>When</th>}
              </tr>
            </thead>
            <tbody>
              {p.sources.map((s) => (
                <tr key={s.id}>
                  <td>{s.kind ? (KIND[s.kind] ?? s.kind) : s.id === p.run ? <Link to={`/run/${workspace.name}/${s.id}`}>The run that proposed it</Link> : <code className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{s.id}</code>}</td>
                  {withUnit && <td>{s.unit || "—"}</td>}
                  {withWhen && <td>{s.at ? ago(s.at) : "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {p.state === "pending" && (
            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              <div className="row">
                <input className="input" aria-label="Slug of the new unit" value={slug} onChange={(e) => setSlug(e.target.value)} />
                <Button kind="primary" disabled={busy || !slug.trim()} onClick={accept}>
                  Accept as a unit
                </Button>
              </div>
              <div className="row">
                <input className="input" aria-label="Why it is dismissed" placeholder="Why it is dismissed" value={why} onChange={(e) => setWhy(e.target.value)} />
                <Button disabled={busy || !why.trim()} onClick={() => decide({ action: "dismiss", reason: why })}>
                  Dismiss
                </Button>
              </div>
              {!why.trim() && <div className="faint" style={{ fontSize: 12 }}>Dismiss needs a reason.</div>}
            </div>
          )}
          {p.state === "accepted" && (
            <div className="muted" style={{ marginTop: 10 }}>
              Accepted {ago(p.decided)}: <Link to={madeAt(workspace.name, p.made)}>{unitCode(workspace.name, Number(p.made.slice(0, 4)))}</Link>
            </div>
          )}
          {p.state === "dismissed" && <div className="muted" style={{ marginTop: 10 }}>Dismissed {ago(p.decided)}: {p.reason}</div>}
        </div>
      )}
    </div>
  );
}
