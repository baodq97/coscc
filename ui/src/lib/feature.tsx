// What a feature adds to the studio. Each `coscc/features/<name>/ui/index.tsx` exports `ui`, a
// `FeatureUI`; the glob below finds them when the studio is built, so a feature with no `ui/`
// adds nothing. A slot is drawn only where the feature is not off for the workspace.

import type { ComponentType } from "react";
import { useResource } from "./api";
import type { IconName } from "./icons";
import type { Workspace } from "./model";

export type FeatureUI = {
  /** Beside the Leif button on every screen. No workspace: its routes know each one's state. */
  topbar?: ComponentType;
  /** On an open unit, under its next step. */
  unit?: ComponentType<{ workspace: Workspace; unit: string }>;
  /** In the workspace's Up next. */
  backlog?: ComponentType<{ workspace: Workspace }>;
  /** A sidebar entry in Team, and the screen at `/feature/<name>`, one workspace at a time. */
  page?: { label: string; lede: string; icon: IconName; Component: ComponentType<{ workspace: Workspace }> };
};

const found = import.meta.glob<{ ui: FeatureUI }>("../../../coscc/features/*/ui/index.tsx", { eager: true });

/** Each feature's UI by the name of its folder. */
export const FEATURE_UIS: Record<string, FeatureUI> = Object.fromEntries(
  Object.entries(found).map(([path, module]) => [path.split("/").at(-3) ?? path, module.ui]),
);

/** The slots of `at` that features with a UI have for this workspace (or none, for the top bar). */
export function FeatureSlots(props: { at: "topbar" } | { at: "backlog"; workspace: Workspace } | { at: "unit"; workspace: Workspace; unit: string }) {
  const cwd = props.at === "topbar" ? null : props.workspace.path;
  const shown = useResource(cwd === null ? null : "/api/features/shown", cwd === null ? {} : { cwd });
  const on = (name: string) => props.at === "topbar" || (shown.data ?? []).some((f) => f.name === name && f.state !== "off");
  return (
    <>
      {Object.entries(FEATURE_UIS).map(([name, ui]) => {
        if (!on(name)) return null;
        if (props.at === "topbar") return ui.topbar ? <ui.topbar key={name} /> : null;
        if (props.at === "backlog") return ui.backlog ? <ui.backlog key={name} workspace={props.workspace} /> : null;
        return ui.unit ? <ui.unit key={name} workspace={props.workspace} unit={props.unit} /> : null;
      })}
    </>
  );
}
