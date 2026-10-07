// Every skill the agents can be given: where it comes from, which agents name it, and how often
// a run was given it in the last 30 days. A new skill is the owner's own text, written into their
// layer and, if they say so, given to one agent at once.

import { useState } from "react";
import type { Skill, SkillsPage } from "../api.gen";
import { ApiError, api, useResource } from "../lib/api";
import { ago } from "../lib/format";
import { Link, setQuery, useQuery } from "../lib/router";
import { Button, Chip, Dialog, Empty, ErrorState, PageHead, SkeletonRows } from "../components/ui";

export const SKILL_NAME = /^[a-z][a-z0-9-]{0,39}$/;
export const SKILL_MAX_BYTES = 16_000;

/** Why `name` and `text` cannot be saved yet, or "" (the app says the rest: a taken name). */
export function skillProblem(name: string, text: string): string {
  if (!name) return "Give it a name.";
  if (!SKILL_NAME.test(name)) return "A name is lowercase letters, digits and hyphens, opening with a letter, at most 40.";
  if (!text.trim()) return "Write what the agent should do.";
  if (new TextEncoder().encode(text).length > SKILL_MAX_BYTES) return "At most 16 KB.";
  return "";
}

function whose(s: Skill): string {
  if (s.own) return "Yours";
  return s.edited ? `${s.pack}, edited by you` : s.pack;
}

export function Skills() {
  const res = useResource("/api/skills", {}, { on: ["agent-run."] });
  const open = useQuery("skill");
  const adding = useQuery("new") === "1";
  const agent = useQuery("agent");
  const skills = res.data?.skills ?? [];
  const shown = skills.find((s) => s.name === open);
  return (
    <div className="page">
      <PageHead
        title="Skills"
        lede="The rules an agent is given with its prompt. A skill counts as used each time a run is given it."
        actions={
          <Button kind="primary" size="sm" icon="plus" onClick={() => setQuery("new", "1")}>
            New skill
          </Button>
        }
      />
      {res.state === "error" && !res.data ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.state === "loading" ? (
        <SkeletonRows rows={6} />
      ) : skills.length === 0 ? (
        <Empty icon="book" title="No skills yet">Write one with New skill.</Empty>
      ) : (
        <>
          {res.data?.problems.map((p) => (
            <div key={p} className="callout amber" role="alert" style={{ marginTop: 16 }}>
              {p}
            </div>
          ))}
          <div className="card" style={{ marginTop: 20 }}>
            {skills.map((s) => (
              <button key={s.name} className="agent-line skill-line" onClick={() => setQuery("skill", s.name)}>
                <span />
                <span className="who">
                  <b>{s.name}</b>
                  <span className="when">{s.description || "No description."}</span>
                </span>
                <span className="what">{whose(s)}</span>
                <span className="what">{s.agents.length ? s.agents.join(", ") : "No agent"}</span>
                <span className="cost">
                  {s.uses_30d ? `${s.uses_30d} ${s.uses_30d === 1 ? "use" : "uses"} · ${ago(s.last_used)}` : "Not used in 30 days"}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
      {shown && <SkillText s={shown} onClose={() => setQuery("skill", "")} />}
      {adding && (
        <NewSkill
          agent={agent}
          onClose={() => {
            setQuery("new", "");
            setQuery("agent", "");
          }}
          onSaved={(name) => {
            res.reload();
            setQuery("new", "");
            setQuery("agent", "");
            setQuery("skill", name);
          }}
        />
      )}
    </div>
  );
}

function SkillText({ s, onClose }: { s: Skill; onClose: () => void }) {
  return (
    <Dialog title={s.name} onClose={onClose} wide>
      <div className="kv">
        <span className="k">From</span>
        <span>{whose(s)}</span>
        <span className="k">Given to</span>
        <span className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {s.agents.length ? s.agents.map((a) => <Link key={a} to={`/agents/${a}/prompt`}>{a}</Link>) : <span className="faint">No agent names it yet: add it on an agent's Prompt & skills tab.</span>}
        </span>
        <span className="k">Used</span>
        <span>{s.uses_30d ? `${s.uses_30d} runs in 30 days, last ${ago(s.last_used)}` : "No run in 30 days"}</span>
        <span className="k">Version</span>
        <span className="mono faint">{s.hash}</span>
      </div>
      <pre className="skill-text">{s.text}</pre>
      <div className="faint" style={{ fontSize: 12 }}>Edit its text on the Prompt & skills tab of an agent that names it.</div>
    </Dialog>
  );
}

function NewSkill({ agent, onClose, onSaved }: { agent: string; onClose: () => void; onSaved: (name: string) => void }) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const problem = skillProblem(name, text);
  const save = async () => {
    setBusy(true);
    setSaid("");
    try {
      await api.post<SkillsPage>("/api/skills/new", { name, text, ...(agent ? { agent } : {}) });
      onSaved(name);
    } catch (e) {
      setSaid(e instanceof ApiError && e.reasons.length ? e.reasons.join("; ") : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title={agent ? `New skill for ${agent}` : "New skill"} onClose={onClose} wide>
      <div className="kv">
        <span className="k">Name</span>
        <input className="input mono" aria-label="Skill name" value={name} placeholder="for-example-cite-sources" onChange={(e) => setName(e.target.value.trim())} />
      </div>
      <textarea className="ta mono" aria-label="Skill text" rows={14} value={text} placeholder="What the agent should do, in your words. Every run of an agent that names this skill is given it." onChange={(e) => setText(e.target.value)} />
      {said && (
        <div className="pe-refused" role="alert">
          <b>Not saved</b>
          <div>{said}</div>
        </div>
      )}
      <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Button kind="primary" size="sm" disabled={busy || Boolean(problem)} onClick={save}>
          {agent ? `Save and give to ${agent}` : "Save"}
        </Button>
        <span className="faint" style={{ fontSize: 12 }}>{problem || "Saved in your own layer; nothing runs until an agent names it."}</span>
        {(name || text) && <Chip tone="plain">{new TextEncoder().encode(text).length.toLocaleString()} / 16,000 bytes</Chip>}
      </div>
    </Dialog>
  );
}
