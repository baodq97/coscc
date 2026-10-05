// A feature's own page (the vault's) in the studio's frame: its title, the project picker, and
// what the feature draws for that project. A feature off in that project says so.

import { useState } from "react";
import { useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { FEATURE_UIS } from "../lib/feature";
import { Link } from "../lib/router";
import { Empty, PageHead, SkeletonRows } from "../components/ui";

export function Feature({ name }: { name: string }) {
  const { boards, loading } = useBoards();
  const [project, setProject] = useState("");
  const workspace = boards.find((b) => b.workspace.name === project)?.workspace ?? boards[0]?.workspace;
  const shown = useResource(workspace ? "/api/features/shown" : null, workspace ? { cwd: workspace.path } : {});
  const page = FEATURE_UIS[name]?.page;

  if (!page)
    return (
      <div className="page">
        <Empty icon="search" title="No such page">
          No feature has a page called {name}.
        </Empty>
      </div>
    );
  if (loading || !workspace || !shown.data)
    return (
      <div className="page">
        <SkeletonRows rows={4} />
      </div>
    );
  const off = shown.data.find((f) => f.name === name)?.state === "off";
  return (
    <div className="page">
      <PageHead title={page.label} lede={page.lede} />
      <div className="seg" style={{ marginTop: 16 }}>
        {boards.map((b) => (
          <button key={b.workspace.path} className={workspace.path === b.workspace.path ? "on" : ""} onClick={() => setProject(b.workspace.name)}>
            {b.workspace.name}
          </button>
        ))}
      </div>
      {off ? (
        <Empty icon="lock" title={`${page.label} is off in ${workspace.name}`}>
          Turn it on in <Link to="/may-do">What Leif may do</Link>.
        </Empty>
      ) : (
        <page.Component key={workspace.path} workspace={workspace} />
      )}
    </div>
  );
}
