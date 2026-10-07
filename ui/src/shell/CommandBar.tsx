// ⌘K: ask Leif, hand text over as new work, or jump to any screen. Asking Leif and handing
// over need Leif's backend; until then they open their screens.

import { useEffect, useMemo, useRef, useState } from "react";
import { Icon, LeifAvatar, LeifMark } from "../lib/icons";
import { navigate } from "../lib/router";
import { SCREENS } from "../routes";
import { Kbd } from "../components/ui";
import { useShell } from "./Shell";

type Item = { group: string; label: React.ReactNode; right?: React.ReactNode; icon: React.ReactNode; go: () => void };

export function CommandBar({ onClose }: { onClose: () => void }) {
  const shell = useShell();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);

  const items = useMemo<Item[]>(() => {
    const text = q.trim();
    const out: Item[] = [];
    if (text) {
      out.push({ group: "Leif", icon: <LeifAvatar />, label: <>Ask Leif: <b>{text}</b></>, right: <Kbd>↵</Kbd>, go: () => navigate("/leif") });
      out.push({ group: "Leif", icon: <Icon name="edit" />, label: <>Hand over as new work: <span className="muted">“{text}”</span></>, right: <Kbd>⇥</Kbd>, go: () => navigate("/new") });
    }
    const l = text.toLowerCase();
    for (const s of SCREENS) {
      if (s.path.includes(":") || (l && !s.title.toLowerCase().includes(l))) continue;
      out.push({
        group: "Go to",
        icon: s.icon ? <Icon name={s.icon} /> : <Icon name="home" />,
        label: s.title,
        right: s.keys ? s.keys.split(" ").map((k) => <Kbd key={k}>{k}</Kbd>) : undefined,
        go: () => navigate(s.path),
      });
    }
    if (!l || "switch theme dark light".includes(l)) out.push({ group: "Actions", icon: <Icon name="moon" />, label: "Switch theme", go: shell.toggleTheme });
    return out;
  }, [q, shell.toggleTheme]);

  const choose = (it?: Item) => {
    if (!it) return;
    onClose();
    it.go();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") setSel((i) => Math.min(i + 1, items.length - 1));
    else if (e.key === "ArrowUp") setSel((i) => Math.max(i - 1, 0));
    else if (e.key === "Enter") choose(items[sel]);
    else if (e.key === "Tab" && q.trim()) choose(items[1]);
    else if (e.key === "Escape") onClose();
    else return;
    e.preventDefault();
  };

  let lastGroup = "";
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pal" role="dialog" aria-label="Command bar">
        <div className="pal-in">
          <LeifAvatar />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
            }}
            onKeyDown={onKey}
            placeholder="Ask Leif, hand over work, or jump anywhere…"
            autoComplete="off"
          />
          <Kbd>esc</Kbd>
        </div>
        <div className="pal-list">
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? <div className="pal-g">{it.group}</div> : null;
            lastGroup = it.group;
            return (
              <div key={i}>
                {head}
                <div className={`pal-i ${i === sel ? "on" : ""}`} onMouseEnter={() => setSel(i)} onClick={() => choose(it)}>
                  {it.icon}
                  <span className="ellipsis">{it.label}</span>
                  <span className="r">{it.right}</span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="pal-f">
          <span>
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> move
          </span>
          <span>
            <Kbd>↵</Kbd> choose
          </span>
          <span>
            <Kbd>⇥</Kbd> hand over as work
          </span>
          <span style={{ marginLeft: "auto" }}>
            <LeifMark size={10} /> Leif acts only after you confirm
          </span>
        </div>
      </div>
    </div>
  );
}
