// A toast for each notice the app sends, bottom right on every screen. One stream, read for as
// long as the studio is open; the cursor in `localStorage` keeps a reload from showing old
// notices again, and moves only forward.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useFollow } from "@studio/lib/api";
import { ago } from "@studio/lib/format";
import { Button } from "@studio/components/ui";
import type { FeatureUI } from "@studio/lib/feature";

type Notice = { id: number; text: string; at: string };

const KEY = "coscc_notice_after";
const SHOWN = 5;

function cursor(): number | null {
  const v = localStorage.getItem(KEY);
  return v === null ? null : Number(v);
}

function advance(id: number) {
  const now = cursor();
  if (now === null || id > now) localStorage.setItem(KEY, String(id));
}

function Notices() {
  const [list, setList] = useState<Notice[]>([]);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  useFollow(
    "/api/notices/follow",
    (m) => {
      if (m.type === "head") localStorage.setItem(KEY, String(m.id));
      else if (m.type === "notice") {
        const n = m as unknown as Notice;
        setList((l) => (l.some((x) => x.id === n.id) ? l : [n, ...l]));
        advance(n.id);
      }
    },
    cursor,
  );
  if (!list.length) return null;
  const hidden = list.length - SHOWN;
  return createPortal(
    <div className="toasts" role="region" aria-label="Notices" aria-live="polite">
      {list.slice(0, SHOWN).map((n) => (
        <div className="toast" key={n.id}>
          <div className="grow">
            {n.text}
            <div className="prov" style={{ display: "flex" }}>{ago(n.at)}</div>
          </div>
          <Button size="sm" kind="ghost" icon="x" title="Dismiss" onClick={() => setList((l) => l.filter((x) => x.id !== n.id))} />
        </div>
      ))}
      {hidden > 0 && (
        <div className="faint" style={{ fontSize: 12, textAlign: "right" }}>
          {hidden} more {hidden === 1 ? "notice" : "notices"}
        </div>
      )}
    </div>,
    document.body,
  );
}

export const ui: FeatureUI = { topbar: Notices };
