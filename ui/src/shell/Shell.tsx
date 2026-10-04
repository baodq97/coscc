// The frame every screen sits in: sidebar, top bar, the screen, Leif's panel, the command bar,
// and the keys that work everywhere (⌘K, /, C, L, G then a letter).

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { navigate } from "../lib/router";
import { SCREENS } from "../routes";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { LeifPanel } from "./LeifPanel";
import { CommandBar } from "./CommandBar";

type ShellState = {
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

function stored<T extends string>(key: string, fallback: T): T | string {
  return localStorage.getItem(key) || fallback;
}

export function Shell({ title, crumbs, children }: { title: string; crumbs: string[]; children: ReactNode }) {
  const [leifOpen, setLeifOpen] = useState(() => stored("cos-leif", "0") === "1");
  const [palette, setPalette] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() => (stored("cos-theme", "light") === "dark" ? "dark" : "light"));
  const pendingG = useRef(0);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("cos-theme", theme);
  }, [theme]);
  useEffect(() => localStorage.setItem("cos-leif", leifOpen ? "1" : "0"), [leifOpen]);
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
      if (palette || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      if (e.key === "/") {
        e.preventDefault();
        setPalette(true);
      } else if (e.key === "c") navigate("/new");
      else if (e.key === "l") setLeifOpen((o) => !o);
      else if (e.key === "g") pendingG.current = Date.now();
      else if (Date.now() - pendingG.current < 900) {
        const s = SCREENS.find((x) => x.keys === `G ${e.key.toUpperCase()}`);
        if (s) navigate(s.path);
        pendingG.current = 0;
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [palette]);

  const state: ShellState = {
    leifOpen,
    toggleLeif: () => setLeifOpen((o) => !o),
    openPalette: () => setPalette(true),
    theme,
    toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };

  return (
    <Ctx.Provider value={state}>
      <div className="app">
        <Sidebar />
        <main className="main">
          <Topbar crumbs={crumbs} />
          <div className="scroll">{children}</div>
        </main>
        <LeifPanel />
      </div>
      {palette && <CommandBar onClose={() => setPalette(false)} />}
    </Ctx.Provider>
  );
}
