// Leif's contract with the owner, as far as the app holds it today: how much may be spent and
// how fast, what runs on its own in each project, which features are on, and the app itself.
// Delegations in the owner's words come with Leif's own backend.

import { useState, type ReactNode } from "react";
import type { Shown } from "../api.gen";
import { api, useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { money } from "../lib/format";
import type { Workspace } from "../lib/model";
import { Button, ErrorState, Meter, PageHead, SkeletonRows } from "../components/ui";

export function MayDo() {
  const { boards, loading } = useBoards();
  const cap = boards.map((b) => b.board?.autopilot?.cap).find(Boolean);

  return (
    <div className="page mid">
      <PageHead title="What Leif may do" lede="What runs without you, how much it may spend, and the app it runs in. A change applies from the next step." />
      <Section icon="$" title="Spend today">
        <div className="card card-b">
          <div className="row" style={{ alignItems: "baseline" }}>
            <b style={{ fontSize: 18 }}>{money(cap?.spent ?? null)}</b>
            <span className="faint">of {money(cap?.limit ?? null, 0)} today, every project together</span>
          </div>
          <div style={{ marginTop: 8 }}>
            <Meter value={cap?.spent ?? 0} max={cap?.limit ?? 1} />
          </div>
        </div>
      </Section>
      <Section title="Each project">
        {loading ? <SkeletonRows rows={3} /> : boards.map((b) => <Project key={b.workspace.path} workspace={b.workspace} />)}
      </Section>
      <Section title="The app">
        <TheApp />
      </Section>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon?: string; children: ReactNode }) {
  return (
    <>
      <div className="sec-h">
        {icon && <span className="faint">{icon}</span>}
        {title}
      </div>
      {children}
    </>
  );
}

function Toggle({ on, disabled, onChange }: { on: boolean; disabled?: boolean; onChange: (on: boolean) => void }) {
  return <button className={`toggle ${on ? "on" : ""}`} style={{ border: 0, padding: 0, cursor: disabled ? "default" : "pointer" }} disabled={disabled} onClick={() => onChange(!on)} aria-pressed={on} />;
}

function useSaving(reload: () => void) {
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (path: string, body: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(path, body);
      reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, save };
}

function Project({ workspace }: { workspace: Workspace }) {
  const cwd = workspace.path;
  const settings = useResource("/api/settings/autopilot", { cwd });
  const shown = useResource("/api/features/shown", { cwd });
  const { error, busy, save } = useSaving(() => {
    settings.reload();
    shown.reload();
  });
  const set = (name: string, value: unknown) => save("/api/settings/autopilot", { cwd, name, value });
  const s = settings.data;

  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-h">{workspace.name}</div>
      <div className="card-b">
        {settings.state === "error" ? (
          <ErrorState error={settings.error} onRetry={settings.reload} />
        ) : !s ? (
          <SkeletonRows rows={2} />
        ) : (
          <>
            <Field label="Autopilot" hint={s.refused_because || "Runs the shortlisted units' next steps without a press."}>
              <Toggle on={s.autopilot} disabled={busy || Boolean(s.refused_because)} onChange={(on) => set("autopilot", on)} />
            </Field>
            <Field label="May merge" hint="Lets the autopilot ship a unit whose review passed and CI is green.">
              <Toggle on={s.autopilot_may_ship} disabled={busy} onChange={(on) => set("autopilot_may_ship", on)} />
            </Field>
            <Field label="At once" hint="Steps the autopilot runs side by side here.">
              <div className="seg">
                {[1, 2, 3, 4].map((n) => (
                  <button key={n} className={s.max_parallel === n ? "on" : ""} disabled={busy} onClick={() => set("max_parallel", n)}>
                    {n}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Daily cap" hint="The app's, for every project together.">
              <Cap value={s.daily_cap_usd} disabled={busy} onSave={(v) => set("daily_cap_usd", v)} />
            </Field>
          </>
        )}
        {shown.data && shown.data.map((f) => <Feature key={f.name} feature={f} disabled={busy} onState={(state) => save("/api/features", { cwd, name: f.name, state })} />)}
        {error && <div style={{ color: "var(--red)", marginTop: 8, fontSize: 12.5 }}>{error.message}</div>}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <div>
        <div className="lab">{label}</div>
        {hint && <div className="hint">{hint}</div>}
      </div>
      <div className="row">{children}</div>
    </div>
  );
}

function Cap({ value, disabled, onSave }: { value: number; disabled: boolean; onSave: (v: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  const changed = Number(draft) !== value && Number(draft) > 0;
  return (
    <>
      <span className="faint">$</span>
      <input className="input sm" inputMode="decimal" value={draft} onChange={(e) => setDraft(e.target.value)} />
      {changed && (
        <Button size="sm" kind="primary" disabled={disabled} onClick={() => onSave(Number(draft))}>
          Save
        </Button>
      )}
    </>
  );
}

function Feature({ feature, disabled, onState }: { feature: Shown; disabled: boolean; onState: (state: string) => void }) {
  const states = feature.pilot ? ["off", "pilot", "on"] : ["off", "on"];
  return (
    <Field label={feature.name} hint={feature.summary || feature.sentence}>
      <div className="seg">
        {states.map((st) => (
          <button key={st} className={feature.state === st ? "on" : ""} disabled={disabled || (feature.locked && st !== "off")} onClick={() => onState(st)}>
            {st}
          </button>
        ))}
      </div>
    </Field>
  );
}

/** What runs, and the one button the updater offers now: build, apply or cancel. */
function TheApp() {
  const update = useResource("/api/update", {}, { every: 15_000 });
  const { error, busy, save } = useSaving(update.reload);
  const u = update.data;
  if (update.state === "error") return <ErrorState error={update.error} onRetry={update.reload} />;
  if (!u) return <SkeletonRows rows={2} />;
  return (
    <div className="card card-b">
      <Field label="Running" hint={u.line}>
        <span>{u.version}</span>
      </Field>
      <Field label="Local build" hint={u.local_line || "Built from main on this machine."}>
        <div className="row" style={{ gap: 6 }}>
          {u.actions.includes("build-local") && (
            <Button size="sm" disabled={busy} onClick={() => save("/api/update/build-local", {})}>
              Build from main
            </Button>
          )}
          {u.actions.includes("apply-local") && (
            <Button size="sm" kind="primary" disabled={busy} title="Restarts the app once nothing is running." onClick={() => save("/api/update/apply", { channel: "local" })}>
              Apply {u.local?.version}
            </Button>
          )}
          {u.actions.includes("apply-release") && (
            <Button size="sm" disabled={busy} onClick={() => save("/api/update/apply", { channel: "release" })}>
              Apply release {u.release?.version}
            </Button>
          )}
          {u.actions.includes("cancel") && (
            <Button size="sm" kind="ghost" disabled={busy} onClick={() => save("/api/update/cancel", {})}>
              Cancel the update
            </Button>
          )}
        </div>
      </Field>
      {u.warning && <div className="faint" style={{ fontSize: 12.5 }}>{u.warning}</div>}
      {error && <div style={{ color: "var(--red)", marginTop: 8, fontSize: 12.5 }}>{error.message}</div>}
    </div>
  );
}

