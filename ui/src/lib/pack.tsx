// What the pack says about its states and agents, read once for the whole studio: a state's plain
// label, an agent's name and glyph. No screen names a state itself; it asks here.

import { createContext, useContext, type ReactNode } from "react";
import type { PackShown } from "../api.gen";
import { useResource } from "./api";
import { SkeletonRows } from "../components/ui";

export type AgentFace = { name: string; glyph: string };
type Index = { faces: Record<string, AgentFace>; labels: Record<string, string>; withAgent: string[]; packs: PackShown[]; reload: () => void };

const EMPTY: Index = { faces: {}, labels: {}, withAgent: [], packs: [], reload: () => {} };
let index: Index = EMPTY;
const Ctx = createContext<Index>(EMPTY);

const capital = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** A state or agent in plain words: the pack's label, else the agent's name, else the key capitalised. */
export function stageLabel(key: string): string {
  return index.labels[key] ?? index.faces[key]?.name ?? capital(key);
}

export function agentFace(key: string): AgentFace {
  return index.faces[key] ?? { name: capital(key), glyph: capital(key).slice(0, 1) };
}

/** Read the agents and packs again, after the page added or removed one. */
export const refreshPacks = () => index.reload();

export const useIndex = () => useContext(Ctx);

// Every screen waits on this one read, so it is the packs alone (a few KB, no run log): the agents'
// names ride with them. Never the Agents page's read, which counts every run.
export function PackProvider({ children }: { children: ReactNode }) {
  const ws = useResource("/api/workspaces");
  const cwd = ws.data?.workspaces[0]?.path;
  const packs = useResource(cwd ? "/api/packs" : null, cwd ? { cwd } : {});
  const settled = ws.state !== "loading" && (!cwd || packs.state !== "loading");
  if (!settled) return <div className="page"><SkeletonRows rows={4} /></div>;

  const faces = Object.fromEntries((packs.data ?? []).flatMap((p) => p.agents.map((a) => [a.key, { name: a.name, glyph: a.glyph }])));
  const states = (packs.data ?? []).flatMap((p) => p.processes.flatMap((pr) => Object.entries(pr.states)));
  const labels: Record<string, string> = {};
  const withAgent = new Set<string>();
  for (const [key, st] of states) {
    if (st.label) labels[key] = st.label;
    if (st.agent) withAgent.add(key);
  }
  index = { faces, labels, withAgent: [...withAgent], packs: packs.data ?? [], reload: packs.reload };
  return <Ctx.Provider value={index}>{children}</Ctx.Provider>;
}
