// Leif beside every screen. In the frame it only holds its place: the conversation and
// Leif's notes on the screen in view come with Leif's own backend.

import { Icon, LeifAvatar } from "../lib/icons";
import { Link } from "../lib/router";
import { useShell } from "./Shell";

export function LeifPanel() {
  const shell = useShell();
  return (
    <aside className="leifp" hidden={!shell.leifOpen}>
      <div className="leifp-h">
        <LeifAvatar />
        <b>Leif</b>
        <span className="faint" style={{ fontSize: 12 }}>
          Chief of staff
        </span>
        <span className="grow" />
        <Link className="iconbtn" to="/leif" title="Open the conversation">
          <Icon name="ext" size={14} />
        </Link>
        <button className="iconbtn" onClick={shell.toggleLeif} title="Close (L)">
          <Icon name="x" size={14} />
        </button>
      </div>
      <div className="leifp-b">
        <div className="leif-say">
          <LeifAvatar />
          <div className="bubble">
            <div className="leif-name">Leif</div>
            <div style={{ marginTop: 2 }}>I'll keep notes here on whatever you're looking at: what changed, what I decided, and what I'd do next.</div>
          </div>
        </div>
      </div>
      <div className="leifp-f">
        <div className="composer" style={{ boxShadow: "none", padding: "8px 8px 6px 12px" }}>
          <textarea rows={1} placeholder="Ask Leif…" disabled />
          <div className="cf">
            <span className="faint" style={{ fontSize: 11.5 }}>
              Leif acts only after you say so
            </span>
            <span className="grow" />
            <button className="btn primary sm" disabled>
              <Icon name="send" size={13} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
