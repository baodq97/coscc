// Every workspace's board, read together: the studio looks across projects by default.

import { useEffect, useState } from "react";
import { api, useResource } from "./api";
import type { Board, Unit, Workspace } from "./model";

export type WorkspaceBoard = { workspace: Workspace; board?: Board; error?: Error };

export function useBoards(every = 30_000): { boards: WorkspaceBoard[]; loading: boolean } {
  const ws = useResource<{ workspaces: Workspace[] }>("/api/workspaces");
  const [boards, setBoards] = useState<WorkspaceBoard[]>([]);
  const [tick, setTick] = useState(0);
  const list = ws.data?.workspaces.filter((w) => !w.missing) ?? [];
  const key = list.map((w) => w.path).join("|");

  useEffect(() => {
    if (!list.length) return;
    let live = true;
    Promise.all(
      list.map((w) =>
        api
          .get<Board>("/api/board", { cwd: w.path })
          .then((board): WorkspaceBoard => ({ workspace: w, board }))
          .catch((error: Error): WorkspaceBoard => ({ workspace: w, error })),
      ),
    ).then((got) => live && setBoards(got));
    return () => {
      live = false;
    };
    // `key` stands for the list of workspaces.
  }, [key, tick]);

  useEffect(() => {
    const id = setInterval(() => document.visibilityState === "visible" && setTick((t) => t + 1), every);
    return () => clearInterval(id);
  }, [every]);

  return { boards, loading: ws.state === "loading" || (list.length > 0 && boards.length === 0) };
}

export type PlacedUnit = Unit & { workspace: Workspace };

export function allUnits(boards: WorkspaceBoard[]): PlacedUnit[] {
  return boards.flatMap((b) => (b.board?.units ?? []).map((u) => ({ ...u, workspace: b.workspace })));
}
