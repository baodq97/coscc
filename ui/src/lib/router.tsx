// Path routing under `/next`, with no library: the app serves `index.html` for every path
// below it (`coscc/studio.py`), and the page reads `location.pathname`.

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

export const BASE = "/next";

export function currentPath(): string {
  const p = location.pathname.startsWith(BASE) ? location.pathname.slice(BASE.length) : location.pathname;
  return p.replace(/\/+$/, "") || "/";
}

export function navigate(to: string): void {
  history.pushState(null, "", BASE + (to === "/" ? "/" : to));
  dispatchEvent(new PopStateEvent("popstate"));
}

export function usePath(): string {
  const [path, setPath] = useState(currentPath);
  useEffect(() => {
    const on = () => setPath(currentPath());
    addEventListener("popstate", on);
    return () => removeEventListener("popstate", on);
  }, []);
  return path;
}

/** `match("/unit/:ws/:n", "/unit/coscc/162")` gives `{ws: "coscc", n: "162"}`, or null. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const want = pattern.split("/").filter(Boolean);
  const got = path.split("/").filter(Boolean);
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < want.length; i++) {
    if (want[i].startsWith(":")) params[want[i].slice(1)] = decodeURIComponent(got[i]);
    else if (want[i] !== got[i]) return null;
  }
  return params;
}

export function Link({ to, className, children, title }: { to: string; className?: string; children: ReactNode; title?: string }) {
  const onClick = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
  return (
    <a href={BASE + to} className={className} onClick={onClick} title={title}>
      {children}
    </a>
  );
}
