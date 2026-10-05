import { Fragment } from "react";
import { FeatureSlots } from "../lib/feature";
import { LeifAvatar } from "../lib/icons";
import { Kbd } from "../components/ui";
import { useShell } from "./Shell";

export function Topbar({ crumbs }: { crumbs: string[] }) {
  const shell = useShell();
  return (
    <div className="top">
      <div className="crumbs">
        {crumbs.map((c, i) =>
          i === crumbs.length - 1 ? (
            <span className="cur ellipsis" key={i}>
              {c}
            </span>
          ) : (
            <Fragment key={i}>
              <span>{c}</span>
              <span className="sep">/</span>
            </Fragment>
          ),
        )}
      </div>
      <div className="top-r">
        <FeatureSlots at="topbar" />
        <button className={`btn ghost sm ${shell.leifOpen ? "on" : ""}`} onClick={shell.toggleLeif} title="Leif panel (L)">
          <LeifAvatar />
          <span>Leif</span>
          <Kbd>L</Kbd>
        </button>
      </div>
    </div>
  );
}
