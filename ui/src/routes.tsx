// Every screen of the studio: its path, its place in the sidebar, and what draws it.
// The command bar and the sidebar are both built from this one list.

import { agentFace } from "./lib/pack";
import type { ReactNode } from "react";
import type { IconName } from "./lib/icons";
import { Briefing } from "./screens/Briefing";
import { Work } from "./screens/Work";
import { UnitPage } from "./screens/UnitPage";
import { Inbox } from "./screens/Inbox";
import { Agents } from "./screens/Agents";
import { AgentPage } from "./screens/AgentPage";
import { System } from "./screens/System";
import { MayDo } from "./screens/MayDo";
import { NewWork } from "./screens/NewWork";
import { UpNext } from "./screens/UpNext";
import { Talk } from "./screens/Talk";
import { Insights } from "./screens/Insights";
import { RunPage } from "./screens/RunLog";
import { Feature } from "./screens/Feature";
import { Decided } from "./screens/Decided";

export type Screen = {
  path: string;
  title: string;
  nav?: "Leif" | "Work" | "Team";
  icon?: IconName;
  keys?: string;
  crumbs?: (p: Record<string, string>) => string[];
  render: (p: Record<string, string>) => ReactNode;
};

export const SCREENS: Screen[] = [
  { path: "/", title: "Briefing", nav: "Leif", keys: "G H", render: () => <Briefing /> },
  {
    path: "/inbox",
    title: "Needs you",
    nav: "Leif",
    icon: "inbox",
    keys: "G I",
    render: () => <Inbox />,
  },
  { path: "/inbox/:ws/:n", title: "Needs you", crumbs: (p) => ["Needs you", p.ws, p.n], render: (p) => <Inbox workspace={p.ws} number={p.n} /> },
  {
    path: "/leif",
    title: "Talk to Leif",
    nav: "Leif",
    icon: "chat",
    keys: "G L",
    render: () => <Talk />,
  },
  {
    path: "/decisions",
    title: "Leif decided",
    nav: "Leif",
    icon: "decided",
    keys: "G D",
    render: () => <Decided />,
  },
  { path: "/work", title: "All work", nav: "Work", icon: "board", keys: "G B", render: () => <Work /> },
  { path: "/up-next", title: "Up next", nav: "Work", icon: "arrow", keys: "G U", render: () => <UpNext /> },
  { path: "/work/:ws", title: "Work", crumbs: (p) => ["Work", p.ws], render: (p) => <Work workspace={p.ws} /> },
  { path: "/unit/:ws/:n", title: "Unit", crumbs: (p) => ["Work", p.ws, p.n], render: (p) => <UnitPage workspace={p.ws} number={p.n} /> },
  {
    path: "/new",
    title: "New work",
    keys: "C",
    render: () => <NewWork />,
  },
  { path: "/agents", title: "Agents", nav: "Team", icon: "team", keys: "G T", render: () => <Agents /> },
  { path: "/agents/:key", title: "Agent", crumbs: (p) => ["Agents", agentFace(p.key).name], render: (p) => <AgentPage name={p.key} /> },
  { path: "/agents/:key/:tab", title: "Agent", crumbs: (p) => ["Agents", agentFace(p.key).name], render: (p) => <AgentPage name={p.key} tab={p.tab} /> },
  {
    path: "/insights",
    title: "Insights",
    nav: "Team",
    icon: "chart",
    keys: "G S",
    render: () => <Insights />,
  },
  { path: "/run/:ws/:run", title: "Run", crumbs: () => ["Run"], render: (p) => <RunPage workspace={p.ws} run={p.run} /> },
  {
    path: "/may-do",
    title: "What Leif may do",
    nav: "Team",
    icon: "book",
    keys: "G K",
    render: () => <MayDo />,
  },
  { path: "/feature/:name", title: "Feature", crumbs: (p) => [p.name.charAt(0).toUpperCase() + p.name.slice(1)], render: (p) => <Feature name={p.name} /> },
  { path: "/system", title: "Design system", render: () => <System /> },
];
