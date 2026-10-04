// Grouped by who it is about: Leif first, then the work, then the team. The foot holds what
// is always worth a glance: today's spend against the cap and the autopilot.

import { Fragment } from "react";
import { useResource } from "../lib/api";
import { Icon, LeifMark } from "../lib/icons";
import { money } from "../lib/format";
import type { Board, Workspace } from "../lib/model";
import { Link, usePath } from "../lib/router";
import { SCREENS } from "../routes";
import { Kbd, Meter } from "../components/ui";
import { useShell } from "./Shell";

const COLORS = ["#5e6ad2", "#e07a2f", "#2a9461", "#c2417a", "#8a6d3b"];

export function Sidebar() {
  const path = usePath();
  const shell = useShell();
  const ws = useResource<{ workspaces: Workspace[] }>("/api/workspaces");
  const first = ws.data?.workspaces[0];
  const board = useResource<Board>(first ? "/api/board" : null, first ? { cwd: first.path } : {}, { on: ["step.", "integration.", "mode."], every: 120_000 });
  const cap = board.data?.autopilot?.cap;

  const item = (to: string, label: string, icon: React.ReactNode, extra?: React.ReactNode) => (
    <Link to={to} className={path === to ? "on" : ""}>
      {icon}
      <span>{label}</span>
      {extra}
    </Link>
  );

  return (
    <aside className="side">
      <div className="ws">
        <span className="ws-logo">c</span>
        <span className="ws-name">cos studio</span>
      </div>
      <div className="side-actions">
        <button className="side-search" onClick={shell.openPalette}>
          <Icon name="search" size={14} />
          <span>Ask Leif…</span>
          <span style={{ marginLeft: "auto" }} className="row gap4">
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </span>
        </button>
        <Link className="side-new" to="/new" title="New work (C)">
          <Icon name="edit" size={15} />
        </Link>
      </div>
      <nav className="nav">
        {(["Leif", "Work", "Team"] as const).map((group) => (
          <Fragment key={group}>
            {group !== "Leif" && <div className="nav-h">{group}</div>}
            {SCREENS.filter((s) => s.nav === group).map((s) =>
              s.path === "/" ? (
                <Fragment key={s.path}>
                  {item(
                    "/",
                    "Leif",
                    <span className="av leif" style={{ width: 16, height: 16, borderRadius: 4 }}>
                      <LeifMark size={9} />
                    </span>,
                    <span className="count">Briefing</span>,
                  )}
                </Fragment>
              ) : (
                <Fragment key={s.path}>{item(s.path, s.title, s.icon ? <Icon name={s.icon} /> : null)}</Fragment>
              ),
            )}
            {group === "Work" &&
              ws.data?.workspaces.map((w, i) => (
                <Fragment key={w.path}>{item(`/work/${w.name}`, w.name, <span className="pdot" style={{ background: COLORS[i % COLORS.length] }} />)}</Fragment>
              ))}
          </Fragment>
        ))}
      </nav>
      <div className="side-foot">
        <div className="spend">
          <div className="spend-top">
            <span>Today</span>
            <span>
              {cap ? (
                <>
                  <b>{money(cap.spent, 0)}</b> of {money(cap.limit, 0)}
                </>
              ) : (
                <span className="sk" style={{ width: 60, height: 9, display: "inline-block" }} />
              )}
            </span>
          </div>
          <Meter value={cap?.spent ?? 0} max={cap?.limit ?? 1} />
          <div className="ap">
            <span className={`dot ${board.data?.autopilot?.on ? "green" : "amber"}`} />
            {board.data ? `Autopilot ${board.data.autopilot?.on ? "on" : "off"} in ${first?.name}` : "Reading the autopilot…"}
          </div>
        </div>
        <div className="side-util">
          <span className="av owner" style={{ margin: "0 6px 0 4px" }}>
            B
          </span>
          <span className="faint grow" style={{ fontSize: 12 }}>
            Owner
          </span>
          <Link className="iconbtn" to="/system" title="Design system">
            <Icon name="palette" size={15} />
          </Link>
          <button className="iconbtn" onClick={shell.toggleTheme} title="Theme">
            <Icon name={shell.theme === "dark" ? "sun" : "moon"} size={15} />
          </button>
        </div>
      </div>
    </aside>
  );
}
