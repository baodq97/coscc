// Every workspace's board, read together: the studio looks across projects by default.

import { useEffect, useState } from "react";
import { api, useResource } from "./api";
import { useChanges } from "./stream";
import type { Cards } from "../api.gen";
import { unitState, type Unit, type Workspace } from "./model";

export type WorkspaceBoard = { workspace: Workspace; board?: Cards; error?: Error };

/** Tell every board in view that the list of projects changed (one added or unlisted). */
export function workspacesChanged(): void {
  dispatchEvent(new Event("cos-workspaces"));
}

// Facts that change no unit; a board read is the dearest read there is.
const NOT_BOARD = ["agent-run.", "chat-turn."];

// The stream carries what the app does; a pull request merged or CI finished on GitHub reaches
// the board only through a slow refresh. A reader starts from the last boards any reader got (the
// sidebar's, read before the page opened) and reads again, rather than showing nothing until then.
let last: { key: string; boards: WorkspaceBoard[] } = { key: "", boards: [] };

export function useBoards(every = 120_000): { boards: WorkspaceBoard[]; loading: boolean } {
  const ws = useResource("/api/workspaces");
  const [mine, setBoards] = useState<{ key: string; boards: WorkspaceBoard[] }>(last);
  const [tick, setTick] = useState(0);
  const list = ws.data ? ws.data.workspaces.filter((w) => !w.missing) : last.boards.map((b) => b.workspace);
  const key = list.map((w) => w.path).join("|");
  const boards = mine.key === key ? mine.boards : last.key === key ? last.boards : [];

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
    ).then((got) => {
      last = { key, boards: got };
      if (live) setBoards(last);
    });
    return () => {
      live = false;
    };
    // `key` stands for the list of workspaces.
  }, [key, tick]);

  useChanges([""], () => setTick((t) => t + 1), "", 400, NOT_BOARD);
  useEffect(() => {
    const on = () => ws.reload();
    addEventListener("cos-workspaces", on);
    return () => removeEventListener("cos-workspaces", on);
  }, [ws.reload]);

  useEffect(() => {
    const id = setInterval(() => document.visibilityState === "visible" && setTick((t) => t + 1), every);
    return () => clearInterval(id);
  }, [every]);

  return { boards, loading: boards.length === 0 && (ws.state === "loading" || list.length > 0) };
}

/** What agents are doing across every project: the runs in flight and the proposals waiting for a decision. */
export function useLive() {
  const got = useResource("/api/agents/live", {}, { on: ["agent-run."], every: 60_000, wait: 0 });
  return {
    running: got.data?.running ?? [],
    proposals: got.data?.proposals ?? [],
    failed: got.data?.failed ?? [],
    loading: !got.data && got.state !== "error",
    // Failed with nothing to show: a screen says so, never "0".
    error: got.state === "error" && !got.data ? got.error : undefined,
    reload: got.reload,
  };
}

/** What waits on the owner, defined once for the Briefing and Needs you: the units the board marks "Needs you" and the pending proposals. */
export function needsYou<P, F>(units: PlacedUnit[], proposals: P[], failed: F[] = []) {
  const mine = units.filter((u) => unitState(u).group === "Needs you");
  return { units: mine, proposals, failed, total: mine.length + proposals.length + failed.length };
}

/** Where a failed agent run opens: its run page, or the agent's own page when no log was kept. */
export const failedLink = (f: { workspace: string; agent: string; run: string }) =>
  f.run ? `/run/${encodeURIComponent(f.workspace)}/${f.run}` : `/agents/${f.agent}?ws=${encodeURIComponent(f.workspace)}`;

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
