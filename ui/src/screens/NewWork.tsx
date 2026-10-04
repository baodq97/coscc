// Hand over new work: the owner's own words become a unit's brief, which the intent step reads.
// The name is made from the words and can be changed; nothing runs until a step is started.

import { useState } from "react";
import { api } from "../lib/api";
import { useBoards } from "../lib/boards";
import { navigate } from "../lib/router";
import { Button, PageHead, SkeletonRows } from "../components/ui";

const SLUG_MAX = 60;

/** "Sửa lỗi: board chậm!" reads `sua-loi-board-cham`: lowercase ASCII words joined by hyphens. */
export function slugOf(words: string): string {
  const plain = words
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/gi, "d")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  const cut = plain.slice(0, SLUG_MAX);
  return cut.length < plain.length ? cut.replace(/-[^-]*$/, "") || cut : cut;
}

export function NewWork() {
  const { boards, loading } = useBoards();
  const [project, setProject] = useState("");
  const [brief, setBrief] = useState("");
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  const name = slug || slugOf(brief.split("\n")[0]);

  const create = async () => {
    if (!workspace) return;
    setBusy(true);
    setError(null);
    try {
      const made = await api.post<{ unit: string }>("/api/units", { cwd: workspace.path, slug: name, brief: brief.trim() });
      navigate(`/unit/${workspace.name}/${Number(made.unit.slice(0, 4))}`);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="page mid"><SkeletonRows rows={4} /></div>;
  return (
    <div className="page mid">
      <PageHead title="New work" lede="Say what is wrong or what you want, in your own words. It becomes the unit's brief; nothing runs until a step is started." />
      <div className="card card-b" style={{ marginTop: 20 }}>
        <div className="field">
          <div className="lab">Project</div>
          <div className="seg">
            {boards.map((b) => (
              <button key={b.workspace.path} className={workspace?.path === b.workspace.path ? "on" : ""} onClick={() => setProject(b.workspace.name)}>
                {b.workspace.name}
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <div>
            <div className="lab">What you want</div>
            <div className="hint">The problem, the evidence, what you said you want. No design: intent and spec work that out.</div>
          </div>
          <textarea className="ta" rows={7} autoFocus value={brief} onChange={(e) => setBrief(e.target.value)} />
        </div>
        <div className="field">
          <div>
            <div className="lab">Name</div>
            <div className="hint">From the first line; change it if it reads badly.</div>
          </div>
          <input className="input" value={name} onChange={(e) => setSlug(slugOf(e.target.value))} />
        </div>
        {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
        <div className="row" style={{ marginTop: 12 }}>
          <span className="grow" />
          <Button kind="primary" disabled={busy || !brief.trim() || !name || !workspace} onClick={create}>
            {busy ? "Handing over…" : `Hand over to ${workspace?.name ?? "…"}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
