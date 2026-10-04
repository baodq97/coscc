// The one way the studio talks to the app: its own `/api` routes, with the login cookie the
// browser already holds. A 401 means the session ended, so the page goes to the login.

import { useEffect, useRef, useState } from "react";
import { useChanges } from "./stream";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    throw new ApiError(401, "signed out");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || res.statusText);
  return data as T;
}

export const api = {
  get: <T>(path: string, query: Record<string, string> = {}) => {
    const qs = new URLSearchParams(query).toString();
    return call<T>("GET", qs ? `${path}?${qs}` : path);
  },
  post: <T>(path: string, body: unknown) => call<T>("POST", path, body),
};

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
export function useResource<T>(
  path: string | null,
  query: Record<string, string> = {},
  { on = null, every = 0 }: { on?: string[] | null; every?: number } = {},
): Resource<T> & { reload: () => void } {
  const [res, setRes] = useState<Resource<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${path}?${new URLSearchParams(query)}`;
  const last = useRef<T | undefined>(undefined);

  useEffect(() => {
    if (path === null) return;
    let live = true;
    setRes(last.current === undefined ? { state: "loading" } : { state: "ready", data: last.current });
    api
      .get<T>(path, query)
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
