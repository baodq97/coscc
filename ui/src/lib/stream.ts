// The app's one live channel. `/api/stream` mostly says that something changed; a screen then
// reads what it shows again. One EventSource serves the whole page, and the browser reconnects
// it by itself. The server ends each stream after a while with an `end` event; a reconnect
// after anything else counts as a change to everything, since events may have been missed.

import { useEffect, useRef } from "react";

type OfUnit = { workspace: string; unit: string };
/** A subject and the payload it declares (`SCHEMAS` in `coscc/bus.py`). */
export type Change = { subject: string } & (
  | (OfUnit & { going_down: boolean }) // an attempt's move, `<machine>.<state>`
  | (OfUnit & { sha: string; at: string }) // `unit.shipped`
  | OfUnit // `answer.written`, `hold.moved`, `mode.set`, `retake.ended`, `integration.escalated`
  | { workspace: string } // `shortlist.saved`, `board.read`
  | { workspace: string; agent: string; run: string } // `agent-run.started`
  | { workspace: string; agent: string; run: string; outcome: string } // `agent-run.ended`
  | { session: string } // `chat-turn.ended`
);

const EVERYTHING: Change = { subject: "", workspace: "" };
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
export function matches(change: Change, subjects: string[], workspace = "", except: string[] = []): boolean {
  const of = "workspace" in change ? change.workspace : "";
  const mine = !workspace || !of || of === workspace;
  // The replay after a reconnect has subject `""`: it is a change to everything, whatever is left out.
  const left = change.subject !== "" && except.some((s) => change.subject.startsWith(s));
  return mine && !left && subjects.some((s) => change.subject.startsWith(s));
}

/** Calls `fn` after a matching change, once per burst: changes come in runs (queued, running, ended). */
export function useChanges(subjects: string[] | null, fn: () => void, workspace = "", wait = 400, except: string[] = []) {
  const latest = useRef(fn);
  latest.current = fn;
  const key = subjects?.join("|") ?? null;
  useEffect(() => {
    if (key === null) return;
    const wanted = key.split("|");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = onChange((c) => {
      if (!matches(c, wanted, workspace, except)) return;
      clearTimeout(timer);
      timer = setTimeout(() => latest.current(), wait);
    });
    return () => {
      clearTimeout(timer);
      stop();
    };
    // `except` is a constant of its caller.
  }, [key, workspace, wait]);
}
