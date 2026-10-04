// All work across projects as one list, grouped by where each unit stands.

import { allUnits, useBoards } from "../lib/boards";
import { unitCode, unitTitle } from "../lib/format";
import { GROUP_ORDER, unitState } from "../lib/model";
import { Link } from "../lib/router";
import { Chip, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

const FOLDED = new Set(["Shipped", "Dropped"]);

export function Work({ workspace }: { workspace?: string }) {
  const { boards, loading } = useBoards();
  const shown = workspace ? boards.filter((b) => b.workspace.name === workspace) : boards;
  const units = allUnits(shown);
  const failed = shown.find((b) => b.error);

  return (
    <div className="page wide">
      <div style={{ padding: "24px 20px 12px" }}>
        <PageHead title={workspace ?? "All work"} lede="Every unit, from idea to ship. Shipped and dropped units are folded at the bottom." />
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
