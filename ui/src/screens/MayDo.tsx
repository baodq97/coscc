// Leif's contract with the owner, as far as the app holds it today: how much may be spent and
// how fast, what runs on its own in each project, which features are on, and the app itself.
// Delegations in the owner's words come with Leif's own backend.

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { PackShown, Shown } from "../api.gen";
import { api, useResource } from "../lib/api";
import { useBoards } from "../lib/boards";
import { money } from "../lib/format";
import type { Workspace } from "../lib/model";
import { ProcessDiagram, ProcessEditor } from "../components/process";
import { useQuery } from "../lib/router";
import { useRunWorkspace } from "./Agents";
import { refreshPacks } from "../lib/pack";
import { ApiError } from "../lib/api";
import { sizeWords, walkChoices, type BuildAgent, type BuildPack, type BuildProcess } from "../lib/build";
import { Button, Chip, Dialog, ErrorState, Meter, PageHead, SkeletonRows } from "../components/ui";

export function MayDo() {
  const { boards, loading } = useBoards();
  const cap = boards.map((b) => b.board?.autopilot?.cap).find(Boolean);
  // `?draft=<run>`: Dagaz's process opens in the editor of the project it was run in, as Leif hands it over.
  const run = useQuery("draft");
  const ofRun = useRunWorkspace(run, boards.map((b) => b.workspace));

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
        {loading ? <SkeletonRows rows={3} /> : boards.map((b, i) => <Project key={b.workspace.path} workspace={b.workspace} run={ofRun === b.workspace.path || (ofRun === "" && i === 0) ? run : ""} />)}
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

function Project({ workspace, run }: { workspace: Workspace; run: string }) {
  const cwd = workspace.path;
  const settings = useResource("/api/settings/autopilot", { cwd });
  const shown = useResource("/api/features/shown", { cwd });
  const packs = useResource("/api/packs", { cwd });
  const { error, busy, save } = useSaving(() => {
    settings.reload();
    shown.reload();
    packs.reload();
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
        {packs.data && (
          <Packs cwd={cwd} workspace={workspace.name} packs={packs.data} run={run} disabled={busy} onSave={(name, body) => save("/api/packs", { cwd, name, ...body })} onChanged={() => (packs.reload(), refreshPacks())} />
        )}
        {shown.data && shown.data.map((f) => <Feature key={f.name} feature={f} disabled={busy} onState={(state) => save("/api/features", { cwd, name: f.name, state })} />)}
        {error && <div style={{ color: "var(--red)", marginTop: 8, fontSize: 12.5 }}>{error.message}</div>}
      </div>
    </div>
  );
}

/** The packs of one project, each as a card, and the one door in for a pack from outside. */
function Packs({ cwd, workspace, packs, run, disabled, onSave, onChanged }: { cwd: string; workspace: string; packs: PackShown[]; run: string; disabled: boolean; onSave: (name: string, body: { on?: boolean; process?: string }) => void; onChanged: () => void }) {
  const agents = useResource("/api/agents", { cwd });
  const [importing, setImporting] = useState(false);
  const [added, setAdded] = useState("");
  const rows = (agents.data?.rows ?? []) as BuildAgent[];
  return (
    <>
      <div className="pack-bar">
        <span className="lab">Packs</span>
        <span className="faint grow">A pack is a set of agents and processes. Yours is the one you build on the page.</span>
        <Button size="sm" onClick={() => (setAdded(""), setImporting(true))}>Import a pack</Button>
      </div>
      {added && <div className="pack-added" role="status">{added}</div>}
      <NewUnitsWalk packs={packs} disabled={disabled} onSave={(ref) => onSave(ref.split("/")[0], { process: ref })} />
      {packs.map((p) => (
        <Pack key={p.name} cwd={cwd} workspace={workspace} pack={p as BuildPack} rows={rows} run={p.own ? run : ""} disabled={disabled} onSave={(body) => onSave(p.name, body)} onChanged={onChanged} />
      ))}
      {importing && (
        <ImportPack
          cwd={cwd}
          onClose={() => setImporting(false)}
          onDone={(list, before) => {
            const fresh = list.filter((p) => !before.includes(p.name));
            setAdded(fresh.length ? `Added ${fresh.map((p) => `${p.name} (${p.processes.length} process${p.processes.length === 1 ? "" : "es"})`).join(", ")}. It is off until you turn it on.` : "Imported.");
            setImporting(false);
            onChanged();
          }}
          known={packs.map((p) => p.name)}
        />
      )}
    </>
  );
}

/** The one process a new unit of the project records: every process of every pack that is on, in one select. */
function NewUnitsWalk({ packs, disabled, onSave }: { packs: PackShown[]; disabled: boolean; onSave: (ref: string) => void }) {
  const { stored, choices } = walkChoices(packs);
  if (!choices.length) return null;
  return (
    <Field label="New units walk" hint="The process a new unit records when it opens. A unit keeps its own for good.">
      <select className="input" aria-label="New units walk" value={stored} disabled={disabled} onChange={(e) => onSave(e.target.value)}>
        {choices.map((c) => (
          <option key={c.ref} value={c.ref}>
            {c.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

const KIND_WORDS = (p: BuildPack) => (p.own ? "Yours" : p.imported ? "Imported" : "Built in");

/** A pack in one project: what it is, on or off, the process a new unit walks, those processes drawn, and its export. */
function Pack({ cwd, workspace, pack, rows, run, disabled, onSave, onChanged }: { cwd: string; workspace: string; pack: BuildPack; rows: BuildAgent[]; run: string; disabled: boolean; onSave: (body: { on?: boolean }) => void; onChanged: () => void }) {
  const [shown, setShown] = useState(pack.process);
  const [editor, setEditor] = useState<"new" | string | null>(run ? "new" : null);
  const here = useRef<HTMLDivElement>(null);
  // The run is known once the project it ran in is found, after the first draw.
  useEffect(() => {
    if (run) setEditor("new");
  }, [run]);
  useEffect(() => {
    if (run && editor === "new") here.current?.scrollIntoView({ block: "start" });
  }, [run, editor]);
  const [removing, setRemoving] = useState(false);
  const [refused, setRefused] = useState<string[]>([]);
  const processes = pack.processes as BuildProcess[];
  const drawn = processes.find((p) => p.ref === shown) ?? processes[0];
  const editing = editor && editor !== "new" ? processes.find((p) => p.ref === editor) : undefined;
  const remove = async () => {
    try {
      await api.post("/api/packs", { cwd, name: pack.name, delete: true });
      onChanged();
    } catch (e) {
      setRefused(e instanceof ApiError && e.reasons.length ? e.reasons : [(e as Error).message]);
      setRemoving(false);
    }
  };
  return (
    <div className="pack-card">
      <Field label={<>{pack.name} <span className="faint">{pack.version}</span> <Chip square tone={pack.own ? "accent" : "plain"}>{KIND_WORDS(pack)}</Chip></>} hint={pack.on ? pack.description : "Off: no new unit or idea opens here. Units already running carry on."}>
        <Toggle on={pack.on} disabled={disabled} onChange={(on) => onSave({ on })} />
      </Field>
      {pack.problems && pack.problems.length > 0 && (
        <div className="pack-problems" role="alert">
          <b>This pack cannot run as it is:</b>
          <ul>{pack.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {refused.length > 0 && (
        <div className="pack-problems" role="alert">
          <b>Not removed:</b>
          <ul>{refused.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      <div className="pack-acts">
        <a className="btn sm" href={`/api/packs/${encodeURIComponent(pack.name)}/export?cwd=${encodeURIComponent(cwd)}`} download={`${pack.name}.zip`}>
          Export
        </a>
        {pack.own && <Button size="sm" onClick={() => setEditor("new")}>New process</Button>}
        {pack.imported &&
          (removing ? (
            <>
              <span className="faint" style={{ fontSize: 12.5 }}>Remove {pack.name} for good?</span>
              <Button size="sm" kind="danger" onClick={remove}>Yes, remove</Button>
              <Button size="sm" kind="ghost" onClick={() => setRemoving(false)}>Keep it</Button>
            </>
          ) : (
            <Button size="sm" kind="ghost" onClick={() => setRemoving(true)}>Remove</Button>
          ))}
        {pack.own && <span className="faint grow" style={{ fontSize: 12 }}>Export holds your own agents and processes, not your changes to built-in ones.</span>}
      </div>
      {editor && (
        <div className="pack-editor" ref={here}>
          <div className="sec-h" style={{ marginTop: 0 }}>{editing ? `Change ${editing.name}` : "New process"}</div>
          <ProcessEditor
            key={editor}
            rows={rows}
            cwd={cwd}
            taken={processes.map((p) => p.name)}
            initial={editing ? { name: editing.name, process: editing } : undefined}
            run={editor === "new" && run ? run : undefined}
            onClose={() => setEditor(null)}
            onSaved={() => (setEditor(null), onChanged())}
          />
        </div>
      )}
      {drawn && !editor && (
        <details id={`pack-${workspace}-${pack.name}`} className="pack-draw">
          <summary>How a unit walks {drawn.name}</summary>
          <div className="row" style={{ gap: 6, margin: "10px 0 8px" }}>
            {processes.map((p) => (
              <button key={p.ref} className={`btn sm ${p.ref === drawn.ref ? "primary" : "ghost"}`} onClick={() => setShown(p.ref)}>
                {p.name}
              </button>
            ))}
          </div>
          {drawn.own && (
            <div style={{ marginBottom: 8 }}>
              <Button size="sm" onClick={() => setEditor(drawn.ref)}>Change {drawn.name}</Button>
            </div>
          )}
          <ProcessDiagram process={drawn} />
        </details>
      )}
    </div>
  );
}

/** Pick a pack's zip, then send it: it is checked whole and refused with every reason, or kept off. */
function ImportPack({ cwd, known, onClose, onDone }: { cwd: string; known: string[]; onClose: () => void; onDone: (list: PackShown[], before: string[]) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState<string[]>([]);
  const send = async () => {
    if (!file) return;
    setBusy(true);
    setReasons([]);
    try {
      onDone(await api.post<PackShown[]>(`/api/packs/import?cwd=${encodeURIComponent(cwd)}`, file), known);
    } catch (e) {
      setReasons(e instanceof ApiError && e.reasons.length ? e.reasons : [(e as Error).message]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Import a pack" onClose={onClose}>
      <label className="f">
        <span className="lab">A pack's zip</span>
        <input type="file" accept=".zip,application/zip" aria-label="Pack file" onChange={(e) => (setFile(e.target.files?.[0] ?? null), setReasons([]))} />
        <span className="faint" style={{ fontSize: 12 }}>Up to 1 MB. Only agents, skills and processes are read.</span>
      </label>
      {file && (
        <div className="pack-added">
          <b>{file.name}</b> <span className="faint">{sizeWords(file.size)}</span>
          <div className="faint" style={{ fontSize: 12.5 }}>It is checked whole first. If it passes, its agents and processes are added and stay off until you turn the pack on.</div>
        </div>
      )}
      {reasons.length > 0 && (
        <div className="pe-refused" role="alert">
          <b>Not imported</b>
          <ul>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      <div className="dlg-f">
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={!file || busy} onClick={send}>{busy ? "Checking…" : "Import"}</Button>
      </div>
      {!file && <div className="faint" style={{ fontSize: 12 }}>Choose a file to continue.</div>}
    </Dialog>
  );
}

function Field({ label, hint, children }: { label: ReactNode; hint?: string; children: ReactNode }) {
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

