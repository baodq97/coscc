// The New agent dialog: a name, a key and, if you like, a glyph, started from a copy of an agent, a
// blank reader, or Dagaz's draft of the task you describe. Dagaz may ask first: its questions come as
// cards with its answer filled in. A draft is kept per project until it is saved or set aside, so
// leaving the page loses nothing. A refusal's reasons land beside the field they are about.
// `DescribeTask` is the "Describe the task" box the process editor shares.

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AgentPage, CatalogTool, ProposalRow, Started, StepEvent } from "../api.gen";
import { api, ApiError, useResource } from "../lib/api";
import { afterAgentSaved, agentNameProblem, answeredTask, draftOf, draftParts, draftTools, GAP_PART, keepDraft, keptDraft, runsByItself, keyProblem, liveLine, packTitle, slugKey, sortReasons, type BuildAgent, type DraftGap, type Drafted, type NewAgentField } from "../lib/build";
import { Rune } from "../lib/icons";
import { refreshPacks } from "../lib/pack";
import { Link, navigate } from "../lib/router";
import { EFFECT, TIER } from "../screens/AgentPage";
import { Button, Chip, Dialog, Dot } from "./ui";

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

type Phase = { at: "idle" } | { at: "running"; run: string; line: string } | { at: "failed"; why: string } | { at: "asked"; run: string; d: Drafted } | { at: "drafted"; run: string };

/**
 * "Describe the task": the words go to Dagaz (one paid run that writes nothing), its live line shows
 * while it runs, and its draft comes back through `onDraft`. When it asks first, its questions show
 * here with its answers filled in; answering runs it again with them. `run` follows a run already
 * started; with none, the project's kept draft is followed again.
 */
export function DescribeTask({ cwd, want, run, onDraft }: { cwd: string; want: "agent" | "process"; run?: string; onDraft: (d: Drafted, run: string) => void }) {
  // Only an agent's draft is kept: the process editor holds its own until it is saved.
  const keeps = want === "agent" && Boolean(cwd);
  const kept = keeps ? keptDraft(want, cwd) : null;
  const [words, setWords] = useState(kept?.words ?? "");
  const [phase, setPhase] = useState<Phase>({ at: "idle" });
  const [answers, setAnswers] = useState<string[]>([]);
  const source = useRef<EventSource | null>(null);
  const onDraftRef = useRef(onDraft);
  onDraftRef.current = onDraft;

  const ended = async (id: string, tries = 0): Promise<void> => {
    try {
      const page = await api.get("/api/runs/{run}", { cwd, run: id, limit: "1" });
      const d = draftOf(page);
      if (d && !d.agent && !d.process) {
        setAnswers((d.questions ?? []).map((q) => q.recommendation));
        setPhase({ at: "asked", run: id, d });
      } else if (d) {
        setPhase({ at: "drafted", run: id });
        onDraftRef.current(d, id);
      } else if (page.status === "running") follow(id);
      else if (page.outcome) setPhase({ at: "failed", why: page.detail || `The run ended ${page.outcome} with no draft.` });
      // The end is written a moment after its last event: ask again, a few times.
      else if (tries < 4) setTimeout(() => void ended(id, tries + 1), 800);
      else setPhase({ at: "failed", why: "The run left no draft." });
    } catch (e) {
      setPhase({ at: "failed", why: (e as Error).message });
    }
  };

  const follow = (id: string) => {
    source.current?.close();
    setPhase({ at: "running", run: id, line: "Starting…" });
    const s = new EventSource(`/api/runs/${encodeURIComponent(id)}/follow?${new URLSearchParams({ cwd })}`);
    source.current = s;
    s.onmessage = (m) => {
      const events = JSON.parse(m.data) as StepEvent[];
      setPhase((p) => (p.at === "running" ? { ...p, line: liveLine(events) } : p));
    };
    const over = () => (s.close(), void ended(id));
    for (const e of ["done", "status", "end", "cut"]) s.addEventListener(e, over);
  };

  const follows = run || kept?.run;
  useEffect(() => {
    if (follows && cwd) void ended(follows);
    return () => source.current?.close();
  }, [follows, cwd]);

  const draft = async (text: string) => {
    setPhase({ at: "running", run: "", line: "Starting…" });
    try {
      const said = await api.post<Started>("/api/agents/run", { cwd, key: "dagaz", text });
      if (keeps) keepDraft(want, cwd, { run: said.run, words: words.trim() });
      follow(said.run);
    } catch (e) {
      setPhase({ at: "failed", why: e instanceof ApiError && e.reasons.length ? e.reasons.join(" ") : (e as Error).message });
    }
  };
  const start = () => draft(`${words.trim()}\n\nWanted: ${want === "agent" ? "an agent" : "a process"}.`);
  const again = () => {
    if (keeps) keepDraft(want, cwd, null);
    setPhase({ at: "idle" });
  };

  if (phase.at === "drafted") {
    return (
      <div className="describe done">
        <Rune glyph="ᛞ" size={13} />
        <span className="grow">Drafted by Dagaz. Read every part, then save.</span>
        <button className="linkish" onClick={again}>Describe again</button>
      </div>
    );
  }
  if (phase.at === "asked") {
    const qs = phase.d.questions ?? [];
    return (
      <div className="describe" id="draft-questions">
        <span className="lab">
          <Rune glyph="ᛞ" size={12} /> Dagaz asks before it drafts
        </span>
        <div className="muted" style={{ fontSize: 13 }}>{phase.d.why}</div>
        {qs.map((q, i) => (
          <label key={q.n} className="f dq">
            <span>
              <b>{q.n}.</b> {q.text}
            </span>
            <textarea className="ta" rows={2} aria-label={`Your answer to question ${q.n}`} value={answers[i] ?? ""} onChange={(e) => setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))} />
            <span className="faint" style={{ fontSize: 12 }}>{answers[i] === q.recommendation ? "Dagaz's recommendation; change it if it is not what you want." : "Your answer."}</span>
          </label>
        ))}
        {phase.d.gaps && <Gaps cwd={cwd} run={phase.run} gaps={phase.d.gaps} />}
        <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button size="sm" kind="primary" disabled={answers.some((a) => !a.trim())} onClick={() => draft(answeredTask(`${words.trim()}\n\nWanted: ${want === "agent" ? "an agent" : "a process"}.`, qs, answers))}>
            Answer and draft
          </Button>
          <button className="linkish" onClick={again}>Describe again</button>
          <span className="faint" style={{ fontSize: 12 }}>{answers.some((a) => !a.trim()) ? "Answer every question to continue." : "One more paid run of at most $1.50 that saves nothing."}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="describe">
      <span className="lab">
        <Rune glyph="ᛞ" size={12} /> Describe the task
      </span>
      <textarea
        className="ta"
        rows={3}
        value={words}
        disabled={phase.at === "running"}
        placeholder={want === "agent" ? "For example: on request, read the interventions since the last run and propose at most two changes to the review skill." : "For example: docs changes go intent, then build, then review, then merge; no spec or plan."}
        onChange={(e) => setWords(e.target.value)}
      />
      {phase.at === "running" ? (
        <div className="describe-live" role="status">
          <Dot tone="live" /> Dagaz: {phase.line}
        </div>
      ) : (
        <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button size="sm" kind="primary" disabled={!words.trim()} onClick={start}>Draft it</Button>
          <span className="faint" style={{ fontSize: 12 }}>{words.trim() ? "One paid run of at most $1.50 that saves nothing; you save the draft." : "Write the task first. A draft is one paid run of at most $1.50 that saves nothing."}</span>
        </div>
      )}
      {phase.at === "failed" && (
        <div className="pe-refused" role="alert" id="draft-none">
          <b>No draft</b>
          <div>{phase.why}</div>
        </div>
      )}
    </div>
  );
}

export function NewAgent({ rows, catalog = [], cwd, run, onClose }: { rows: BuildAgent[]; catalog?: CatalogTool[]; cwd: string; run?: string; onClose: () => void }) {
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [glyph, setGlyph] = useState("");
  const [from, setFrom] = useState("");
  const [drafted, setDrafted] = useState<{ d: Drafted; run: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ byField: Partial<Record<NewAgentField, string[]>>; rest: string[] }>({ byField: {}, rest: [] });
  const shownKey = keyTouched ? key : slugKey(name);
  const problem = shownKey ? keyProblem(shownKey, rows.map((r) => r.key)) : null;
  const nameBad = name.trim() ? agentNameProblem(name.trim()) : null;
  const groups = [...new Set(rows.map(packTitle))];
  const agent = drafted?.d.agent;

  const take = (d: Drafted, id: string) => {
    setDrafted({ d, run: id });
    setSaid({ byField: {}, rest: [] });
    if (!d.agent) return;
    setName(String(d.agent.fields.name ?? ""));
    setKey(d.agent.key);
    setKeyTouched(true);
    setGlyph(String(d.agent.fields.glyph ?? ""));
    setFrom("");
  };

  const save = async () => {
    setBusy(true);
    setSaid({ byField: {}, rest: [] });
    try {
      const row = agent ? { row: { fields: { ...agent.fields, ...(glyph.trim() ? { glyph: glyph.trim() } : {}) }, body: agent.body } } : from ? { from } : {};
      await api.post<AgentPage>("/api/agents/new", { cwd, key: shownKey, name: name.trim(), ...row });
      if (!agent && glyph.trim()) await api.post<AgentPage>("/api/agents/field", { cwd, key: shownKey, field: "glyph", value: glyph.trim() });
      refreshPacks();
      keepDraft("agent", cwd, null);
      navigate(afterAgentSaved(drafted?.d ?? null, shownKey, drafted?.run ?? ""));
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
    <Dialog title="New agent" onClose={onClose} wide={Boolean(agent)}>
      <DescribeTask cwd={cwd} want="agent" run={run} onDraft={take} />
      {drafted && !agent && (
        <div className="callout amber">
          <span>
            Dagaz drafted a process, not an agent. <Link to={`/may-do?draft=${drafted.run}`}>Open it in the process editor</Link>.
          </span>
        </div>
      )}
      {agent && (
        <div className="callout accent why" id="draft-why">
          <span>
            <b>Why this agent</b>
            <br />
            {drafted.d.why}
          </span>
        </div>
      )}
      {!agent && <div className="or-line faint">or start it yourself</div>}
      <div className={agent ? "na-grid" : "na-stack"}>
        <Part label="Name" errors={[...(nameBad ? [nameBad] : []), ...(at("name") ?? [])]} hint="What the board calls it. Letters, digits and dashes.">
          <input className="input" autoFocus={!run} value={name} onChange={(e) => setName(e.target.value)} placeholder="Tidy" />
        </Part>
        <Part label="Key" errors={[...(problem ? [problem] : []), ...(at("key") ?? [])]} hint="Its address, as in /agents/tidy. It cannot change later.">
          <input className="input mono" value={shownKey} onChange={(e) => (setKeyTouched(true), setKey(e.target.value))} placeholder="tidy" />
        </Part>
        <Part label="Glyph" errors={at("glyph")} hint="One character for its icon. Optional.">
          <input className="input sm" maxLength={2} value={glyph} onChange={(e) => setGlyph(e.target.value)} />
        </Part>
      </div>
      {agent ? (
        <DraftedRow fields={agent.fields} body={agent.body} catalog={catalog}>
          {drafted.d.gaps && <Gaps cwd={cwd} run={drafted.run} gaps={drafted.d.gaps} />}
        </DraftedRow>
      ) : (
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
      )}
      {said.rest.length > 0 && (
        <div className="pe-refused" role="alert">
          <b>Not saved</b>
          <ul>{said.rest.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      <div className={`dlg-f${agent ? " sticky" : ""}`}>
        {agent && <span className="faint grow" style={{ fontSize: 12 }}>{drafted?.d.process ? "Saved as your own agent; its process opens next." : "Saved as your own agent; change any part on its page."}</span>}
        <Button kind="ghost" onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={busy || !ready} onClick={save}>{busy ? "Saving…" : agent ? "Save agent" : "Create agent"}</Button>
      </div>
      {!ready && (name || shownKey) && <div className="faint" style={{ fontSize: 12 }}>{!name.trim() ? "Give it a name to continue." : nameBad ? "Fix the name to continue." : "Fix the key to continue."}</div>}
    </Dialog>
  );
}

/**
 * What the task needs that the catalog lacks, each part with what the draft does without it, and a
 * press that puts it on Up next as a proposal to build that capability.
 */
export function Gaps({ cwd, run, gaps }: { cwd: string; run: string; gaps: DraftGap[] }) {
  const [made, setMade] = useState<Record<number, ProposalRow | string>>({});
  const ws = useResource("/api/workspaces").data?.workspaces.find((w) => w.path === cwd)?.name ?? "";
  // A gap proposed on an earlier visit shows as proposed: its proposal rests on this run and names the gap.
  const before = useResource("/api/proposals", { cwd }).data?.proposals.filter((p) => p.run === run) ?? [];
  const earlier = (g: DraftGap) => before.find((p) => p.problem.includes(`lacks a ${g.part}: ${g.need.split(/\s+/).join(" ")}.`));
  const propose = async (i: number) => {
    try {
      const p = await api.post<ProposalRow>("/api/proposals", { cwd, run, gap: i });
      setMade((m) => ({ ...m, [i]: p }));
    } catch (e) {
      setMade((m) => ({ ...m, [i]: e instanceof ApiError && e.reasons.length ? e.reasons.join(" ") : (e as Error).message }));
    }
  };
  return (
    <div className="gaps" id="draft-gaps">
      <b>What the catalog lacks for this task</b>
      {gaps.map((g, i) => {
        const m = made[i] ?? earlier(g);
        return (
          <div key={i} className="gap-row">
            <Chip square tone="amber">{GAP_PART[g.part] ?? g.part}</Chip>
            <div className="grow" style={{ minWidth: 0 }}>
              <div>{g.need}</div>
              {g.instead && <div className="faint" style={{ fontSize: 12 }}>Instead: {g.instead}</div>}
              {typeof m === "string" && <div className="field-err">{m}</div>}
            </div>
            {m && typeof m !== "string" ? (
              <span className="nowrap" style={{ fontSize: 12.5 }}>
                <Link to={`/up-next?ws=${encodeURIComponent(ws)}#proposal-${m.id}`}>Proposed #{m.id}, on Up next</Link>
              </span>
            ) : (
              <Button size="sm" onClick={() => void propose(i)}>Propose this capability</Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** A drafted row's parts, as a person checks them before saving: what it is built from (when, on what, what it hands back, its tools), what is missing, and its instructions. */
export function DraftedRow({ fields, body, catalog, children }: { fields: Record<string, unknown>; body: string; catalog: CatalogTool[]; children?: ReactNode }) {
  const tools = draftTools(fields, catalog);
  const alone = runsByItself(fields);
  return (
    <div className="drafted">
      {alone && (
        <div className={`callout ${fields.default === "on" ? "red" : "amber"}`} role="note">
          <span>{alone}</span>
        </div>
      )}
      {typeof fields.description === "string" && <div>{fields.description}</div>}
      <div className="kv">
        {draftParts(fields).map((p) => (
          <div key={p.label} style={{ display: "contents" }}>
            <span className="k">{p.label}</span>
            <span>{p.value}</span>
          </div>
        ))}
        <span className="k">Tools</span>
        <span>
          {tools.length ? (
            tools.map((t) => (
              <span key={t.name} className="drafted-tool">
                <b className="mono">{t.name}</b> <Chip square tone="plain">{EFFECT[t.effect] ?? t.effect}</Chip>
                {t.tier && <Chip square tone={TIER[t.tier] ?? "plain"}>{t.tier} risk</Chip>}
                {t.policy !== "allow" && <Chip square tone="amber">{t.policy}</Chip>}
              </span>
            ))
          ) : (
            <span className="faint">None: it reads only what it is handed.</span>
          )}
        </span>
      </div>
      {children}
      <details>
        <summary>Its instructions</summary>
        <pre className="drafted-body">{body}</pre>
      </details>
    </div>
  );
}
