// The New agent dialog: a name, a key and, if you like, a glyph, started from a copy of an agent or
// from a blank reader. A refusal's reasons land beside the field they are about.

import { useState, type ReactNode } from "react";
import type { AgentPage } from "../api.gen";
import { api, ApiError } from "../lib/api";
import { agentNameProblem, keyProblem, packTitle, slugKey, sortReasons, type BuildAgent, type NewAgentField } from "../lib/build";
import { refreshPacks } from "../lib/pack";
import { navigate } from "../lib/router";
import { Button, Dialog } from "./ui";

function Part({ label, hint, errors, children }: { label: string; hint?: string; errors?: string[]; children: ReactNode }) {
  return (
    <label className="f">
      <span className="lab">{label}</span>
      {children}
      {hint && !errors?.length && <span className="faint" style={{ fontSize: 12 }}>{hint}</span>}
      {errors?.map((e) => (
        <span key={e} className="field-err">{e}</span>
      ))}
    </label>
  );
}

export function NewAgent({ rows, cwd, onClose }: { rows: BuildAgent[]; cwd: string; onClose: () => void }) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [glyph, setGlyph] = useState("");
  const [from, setFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ byField: Partial<Record<NewAgentField, string[]>>; rest: string[] }>({ byField: {}, rest: [] });
  const shownKey = keyTouched ? key : slugKey(name);
  const problem = shownKey ? keyProblem(shownKey, rows.map((r) => r.key)) : null;
  const nameBad = name.trim() ? agentNameProblem(name.trim()) : null;
  const groups = [...new Set(rows.map(packTitle))];

  const save = async () => {
    setBusy(true);
    setSaid({ byField: {}, rest: [] });
    try {
      await api.post<AgentPage>("/api/agents/new", { cwd, key: shownKey, name: name.trim(), ...(from ? { from } : {}) });
      if (glyph.trim()) await api.post<AgentPage>("/api/agents/field", { cwd, key: shownKey, field: "glyph", value: glyph.trim() });
      refreshPacks();
      navigate(`/agents/${shownKey}`);
      onClose();
    } catch (e) {
      setSaid(sortReasons(e instanceof ApiError && e.reasons.length ? e.reasons : [(e as Error).message]));
    } finally {
      setBusy(false);
    }
  };
  const ready = name.trim() && !nameBad && shownKey && !problem;
  const at = (f: NewAgentField) => said.byField[f];

  return (
    <Dialog title="New agent" onClose={onClose}>
      <Part label="Name" errors={[...(nameBad ? [nameBad] : []), ...(at("name") ?? [])]} hint="What the board calls it. Letters, digits and dashes.">
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Tidy" />
      </Part>
      <Part label="Key" errors={[...(problem ? [problem] : []), ...(at("key") ?? [])]} hint="Its address, as in /agents/tidy. It cannot change later.">
        <input className="input mono" value={shownKey} onChange={(e) => (setKeyTouched(true), setKey(e.target.value))} placeholder="tidy" />
      </Part>
      <Part label="Glyph" errors={at("glyph")} hint="One character for its icon. Optional.">
        <input className="input sm" maxLength={2} value={glyph} onChange={(e) => setGlyph(e.target.value)} />
      </Part>
      <Part label="Start from" errors={at("from")} hint="A copy of that agent, which you then change on its page.">
        <select className="input" value={from} onChange={(e) => setFrom(e.target.value)}>
          <option value="">A blank reader: reads, proposes, runs when you ask</option>
          {groups.map((g) => (
            <optgroup key={g} label={g}>
              {rows.filter((r) => packTitle(r) === g).map((r) => (
                <option key={r.key} value={r.key}>
                  {r.row.name ?? r.key} ({r.key})
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </Part>
      {said.rest.map((r) => (
        <div key={r} className="field-err">{r}</div>
      ))}
      <div className="dlg-f">
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={busy || !ready} onClick={save}>{busy ? "Creating…" : "Create agent"}</Button>
      </div>
      {!ready && (name || shownKey) && <div className="faint" style={{ fontSize: 12 }}>{!name.trim() ? "Give it a name to continue." : nameBad ? "Fix the name to continue." : "Fix the key to continue."}</div>}
    </Dialog>
  );
}
