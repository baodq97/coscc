// A feature's own page (the vault's), framed: the feature serves it at its path for one project,
// and the studio puts the project picker around it. A feature off in that project says so.

import { useState } from "react";
import { useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { Link } from "../lib/router";
import { Empty, SkeletonRows } from "../components/ui";

export function Feature({ name }: { name: string }) {
  const { boards, loading } = useBoards();
  const pages = useResource("/api/features/pages");
  const [project, setProject] = useState("");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  const shown = useResource(workspace ? "/api/features/shown" : null, workspace ? { cwd: workspace.path } : {});
  const page = pages.data?.find((p) => p.name === name);
  const state = shown.data?.find((f) => f.name === name)?.state;

  if (loading || !pages.data) return <div className="page"><SkeletonRows rows={4} /></div>;
  if (!page)
    return (
      <div className="page">
        <Empty icon="search" title="No such page">
          No feature has a page called {name}.
        </Empty>
      </div>
    );
  return (
    <div className="page wide" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* The page has its own heading; the frame adds only the project it is for. */}
      <div className="row" style={{ padding: "16px 28px 0", gap: 12 }}>
        <span className="grow" />
        <div className="seg">
          {boards.map((b) => (
            <button key={b.workspace.path} className={workspace?.path === b.workspace.path ? "on" : ""} onClick={() => setProject(b.workspace.name)}>
              {b.workspace.name}
            </button>
          ))}
        </div>
      </div>
      {state === "off" ? (
        <Empty icon="lock" title={`${page.label} is off in ${workspace?.name}`}>
          Turn it on in <Link to="/may-do">What Leif may do</Link>.
        </Empty>
      ) : workspace ? (
        <iframe key={workspace.path} title={page.label} src={`${page.path}?cwd=${encodeURIComponent(workspace.path)}`} className="grow" style={{ border: 0, width: "100%", minHeight: 600, padding: "0 28px" }} />
      ) : null}
    </div>
  );
}
