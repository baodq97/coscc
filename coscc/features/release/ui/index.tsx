// The release card on a project's Work page: what the next release would gather, and the one
// button that moves it forward (prepare the pull request, then publish it).

import { useState } from "react";
import type { ReleaseView } from "@studio/api.gen";
import { api, useResource } from "@studio/lib/api";
import { Button, Chip } from "@studio/components/ui";
import type { FeatureUI } from "@studio/lib/feature";
import type { Workspace } from "@studio/lib/model";

/** A one-line summary of what a release would gather, by kind: `12 feat, 3 fix`. */
export function kinds(units: { type: string }[]): string {
  const n: Record<string, number> = {};
  units.forEach((u) => (n[u.type || "other"] = (n[u.type || "other"] ?? 0) + 1));
  return Object.entries(n)
    .sort((a, b) => b[1] - a[1])
    .map(([k, c]) => `${c} ${k}`)
    .join(", ");
}

/** What a release would gather: the sentence for the card, and one line per commit without a unit. */
export function summary(r: Pick<ReleaseView, "count" | "units" | "unmatched" | "last_tag">): { text: string; lines: string[] } {
  const n = r.unmatched.length;
  const units = `${r.count} ${r.count === 1 ? "unit" : "units"}${r.count ? ` (${kinds(r.units)})` : ""}`;
  const loose = n ? ` and ${n} ${n === 1 ? "commit" : "commits"} without a unit` : "";
  return { text: `${units}${loose} since ${r.last_tag}`, lines: r.unmatched.map((c) => c.subject) };
}

function Release({ workspace }: { workspace: Workspace }) {
  const cwd = workspace.path;
  const release = useResource("/api/release", { cwd }, { on: ["integration.ended", "step.ended"] });
  const [asking, setAsking] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const act = async (what: string, run: () => Promise<unknown>) => {
    if (asking !== what) return setAsking(what);
    setBusy(true);
    setError(null);
    setSaid([]);
    try {
      await run();
      setAsking("");
      release.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const r: ReleaseView | null | undefined = release.data;
  if (!r || r.state === "nothing" || r.state === "unknown") return null;
  const press = () => act(r.button, () => api.stream(`/api/release/${r.button}`, { cwd, version: r.version }, (l) => l.type === "chunk" && setSaid((s) => [...s, String(l.text)])));
  return (
    <div>
      <div className="card card-b" style={{ marginTop: 12 }}>
        <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
          <b>Release {r.version || r.proposed}</b>
          <span className="faint">
            {r.state === "ready"
              ? summary(r).text
              : r.state === "pr-open"
                ? `pull request #${r.pr} open`
                : r.state === "merged-untagged"
                  ? "merged, not tagged yet"
                  : r.state}
          </span>
          <span className="grow" />
          {r.release_url && (
            <a href={r.release_url} target="_blank" rel="noreferrer" className="faint">
              On GitHub
            </a>
          )}
        </div>
        {r.state === "ready" && r.unmatched.length > 0 && (
          <ul className="faint" style={{ fontSize: 12, margin: "8px 0 0", paddingLeft: 18 }}>
            {summary(r).lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
        {r.checks.length > 0 && (
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            {r.checks.map((c) => (
              <Chip key={c.name} square tone={c.bucket === "pass" ? "green" : c.bucket === "fail" ? "red" : "amber"}>
                {c.name}: {c.bucket}
              </Chip>
            ))}
          </div>
        )}
        {r.button && (
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <Button size="sm" kind="primary" disabled={busy || !r.enabled} onClick={press}>
              {asking === r.button
                ? r.button === "prepare"
                  ? `Open the pull request for ${r.version}?`
                  : `Merge and tag ${r.version}? This publishes it`
                : r.button === "prepare"
                  ? `Prepare ${r.version}`
                  : `Publish ${r.version}`}
            </Button>
            <span className="faint" style={{ fontSize: 12 }}>{r.enabled ? r.consequence : r.disabled_reason}</span>
          </div>
        )}
      </div>
      {said.length > 0 && <pre className="mono faint" style={{ fontSize: 12, marginTop: 8, whiteSpace: "pre-wrap" }}>{said.join("")}</pre>}
      {error && <div style={{ color: "var(--red)", fontSize: 12.5, marginTop: 8 }}>{error.message}</div>}
    </div>
  );
}

export const ui: FeatureUI = { project: Release };
