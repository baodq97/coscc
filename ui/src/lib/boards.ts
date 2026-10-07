// Every workspace's board, read together: the studio looks across projects by default.

import { useEffect, useState } from "react";
import { api, useResource } from "./api";
import { useChanges } from "./stream";
import type { Cards } from "../api.gen";
import type { Unit, Workspace } from "./model";

export type WorkspaceBoard = { workspace: Workspace; board?: Cards; error?: Error };

/** Tell every board in view that the list of projects changed (one added or unlisted). */
export function workspacesChanged(): void {
  dispatchEvent(new Event("cos-workspaces"));
}

// The stream carries what the app does; a pull request merged or CI finished on GitHub reaches
// the board only through a slow refresh.
export function useBoards(every = 120_000): { boards: WorkspaceBoard[]; loading: boolean } {
  const ws = useResource("/api/workspaces");
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
          .get("/api/units", { cwd: w.path })
          .then((board): WorkspaceBoard => ({ workspace: w, board }))
          .catch((error: Error): WorkspaceBoard => ({ workspace: w, error })),
      ),
    ).then((got) => live && setBoards(got));
    return () => {
      live = false;
    };
    // `key` stands for the list of workspaces.
  }, [key, tick]);

  useChanges([""], () => setTick((t) => t + 1));
  useEffect(() => {
    const on = () => ws.reload();
    addEventListener("cos-workspaces", on);
    return () => removeEventListener("cos-workspaces", on);
  }, [ws.reload]);

  useEffect(() => {
    const id = setInterval(() => document.visibilityState === "visible" && setTick((t) => t + 1), every);
    return () => clearInterval(id);
  }, [every]);

  return { boards, loading: ws.state === "loading" || (list.length > 0 && boards.length === 0) };
}

/** What agents are doing across every project: the runs in flight and the proposals waiting for a decision. */
export function useLive() {
  const got = useResource("/api/agents/live", {}, { on: ["agent-run."], every: 60_000 });
  return { running: got.data?.running ?? [], proposals: got.data?.proposals ?? [], loading: !got.data && got.state !== "error" };
}

/** Where a proposal is decided: Up next, on its project, scrolled to it. */
export const proposalLink = (p: { workspace: string; id: number }) => `/up-next?ws=${encodeURIComponent(p.workspace)}#proposal-${p.id}`;

export type PlacedUnit = Unit & { workspace: Workspace };

export function allUnits(boards: WorkspaceBoard[]): PlacedUnit[] {
  return boards.flatMap((b) => (b.board?.units ?? []).map((u) => ({ ...u, workspace: b.workspace })));
}

/** The unit `/unit/<workspace>/<n>` names: `n` is its number (`162`, `0162`) or its full name. */
export function findUnit(units: PlacedUnit[], workspace: string, n: string): PlacedUnit | undefined {
  return units.find((u) => u.workspace.name === workspace && (u.name === n || (/^\d+$/.test(n) && u.number === Number(n))));
}
