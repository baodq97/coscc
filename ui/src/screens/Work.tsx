// All work across projects as one list, grouped by where each unit stands. A project's own page
// adds what belongs to the project: its release, pulling its code, and adding or removing one.

import { useState } from "react";
import type { ReleaseView } from "../api.gen";
import { api, useResource } from "../lib/api";
import { allUnits, useBoards } from "../lib/boards";
import { unitCode, unitTitle } from "../lib/format";
import { GROUP_ORDER, unitState, type Workspace } from "../lib/model";
import { Link, navigate } from "../lib/router";
import { Button, Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const FOLDED = new Set(["Shipped", "Dropped"]);

export function Work({ workspace }: { workspace?: string }) {
  const { boards, loading } = useBoards();
  const shown = workspace ? boards.filter((b) => b.workspace.name === workspace) : boards;
  const units = allUnits(shown);
  const failed = shown.find((b) => b.error);

  return (
    <div className="page wide">
      <div style={{ padding: "24px 20px 12px" }}>
        <PageHead title={workspace ?? "All work"} lede={shown.length === 1 && workspace ? shown[0].workspace.label || "Every unit of this project, from idea to ship." : "Every unit, from idea to ship. Shipped and dropped units are folded at the bottom."} />
        {workspace && shown[0] && <ProjectBar workspace={shown[0].workspace} />}
        {!workspace && !loading && <AddProject />}
      </div>
      {failed?.error && <ErrorState error={failed.error} />}
      {loading ? (
        <SkeletonRows rows={10} />
      ) : units.length === 0 ? (
        <Empty icon="board" title="No work yet">
          Hand over the first piece of work with <kbd>C</kbd>.
        </Empty>
      ) : (
        GROUP_ORDER.map((group) => {
          const rows = units.filter((u) => unitState(u).group === group);
          if (!rows.length) return null;
          return (
            <details key={group} open={!FOLDED.has(group)}>
              <summary className="lgroup" style={{ cursor: "pointer", listStyle: "none" }}>
                {group} <span className="n">{rows.length}</span>
              </summary>
              {rows.map((u) => (
                <Link key={u.workspace.name + u.name} to={`/unit/${u.workspace.name}/${u.number}`} className="lrow">
                  <span className="id">{unitCode(u.workspace.name, u.number)}</span>
                  <span className="t">{unitTitle(u.name)}</span>
                  <span className="meta">
                    {u.type && <Chip square tone="plain">{u.type}</Chip>}
                    {!workspace && <span>{u.workspace.name}</span>}
                    <span>{unitState(u).label}</span>
                  </span>
                </Link>
              ))}
            </details>
          );
        })
      )}
    </div>
  );
}

/** What a person does to the project itself: pull its code, release it, stop listing it. */
function ProjectBar({ workspace }: { workspace: Workspace }) {
  const release = useResource("/api/release", { cwd: workspace.path }, { on: ["integration.ended", "step.ended"] });
  const [asking, setAsking] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const act = async (what: string, run: () => Promise<unknown>) => {
    if (asking !== what) return setAsking(what);
    setBusy(true);
    setError(null);
    setSaid([]);
    try {
      await run();
      setAsking("");
      release.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const name = encodeURIComponent(workspace.name);
  return (
    <div style={{ marginTop: 14 }}>
      <div className="row" style={{ gap: 6 }}>
        <Button size="sm" icon="refresh" disabled={busy} title="git pull --ff-only in the project's folder" onClick={() => act("pull", async () => setSaid([String((await api.post<{ output: string }>(`/api/workspaces/${name}/pull`, {})).output || "Up to date.")]))}>
          {asking === "pull" ? "Pull now? Refused while a step runs here" : "Pull from origin"}
        </Button>
        <Button
          size="sm"
          kind="ghost"
          icon="x"
          disabled={busy}
          onClick={() =>
            act("remove", async () => {
              await api.post(`/api/workspaces/${name}/remove`, {});
              navigate("/work");
            })
          }
        >
          {asking === "remove" ? "Stop listing it? Its folder and history stay" : "Stop listing"}
        </Button>
        {asking && !busy && (
          <Button size="sm" kind="ghost" onClick={() => setAsking("")}>
            Cancel
          </Button>
        )}
      </div>
      {release.data && <Release release={release.data} cwd={workspace.path} busy={busy} asking={asking} act={act} onLine={(l) => setSaid((s) => [...s, l])} />}
      {said.length > 0 && <pre className="mono faint" style={{ fontSize: 12, marginTop: 8, whiteSpace: "pre-wrap" }}>{said.join("")}</pre>}
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
    </div>
  );
}

/** A one-line summary of what a release would gather, by kind: `12 feat, 3 fix`. */
export function kinds(units: { type: string }[]): string {
  const n: Record<string, number> = {};
  units.forEach((u) => (n[u.type || "other"] = (n[u.type || "other"] ?? 0) + 1));
  return Object.entries(n)
    .sort((a, b) => b[1] - a[1])
    .map(([k, c]) => `${c} ${k}`)
    .join(", ");
}

function Release({ release: r, cwd, busy, asking, act, onLine }: { release: ReleaseView; cwd: string; busy: boolean; asking: string; act: (what: string, run: () => Promise<unknown>) => void; onLine: (line: string) => void }) {
  if (r.state === "nothing" || r.state === "unknown") return null;
  const press = () => act(r.button, () => api.stream(`/api/release/${r.button}`, { cwd, version: r.version }, (l) => l.type === "chunk" && onLine(String(l.text))));
  return (
    <div className="card card-b" style={{ marginTop: 12 }}>
      <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
        <b>Release {r.version || r.proposed}</b>
        <span className="faint">
          {r.state === "ready"
            ? `${r.count} units since ${r.last_tag}: ${kinds(r.units)}`
            : r.state === "pr-open"
              ? `pull request #${r.pr} open`
              : r.state === "merged-untagged"
                ? "merged, not tagged yet"
                : r.state}
        </span>
        <span className="grow" />
        {r.release_url && (
          <a href={r.release_url} target="_blank" rel="noreferrer" className="faint">
            On GitHub
          </a>
        )}
      </div>
      {r.checks.length > 0 && (
        <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          {r.checks.map((c) => (
            <Chip key={c.name} square tone={c.bucket === "pass" ? "green" : c.bucket === "fail" ? "red" : "amber"}>
              {c.name}: {c.bucket}
            </Chip>
          ))}
        </div>
      )}
      {r.button && (
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <Button size="sm" kind="primary" disabled={busy || !r.enabled} onClick={press}>
            {asking === r.button
              ? r.button === "prepare"
                ? `Open the pull request for ${r.version}?`
                : `Merge and tag ${r.version}? This publishes it`
              : r.button === "prepare"
                ? `Prepare ${r.version}`
                : `Publish ${r.version}`}
          </Button>
          <span className="faint" style={{ fontSize: 12 }}>{r.enabled ? r.consequence : r.disabled_reason}</span>
        </div>
      )}
    </div>
  );
}

/** Adopt a folder already under the working folder, or clone a repository into it. */
function AddProject() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  if (!open)
    return (
      <div style={{ marginTop: 12 }}>
        <Button size="sm" icon="plus" onClick={() => setOpen(true)}>
          Add a project
        </Button>
      </div>
    );
  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/workspaces", { name: name.trim(), repo_url: url.trim() || undefined, label: label.trim() });
      navigate(`/work/${encodeURIComponent(name.trim())}`);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card card-b" style={{ marginTop: 12, maxWidth: 640 }}>
      <div className="field">
        <div>
          <div className="lab">Folder name</div>
          <div className="hint">A folder under the working folder.</div>
        </div>
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <div>
          <div className="lab">Clone from</div>
          <div className="hint">Empty when the folder is already there.</div>
        </div>
        <input className="input" placeholder="https://github.com/…" value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <div className="field">
        <div className="lab">What it is</div>
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
      </div>
      {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error.message}</div>}
      <div className="row" style={{ gap: 6, marginTop: 10 }}>
        <span className="grow" />
        <Button size="sm" kind="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button size="sm" kind="primary" disabled={busy || !name.trim()} onClick={add}>
          {busy ? "Adding…" : url.trim() ? "Clone and add" : "Add"}
        </Button>
      </div>
    </div>
  );
}
