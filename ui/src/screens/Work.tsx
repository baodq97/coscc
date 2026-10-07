// All work across projects as one list, grouped by where each unit stands. A project's own page
// adds what belongs to the project: pulling its code, what its features add, and adding or
// removing one.

import { useState } from "react";
import { api } from "../lib/api";
import { allUnits, useBoards, workspacesChanged } from "../lib/boards";
import { FeatureSlots } from "../lib/feature";
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
        <Empty icon="board" title="No work yet" actions={<Button kind="primary" icon="plus" onClick={() => navigate("/new")}>New work</Button>}>
          Hand over the first piece of work (key <kbd>C</kbd>).
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
                    {u.paused ? <Chip square tone="amber">{unitState(u).label}</Chip> : <span>{unitState(u).label}</span>}
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

/** What a person does to the project itself: pull its code, stop listing it, and what its features add. */
function ProjectBar({ workspace }: { workspace: Workspace }) {
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
              workspacesChanged();
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
      <FeatureSlots at="project" workspace={workspace} />
      {said.length > 0 && <pre className="mono faint" style={{ fontSize: 12, marginTop: 8, whiteSpace: "pre-wrap" }}>{said.join("")}</pre>}
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
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
      workspacesChanged();
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
