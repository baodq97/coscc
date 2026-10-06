// The Vault page and a unit's leak line. Only metadata comes in here; a value goes out through
// one form post (`POST /api/vault/secrets`) and is never read back.

import { useState, type FormEvent, type ReactNode } from "react";
import type { Meta } from "@studio/api.gen";
import { api, useResource } from "@studio/lib/api";
import type { FeatureUI } from "@studio/lib/feature";
import { Icon } from "@studio/lib/icons";
import type { Workspace } from "@studio/lib/model";
import { Button, Chip, Dialog, Empty, ErrorState, SkeletonRows } from "@studio/components/ui";

const NO_AGE = "age is not installed on this machine, so a value cannot be saved: install age.";

type Open = { kind: "add" } | { kind: "access" | "value"; s: Meta } | null;
type Say = { tone: "red" | "amber" | "accent"; text: string } | null;

function Checks({ label, name, options, on, disabled }: { label: string; name: string; options: string[]; on: string[]; disabled?: boolean }) {
  return (
    <>
      <span className="lab">{label}</span>
      <div className="checks">
        {options.map((o) => (
          <label key={o}>
            <input type="checkbox" name={name} value={o} defaultChecked={on.includes(o)} disabled={disabled} /> {o}
          </label>
        ))}
      </div>
    </>
  );
}

function Value({ label }: { label: string }) {
  // Not a password input: a browser strips its line breaks, and a key is many lines.
  return <textarea className="ta secret" name="value" rows={3} required autoComplete="off" spellCheck={false} autoCapitalize="off" aria-label={label} />;
}

/** A dialog that sends its fields as the form post and says what went wrong inside itself. */
function ValueDialog({ title, workspace, age, onClose, onSaved, children }: { title: string; workspace: Workspace; age: boolean; onClose: () => void; onSaved: (say: Say) => void; children: ReactNode }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const fields = new URLSearchParams({ cwd: workspace.path });
    form.forEach((v, k) => fields.append(k, String(v)));
    setBusy(true);
    try {
      const done = await api.post<{ saved: string; short: boolean }>("/api/vault/secrets", fields);
      onSaved(done.short ? { tone: "amber", text: `Saved ${done.saved}. That value is short, so masking it may hide other text.` } : { tone: "accent", text: `Saved ${done.saved}.` });
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };
  return (
    <Dialog title={title} onClose={onClose}>
      <form className="dlg-b" style={{ padding: 0 }} onSubmit={send}>
        {children}
        {error && <div style={{ color: "var(--red)", fontSize: 12.5 }}>{error}</div>}
        <div className="dlg-f">
          {!age && <span className="faint" style={{ fontSize: 12 }}>Install age to save a value.</span>}
          <Button onClick={onClose}>Cancel</Button>
          <button className="btn primary" type="submit" disabled={!age || busy}>
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function VaultPage({ workspace }: { workspace: Workspace }) {
  const cwd = workspace.path;
  const got = useResource("/api/vault/secrets", { cwd }, { on: [""] });
  const [open, setOpen] = useState<Open>(null);
  const [say, setSay] = useState<Say>(null);
  const [busy, setBusy] = useState(false);

  const v = got.data;
  if (got.state === "error" && !v) return <ErrorState error={got.error} onRetry={got.reload} />;
  if (!v) return <SkeletonRows rows={3} />;

  const act = async (verb: string, s: Meta, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    try {
      await api.post(`/api/vault/${verb}`, { cwd, name: s.name, tier: s.tier, ...extra });
      setOpen(null);
      setSay(null);
    } catch (e) {
      setOpen(null);
      setSay({ tone: "red", text: (e as Error).message });
    }
    setBusy(false);
    got.reload();
  };
  const saved = (m: Say) => {
    setOpen(null);
    setSay(m);
    got.reload();
  };
  const ungranted = v.globals.filter((s) => !s.granted);
  const close = () => setOpen(null);

  return (
    <>
      {!v.age && <div className="callout amber" style={{ marginTop: 16 }}><Icon name="warn" size={15} /><div className="grow">{NO_AGE}</div></div>}
      {say && (
        <div className={`callout ${say.tone}`} role="status" style={{ marginTop: 16 }}>
          <div className="grow">{say.text}</div>
        </div>
      )}

      <div className="sec-h">
        Secrets <span className="faint">{v.secrets.length || ""}</span>
        <span className="r">
          <Button size="sm" kind="primary" icon="plus" onClick={() => setOpen({ kind: "add" })}>
            Add secret
          </Button>
        </span>
      </div>
      {v.secrets.length ? (
        <div className="card" style={{ overflowX: "auto" }}>
          <table className="t">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kept for</th>
                <th>Agents that may use it</th>
                <th>Passed as</th>
                <th>Value</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {v.secrets.map((s) => (
                <tr key={`${s.tier}:${s.name}`}>
                  <td>
                    <b style={{ fontFamily: "var(--mono)", fontWeight: 600, overflowWrap: "anywhere" }}>{s.name}</b> {s.broker && <Chip tone="plain">ssh only</Chip>}
                    {s.description && <div className="muted" style={{ fontSize: 12.5 }}>{s.description}</div>}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>{s.tier === "global" ? "every workspace" : "this workspace"}</td>
                  <td>{s.stages.join(", ") || "—"}</td>
                  <td>{s.broker ? "ssh" : s.modes.join(", ") || "—"}</td>
                  <td>{s.has_value ? "set" : <Chip tone="amber">not set</Chip>}</td>
                  <td className="r" style={{ whiteSpace: "nowrap" }}>
                    <Button size="sm" kind="ghost" disabled={busy} onClick={() => setOpen({ kind: "access", s })}>
                      Edit access
                    </Button>
                    <Button size="sm" kind="ghost" disabled={busy} onClick={() => setOpen({ kind: "value", s })}>
                      Replace value
                    </Button>
                    {s.tier === "global" && (
                      <Button size="sm" kind="ghost" disabled={busy} onClick={() => act("revoke", s)}>
                        Revoke
                      </Button>
                    )}
                    <Button size="sm" kind="ghost" disabled={busy} onClick={() => confirm(s.tier === "global" ? "Delete this secret for every workspace?" : "Delete this secret?") && act("delete", s)}>
                      <span style={{ color: "var(--red)" }}>Delete</span>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty icon="lock" title="No secrets here yet">
          Add one and an agent can use it in a command without reading it.
        </Empty>
      )}

      {ungranted.length > 0 && (
        <>
          <div className="sec-h">
            Global secrets not granted here <span className="faint">{ungranted.length}</span>
          </div>
          <div className="card">
            {ungranted.map((s) => (
              <div className="lrow" key={s.name} style={{ height: 44 }}>
                <span className="t" style={{ fontFamily: "var(--mono)" }}>{s.name}</span>
                {s.description && <span className="meta">{s.description}</span>}
                <Button size="sm" disabled={busy} onClick={() => act("grant", s)}>
                  Grant
                </Button>
              </div>
            ))}
          </div>
        </>
      )}

      {open?.kind === "add" && (
        <ValueDialog title="Add a secret" workspace={workspace} age={v.age} onClose={close} onSaved={saved}>
          <label className="f">
            <span className="lab">Name</span>
            <input className="input" name="name" required pattern={v.name_pattern} maxLength={64} title="Lower-case letters, digits, dots, dashes and underscores, up to 64." />
          </label>
          <label className="f">
            <span className="lab">Kept for</span>
            <select className="input" name="tier">
              <option value="ws">this workspace</option>
              <option value="global">every workspace it is granted to</option>
            </select>
          </label>
          <label className="f">
            <span className="lab">Description</span>
            <input className="input" name="description" />
          </label>
          <fieldset>
            <legend>Access</legend>
            <Checks label="Agents that may use it" name="stage" options={v.stages} on={["impl"]} />
            <Checks label="Passed as" name="mode" options={v.modes} on={["env", "file"]} />
            <label>
              <input type="checkbox" name="broker" value="1" /> Broker: passed as ssh only
            </label>
          </fieldset>
          <label className="f">
            <span className="lab">Value</span>
            <Value label="Value" />
          </label>
        </ValueDialog>
      )}
      {open?.kind === "value" && (
        <ValueDialog title={`Replace the value of ${open.s.name}`} workspace={workspace} age={v.age} onClose={close} onSaved={saved}>
          <input type="hidden" name="tier" value={open.s.tier} />
          <input type="hidden" name="name" value={open.s.name} />
          <Value label={`New value of ${open.s.name}`} />
        </ValueDialog>
      )}
      {open?.kind === "access" && <Access s={open.s} stages={v.stages} modes={v.modes} busy={busy} onClose={close} onSave={(stages, modes) => act("policy", open.s, { stages, modes })} />}
    </>
  );
}

function Access({ s, stages, modes, busy, onClose, onSave }: { s: Meta; stages: string[]; modes: string[]; busy: boolean; onClose: () => void; onSave: (stages: string[], modes: string[]) => void }) {
  const [stage, setStage] = useState(s.stages);
  const [mode, setMode] = useState(s.modes);
  const toggle = (list: string[], set: (l: string[]) => void, x: string) => set(list.includes(x) ? list.filter((y) => y !== x) : [...list, x]);
  const group = (label: string, options: string[], list: string[], set: (l: string[]) => void) => (
    <>
      <span className="lab">{label}</span>
      <div className="checks">
        {options.map((o) => (
          <label key={o}>
            <input type="checkbox" checked={list.includes(o)} onChange={() => toggle(list, set, o)} /> {o}
          </label>
        ))}
      </div>
    </>
  );
  return (
    <Dialog title={`Access of ${s.name}`} onClose={onClose}>
      {group("Agents that may use it", stages, stage, setStage)}
      {s.broker ? <div className="muted">Passed as ssh only.</div> : group("Passed as", modes, mode, setMode)}
      <div className="dlg-f">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={busy} onClick={() => onSave(stage, s.broker ? [] : mode)}>
          Save access
        </Button>
      </div>
    </Dialog>
  );
}

function Leaks({ workspace, unit }: { workspace: Workspace; unit: string }) {
  const got = useResource("/api/vault/leaks", { cwd: workspace.path, unit }, { on: [""] });
  const names = got.data?.names ?? [];
  if (!names.length) return null;
  return (
    <div className="callout red" role="status" style={{ marginTop: 14 }}>
      <Icon name="warn" size={15} />
      <div className="grow">It cannot go out: {names.join(", ")} appears in its work.</div>
    </div>
  );
}

export const ui: FeatureUI = {
  unit: Leaks,
  page: { label: "Vault", lede: "Secrets an agent can use in a command but never read.", icon: "lock", Component: VaultPage },
};
