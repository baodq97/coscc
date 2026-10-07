// The one way the studio talks to the app: its own `/api` routes, with the login cookie the
// browser already holds. A 401 means the session ended, so the page goes to the login.

import { useEffect, useRef, useState } from "react";
import { useChanges } from "./stream";
import type { Get } from "../api.gen";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** A refusal's own words, one per reason (`{error, code, reasons[]}`), and its code. */
    readonly reasons: string[] = [],
    readonly code = "",
  ) {
    super(message);
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  // A form (`URLSearchParams`) goes as it is; the browser names its type.
  // A file (`Blob`) goes as it is, under its own type (a pack's zip).
  const form = body instanceof URLSearchParams;
  const file = body instanceof Blob;
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined || form ? undefined : { "Content-Type": file ? body.type || "application/octet-stream" : "application/json" },
    body: body === undefined ? undefined : form || file ? body : JSON.stringify(body),
  });
  if (res.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new ApiError(401, "signed out");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const reasons: unknown[] = data && Array.isArray(data.reasons) ? data.reasons : [];
    throw new ApiError(res.status, (data && (data.error || data.detail)) || res.statusText, reasons.map(String), (data && data.code) || "");
  }
  return data as T;
}

/** `/api/units/{name}` with `{name: "0001_x"}` is `/api/units/0001_x`; the rest of `query` stays a query. */
export function fill(path: string, query: Record<string, string>): { path: string; rest: Record<string, string> } {
  const rest = { ...query };
  const filled = path.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = rest[key] ?? "";
    delete rest[key];
    return encodeURIComponent(value);
  });
  return { path: filled, rest };
}

/** Reads NDJSON line by line, a line split across chunks included. */
export async function readLines(body: ReadableStream<Uint8Array>, on: (line: Record<string, unknown>) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const lines = (rest + decoder.decode(value, { stream: true })).split("\n");
    rest = lines.pop() ?? "";
    lines.filter(Boolean).forEach((l) => on(JSON.parse(l)));
  }
  if (rest.trim()) on(JSON.parse(rest));
}

export const api = {
  /** A `GET` route the app types (`Get`, made from its routes), so the answer is never guessed. */
  get: <P extends keyof Get>(path: P, query: Record<string, string> = {}) => {
    const url = fill(path, query);
    const qs = new URLSearchParams(url.rest).toString();
    return call<Get[P]>("GET", qs ? `${url.path}?${qs}` : url.path);
  },
  /** `body` is JSON, a `URLSearchParams` sent as a form (the vault's one door for a value), or a file. */
  post: <T>(path: string, body: unknown) => call<T>("POST", path, body),
  /**
   * Post and read the answer's NDJSON lines to the end, for a route whose stream does the work
   * (a chat turn, a release): leaving early would stop it. An `error` line throws.
   */
  stream: async (path: string, body: unknown, on: (line: Record<string, unknown>) => void): Promise<void> => {
    const res = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (res.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      throw new ApiError(401, "signed out");
    }
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => null);
      throw new ApiError(res.status, (data && data.error) || res.statusText);
    }
    await readLines(res.body, (l) => {
      if (l.type === "error") throw new Error(String(l.error));
      on(l);
    });
  },
  /**
   * Start something whose route streams until it ends (a step): wait only for the answer that
   * it began or was refused, then let go. Letting go stops nothing; stopping is its own route.
   */
  start: async (path: string, body: unknown): Promise<void> => {
    const res = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (res.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      throw new ApiError(401, "signed out");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new ApiError(res.status, (data && data.error) || res.statusText);
    }
    await res.body?.cancel();
  },
};

/**
 * Reads an NDJSON route that does not end, for as long as the component lives, and comes back
 * when it drops: after 1 s, doubling to 30 s. A stream silent for 40 s is cut and read again.
 * `after` gives the cursor each time it connects, or null for none.
 */
export function useFollow(path: string, on: (line: Record<string, unknown>) => void, after: () => number | null) {
  const latest = useRef({ on, after });
  latest.current = { on, after };
  useEffect(() => {
    const stop = new AbortController();
    (async () => {
      let wait = 1000;
      while (!stop.signal.aborted) {
        const cut = new AbortController();
        const quit = () => cut.abort();
        stop.signal.addEventListener("abort", quit, { once: true });
        let timer: ReturnType<typeof setTimeout> | undefined;
        const arm = () => {
          clearTimeout(timer);
          timer = setTimeout(quit, 40_000);
        };
        try {
          arm();
          const cursor = latest.current.after();
          const url = cursor == null ? path : `${path}${path.includes("?") ? "&" : "?"}after=${cursor}`;
          const res = await fetch(url, { credentials: "same-origin", cache: "no-store", signal: cut.signal });
          if (res.status === 401) {
            location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
            return;
          }
          if (!res.ok || !res.body) throw new ApiError(res.status, res.statusText);
          wait = 1000;
          await readLines(res.body, (line) => {
            arm();
            latest.current.on(line);
          });
        } catch {
          // dropped or cut: come back below
        }
        clearTimeout(timer);
        stop.signal.removeEventListener("abort", quit);
        if (stop.signal.aborted) return;
        await new Promise((r) => setTimeout(r, wait));
        wait = Math.min(wait * 2, 30_000);
      }
    })();
    return () => stop.abort();
  }, [path]);
}

export type Resource<T> =
  | { state: "loading"; data?: undefined; error?: undefined }
  | { state: "ready"; data: T; error?: undefined }
  | { state: "error"; data?: T; error: Error };

/**
 * Read one route and keep it fresh: again after a change on `/api/stream` whose subject starts
 * with one of `on` (in the workspace `query.cwd`, if any), and every `every` milliseconds while
 * the page is visible, for what the stream does not carry. A failed refresh keeps the last data
 * and reports the error beside it.
 */
export function useResource<P extends keyof Get>(
  path: P | null,
  query: Record<string, string> = {},
  { on = null, every = 0 }: { on?: string[] | null; every?: number } = {},
): Resource<Get[P]> & { reload: () => void } {
  const [res, setRes] = useState<Resource<Get[P]>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${path}?${new URLSearchParams(query)}`;
  const last = useRef<Get[P] | undefined>(undefined);

  useEffect(() => {
    if (path === null) return;
    let live = true;
    setRes(last.current === undefined ? { state: "loading" } : { state: "ready", data: last.current });
    api
      .get(path, query)
      .then((data) => {
        if (!live) return;
        last.current = data;
        setRes({ state: "ready", data });
      })
      .catch((error: Error) => live && setRes({ state: "error", data: last.current, error }));
    return () => {
      live = false;
    };
    // `key` stands for `path` and `query`.
  }, [key, tick]);

  useEffect(() => {
    if (!every) return;
    const id = setInterval(() => document.visibilityState === "visible" && setTick((t) => t + 1), every);
    return () => clearInterval(id);
  }, [every]);

  useChanges(path === null ? null : on, () => setTick((t) => t + 1), query.cwd ?? "");

  return { ...res, reload: () => setTick((t) => t + 1) };
}
