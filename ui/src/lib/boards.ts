// Every workspace's board, read together: the studio looks across projects by default.

import { useEffect, useState } from "react";
import { api, useResource } from "./api";
import { onChange, useChanges } from "./stream";
import type { Cards } from "../api.gen";
import { unitState, type Unit, type Workspace } from "./model";

export type WorkspaceBoard = { workspace: Workspace; board?: Cards; error?: Error };

/** Tell every board in view that the list of projects changed (one added or unlisted). */
export function workspacesChanged(): void {
  dispatchEvent(new Event("cos-workspaces"));
}

// Facts that change no unit; a board read is the dearest read there is.
const NOT_BOARD = ["agent-run.", "chat-turn."];
// What the 400 ms wait leaves out: `board.read` says a board is ready, so it is read at once instead.
export const NOT_WAITED = [...NOT_BOARD, "board.read"];

// The board reads in flight by workspace: every part of the page asking for one while it is read shares
// the answer, so a burst of events makes one read of each board, not one per reader.
const reading = new Map<string, Promise<WorkspaceBoard>>();

export function readBoard(w: Workspace): Promise<WorkspaceBoard> {
  const going = reading.get(w.path);
  if (going) return going;
  const read = api
    .get("/api/units", { cwd: w.path })
    .then((board): WorkspaceBoard => ({ workspace: w, board }))
    .catch((error: Error): WorkspaceBoard => ({ workspace: w, error }))
    .finally(() => reading.delete(w.path));
  reading.set(w.path, read);
  return read;
}

/** Reads the board of the workspace each `board.read` names, with no wait, and hands it to `got`. */
export function onBoardRead(list: Workspace[], got: (board: WorkspaceBoard) => void): () => void {
  return onChange((c) => {
    if (c.subject !== "board.read" || !("workspace" in c)) return;
    // The event names the resolved path: a listed path the app resolves to another (a `~`, a link)
    // matches none, so every board is read, each a held answer.
    const named = list.filter((x) => x.path === c.workspace);
    for (const w of named.length ? named : list) readBoard(w).then(got);
  });
}

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
    Promise.all(list.map(readBoard)).then((got) => {
      last = { key, boards: got };
      if (live) setBoards(last);
    });
    return () => {
      live = false;
    };
    // `key` stands for the list of workspaces.
  }, [key, tick]);

  useEffect(() => {
    let live = true;
    const stop = onBoardRead(list, (b) => {
      if (!live || last.key !== key) return;
      last = { key, boards: last.boards.map((x) => (x.workspace.path === b.workspace.path ? b : x)) };
      setBoards(last);
    });
    return () => {
      live = false;
      stop();
    };
    // `key` stands for the list of workspaces.
  }, [key]);

  useChanges([""], () => setTick((t) => t + 1), "", 400, NOT_WAITED);
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
