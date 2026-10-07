// The frame every screen sits in: sidebar, top bar, the screen, Leif's panel, the command bar,
// and the keys that work everywhere (⌘K, /, C, L, G then a letter).

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { navigate, usePath } from "../lib/router";
import { SCREENS } from "../routes";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { LeifPanel } from "./LeifPanel";
import { CommandBar } from "./CommandBar";
import { PackProvider } from "../lib/pack";

type ShellState = {
  navOpen: boolean;
  toggleNav: () => void;
  leifOpen: boolean;
  toggleLeif: () => void;
  openPalette: () => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
};

const Ctx = createContext<ShellState | null>(null);

export function useShell(): ShellState {
  const s = useContext(Ctx);
  if (!s) throw new Error("useShell outside the shell");
  return s;
}

const PHONE = "(max-width: 700px)";

/** What a key does outside a field: `g` arms a jump, the next letter goes to the screen it names. */
export function shortcut(key: string, afterG: boolean): { go?: string; act?: "palette" | "leif" | "help" | "arm" } {
  if (afterG) return { go: SCREENS.find((x) => x.keys === `G ${key.toUpperCase()}`)?.path };
  if (key === "/") return { act: "palette" };
  if (key === "c") return { go: "/new" };
  if (key === "l") return { act: "leif" };
  if (key === "g") return { act: "arm" };
  if (key === "?") return { act: "help" };
  return {};
}

function stored<T extends string>(key: string, fallback: T): T | string {
  return localStorage.getItem(key) || fallback;
}

export function Shell({ title, crumbs, children }: { title: string; crumbs: string[]; children: ReactNode }) {
  const [leifOpen, setLeifOpen] = useState(() => stored("cos-leif", "0") === "1" && !matchMedia(PHONE).matches);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  // On a phone the sidebar is a drawer: shut until the menu button opens it, shut again on a move.
  const [navOpen, setNavOpen] = useState(false);
  const path = usePath();
  useEffect(() => {
    setNavOpen(false);
    // On a phone the panel covers the page: a move shuts it.
    if (matchMedia(PHONE).matches) setLeifOpen(false);
  }, [path]);
  const [theme, setTheme] = useState<"light" | "dark">(() => (stored("cos-theme", "light") === "dark" ? "dark" : "light"));
  const pendingG = useRef(0);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("cos-theme", theme);
  }, [theme]);
  useEffect(() => {
    localStorage.setItem("cos-leif", leifOpen ? "1" : "0");
  }, [leifOpen]);
  useEffect(() => {
    document.title = `${title} · cos studio`;
  }, [title]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
        return;
      }
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") setHelp(false);
      if (palette || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      const afterG = Date.now() - pendingG.current < 900;
      pendingG.current = 0;
      const { go, act } = shortcut(e.key, afterG);
      if (go) navigate(go);
      else if (act === "palette") {
        e.preventDefault();
        setPalette(true);
      } else if (act === "leif") setLeifOpen((o) => !o);
      else if (act === "help") setHelp((h) => !h);
      else if (act === "arm") pendingG.current = Date.now();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [palette]);

  const state: ShellState = {
    navOpen,
    toggleNav: () => setNavOpen((o) => !o),
    leifOpen,
    toggleLeif: () => setLeifOpen((o) => !o),
    openPalette: () => setPalette(true),
    theme,
    toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };

  return (
    <Ctx.Provider value={state}>
      <div className={`app${navOpen ? " nav-open" : ""}`} id="studio-shell">
        <Sidebar />
        <div className="side-scrim" onClick={() => setNavOpen(false)} />
        <main className="main">
          <Topbar crumbs={crumbs} />
          <div className="scroll">
            <PackProvider>{children}</PackProvider>
          </div>
        </main>
        <LeifPanel />
      </div>
      {help && <Shortcuts onClose={() => setHelp(false)} />}
      {palette && <CommandBar onClose={() => setPalette(false)} />}
    </Ctx.Provider>
  );
}

function Shortcuts({ onClose }: { onClose: () => void }) {
  const rows = [["⌘K or /", "Ask Leif, or jump anywhere"], ["C", "New work"], ["L", "Show or hide the Leif panel"], ...SCREENS.filter((s) => s.keys?.startsWith("G ")).map((s) => [s.keys as string, s.title])];
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pal" role="dialog" aria-label="Keyboard shortcuts" style={{ padding: 16 }}>
        <b>Keyboard shortcuts</b>
        <div className="col" style={{ gap: 6, marginTop: 10 }}>
          {rows.map(([k, what]) => (
            <div key={k} className="row gap8">
              <span style={{ width: 90 }}>{k}</span>
              <span className="muted">{what}</span>
            </div>
          ))}
          <span className="faint" style={{ fontSize: 12 }}>? shows this list, Esc closes it. G then a letter goes to a screen.</span>
        </div>
      </div>
    </div>
  );
}
