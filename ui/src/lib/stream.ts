// The app's one live channel. `/api/stream` only says that something changed; a screen then
// reads what it shows again. One EventSource serves the whole page, and the browser reconnects
// it by itself. The server ends each stream after a while with an `end` event; a reconnect
// after anything else counts as a change to everything, since events may have been missed.

import { useEffect, useRef } from "react";

export type Change = { subject: string; workspace: string; unit: string };

const EVERYTHING: Change = { subject: "", workspace: "", unit: "" };
const listeners = new Set<(c: Change) => void>();
let source: EventSource | null = null;

function open() {
  if (source || typeof EventSource === "undefined") return;
  let dropped = false;
  let ending = false;
  source = new EventSource("/api/stream");
  source.addEventListener("end", () => {
    ending = true;
  });
  source.onmessage = (m) => {
    const change = JSON.parse(m.data) as Change;
    listeners.forEach((l) => l(change));
  };
  source.onerror = () => {
    dropped = dropped || !ending;
  };
  source.onopen = () => {
    if (dropped) listeners.forEach((l) => l(EVERYTHING));
    dropped = ending = false;
  };
}

export function onChange(listener: (c: Change) => void): () => void {
  listeners.add(listener);
  open();
  return () => {
    listeners.delete(listener);
    if (!listeners.size && source) {
      source.close();
      source = null;
    }
  };
}

/** A change that a screen showing `subjects` (prefixes, `""` for all) in `workspace` cares about. */
export function matches(change: Change, subjects: string[], workspace = ""): boolean {
  const mine = !workspace || !change.workspace || change.workspace === workspace;
  return mine && subjects.some((s) => change.subject.startsWith(s));
}

/** Calls `fn` after a matching change, once per burst: changes come in runs (queued, running, ended). */
export function useChanges(subjects: string[] | null, fn: () => void, workspace = "", wait = 400) {
  const latest = useRef(fn);
  latest.current = fn;
  const key = subjects?.join("|") ?? null;
  useEffect(() => {
    if (key === null) return;
    const wanted = key.split("|");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = onChange((c) => {
      if (!matches(c, wanted, workspace)) return;
      clearTimeout(timer);
      timer = setTimeout(() => latest.current(), wait);
    });
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [key, workspace, wait]);
}
