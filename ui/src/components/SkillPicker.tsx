// Which skills one agent is given: remove one, add any skill a pack or the owner has, or write a
// new one on the Skills page. A change is a draft of the agent's `skills`, saved with its page.

import { useResource } from "../lib/api";
import { Link } from "../lib/router";
import { Chip } from "./ui";

/** `names` with `name` added at the end, or taken out; never twice. */
export function toggleSkill(names: string[], name: string): string[] {
  return names.includes(name) ? names.filter((n) => n !== name) : [...names, name];
}

export function SkillPicker({ agent, title, names, editable, onChange }: { agent: string; title: string; names: string[]; editable: boolean; onChange: (names: string[]) => void }) {
  const all = useResource("/api/skills").data?.skills ?? [];
  const more = all.filter((s) => !names.includes(s.name));
  return (
    <div className="row skill-pick" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      {names.length === 0 && <span className="faint">It is given no skill.</span>}
      {names.map((n) => (
        <Chip key={n} tone="plain">
          <Link to={`/skills?skill=${n}`}>{n}</Link>
          {editable && (
            <button className="linkish" aria-label={`Remove ${n}`} title={`Remove ${n}`} onClick={() => onChange(toggleSkill(names, n))}>
              ×
            </button>
          )}
        </Chip>
      ))}
      {editable && more.length > 0 && (
        <select className="input" style={{ width: "auto" }} aria-label="Add a skill" value="" onChange={(e) => e.target.value && onChange(toggleSkill(names, e.target.value))}>
          <option value="">Add a skill…</option>
          {more.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
      )}
      {editable && <Link to={`/skills?${new URLSearchParams({ new: "1", agent, as: title })}`}>New skill…</Link>}
    </div>
  );
}
