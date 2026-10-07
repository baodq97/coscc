import { describe, expect, it } from "vitest";
import { startedBy, ago, modelName, money, unitCode, unitTitle } from "./format";
import { match } from "./router";
import { findUnit, type PlacedUnit } from "./boards";
import { consequence, liveQuestions, runnable, unitState, type Unit } from "./model";
import { matches } from "./stream";
import { fill, readLines } from "./api";
import { FEATURE_UIS } from "./feature";
import { slugOf } from "../screens/NewWork";
import { inUnit, merged, runFacts, toolSummary } from "../screens/RunLog";
import { shortcut } from "../shell/Shell";
import { filterDecided } from "../screens/Decided";
import { noRuns } from "../screens/Insights";
import { moved } from "../screens/UpNext";
import { lastDays } from "../screens/Insights";
import { kinds } from "../../../coscc/features/release/ui/index";
import { attention, groupOf, pickWorkspace, triggerWords } from "../screens/Agents";
import { whenWords } from "../components/process";
import { runRow, changedParts, changes, get, modelOptions, put } from "../screens/AgentPage";
import { fieldLabel, isEmpty, itemLine } from "../screens/UnitPage";
import { inboxView } from "../screens/Inbox";
import { featureView } from "../screens/Feature";
import { filterUnits } from "../screens/Work";
import type { NextStep } from "../api.gen";
import type { AgentRow } from "../api.gen";
import { afterAgentSaved, draftOf, draftParts, draftTools, liveLine, runsByItself, unsavedAgent, walkChoices } from "./build";
import { asks, rerunFor, addStep, blankDraft, fieldOf, fieldOptions, fromProcess, keyProblem, missingInput, moveStep, reasonsByStep, renameStep, removeStep, setAgent, setStep, slugKey, toProcess } from "./build";

describe("format", () => {
  it("reads a model id as its family and version", () => {
    expect(modelName("claude-sonnet-5-5[1m]")).toBe("Sonnet 5.5");
    expect(modelName("claude-opus-5-5")).toBe("Opus 5.5");
    expect(modelName(null)).toBe("—");
  });

  it("gives money two decimals and nothing for no value", () => {
    expect(money(1119.31)).toBe("$1,119.31");
    expect(money(92, 0)).toBe("$92");
    expect(money(null)).toBe("—");
  });

  it("says how long ago, in the largest whole unit", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(ago("2026-10-04T11:59:40Z", now)).toBe("now");
    expect(ago("2026-10-04T11:15:00Z", now)).toBe("45m ago");
    expect(ago("2026-10-04T07:00:00Z", now)).toBe("5h ago");
    expect(ago("2026-10-01T12:00:00Z", now)).toBe("3d ago");
  });

  it("names a unit by a short code and a sentence", () => {
    expect(unitCode("coscc", "0162")).toBe("COS-162");
    expect(unitCode("nag-prototype", 8)).toBe("NAG-8");
    expect(unitTitle("0162_shipped-units-are-run-again")).toBe("Shipped units are run again");
  });
});

describe("findUnit", () => {
  it("takes a unit's number or its full name, in its own workspace only", () => {
    const u = { name: "0029_intent-writes", number: 29, workspace: { name: "coscc", path: "/w" } } as PlacedUnit;
    for (const n of ["29", "0029", "0029_intent-writes"]) expect(findUnit([u], "coscc", n)).toBe(u);
    for (const n of ["30", "0029_other", "29x", ""]) expect(findUnit([u], "coscc", n)).toBeUndefined();
    expect(findUnit([u], "other", "29")).toBeUndefined();
  });
});

describe("router", () => {
  it("matches a pattern and reads its parameters", () => {
    expect(match("/unit/:ws/:n", "/unit/coscc/162")).toEqual({ ws: "coscc", n: "162" });
    expect(match("/work", "/work")).toEqual({});
    expect(match("/work/:ws", "/work")).toBeNull();
    expect(match("/", "/")).toEqual({});
  });
});

describe("unit state", () => {
  const base: Unit = {
    name: "0001_x", number: 1, slug: "x", type: "fix", phase: "started", next_stage: "spec", why: "", open: 0,
    state: { state: "ready", label: "Ready", color: "gray" }, hold: null, pr: null, cost_usd: 0, at: "", updated: "",
    attention_reason: "", process: "p/full", missing: [], idea: "", rank: null, effort: null, paused: null,
  };

  it("reads a paused hold as paused", () => {
    expect(unitState({ ...base, hold: { state: "paused", by: "owner", date: "2026-10-04", reason: "" } }).group).toBe("Paused");
  });

  it("puts a unit with open questions under Needs you", () => {
    expect(unitState({ ...base, open: 2 })).toEqual({ group: "Needs you", label: "2 questions" });
  });

  it("says what the next stage lacks, under Needs you", () => {
    expect(unitState({ ...base, next_stage: "review", missing: ["impl.md"] })).toEqual({ group: "Needs you", label: "Needs impl.md" });
  });

  it("says a run held at its ceiling is paused at how much of it, under Needs you", () => {
    const at = { stage: "impl", code: "budget-reached", ceiling: "usd", usd: 8, max_usd: 8, turns: 61, max_turns: 250 };
    expect(unitState({ ...base, paused: at })).toEqual({ group: "Needs you", label: "Paused at $8.00 of $8.00" });
    expect(unitState({ ...base, paused: { ...at, ceiling: "turns", turns: 250 } }).label).toBe("Paused at 250 of 250 turns");
  });

  it("reads a finished unit as shipped and a pre-intent one as an idea", () => {
    expect(unitState({ ...base, why: "finished" }).group).toBe("Shipped");
    expect(unitState({ ...base, phase: "pre-intent" }).group).toBe("Ideas");
  });
});

describe("stream", () => {
  const change = { subject: "step.ended", workspace: "/w/a", unit: "0001_x" };

  it("matches a subject by prefix, and an empty prefix matches all", () => {
    expect(matches(change, ["step."])).toBe(true);
    expect(matches(change, ["answer."])).toBe(false);
    expect(matches(change, [""])).toBe(true);
  });

  it("keeps a screen to its workspace, and a change of no workspace reaches every screen", () => {
    expect(matches(change, ["step."], "/w/a")).toBe(true);
    expect(matches(change, ["step."], "/w/b")).toBe(false);
    expect(matches({ ...change, workspace: "" }, ["step."], "/w/b")).toBe(true);
  });
});

describe("api", () => {
  it("fills a route's path from the query and keeps the rest as a query", () => {
    expect(fill("/api/units/{name}", { name: "0001_x y", cwd: "/w/a" })).toEqual({ path: "/api/units/0001_x%20y", rest: { cwd: "/w/a" } });
    expect(fill("/api/units", { cwd: "/w/a" })).toEqual({ path: "/api/units", rest: { cwd: "/w/a" } });
  });
});

describe("new work", () => {
  it("names a unit from the owner's words in plain ASCII", () => {
    expect(slugOf("Sửa lỗi: board chậm!")).toBe("sua-loi-board-cham");
    expect(slugOf("  Đổi tên  ")).toBe("doi-ten");
  });
  it("cuts a long name at a word", () => {
    const name = slugOf("word ".repeat(30));
    expect(name.length).toBeLessThanOrEqual(60);
    expect(name.endsWith("word")).toBe(true);
  });
});

describe("run log", () => {
  it("reads a tool call as its command or file", () => {
    expect(toolSummary({ command: "npm test\nmore", description: "x" })).toBe("npm test");
    expect(toolSummary({ file_path: "a.py", old_string: "x" })).toBe("a.py");
  });
  it("reads a path into the unit's worktree from that worktree", () => {
    const unit = "0162_stale";
    expect(inUnit(`Read /home/x/.cos/worktrees/c-1/${unit}/coscc/a.py`, unit)).toBe("Read coscc/a.py");
    expect(inUnit(`grep -n x '/w/${unit}/b.py' /w/${unit}/c.py`, unit)).toBe("grep -n x 'b.py' c.py");
    expect(inUnit("/etc/hosts", unit)).toBe("/etc/hosts");
  });
  it("adds events in order, none twice", () => {
    const e = (seq: number) => ({ run: "r", seq, at: 0, kind: "text" });
    expect(merged([e(1), e(3)], [e(2), e(3)]).map((x) => x.seq)).toEqual([1, 2, 3]);
  });
});

describe("up next", () => {
  it("moves a unit within the shortlist and never out of it", () => {
    expect(moved(["a", "b", "c"], "c", -1)).toEqual(["a", "c", "b"]);
    expect(moved(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
    expect(moved(["a", "b", "c"], "a", 1)).toEqual(["b", "a", "c"]);
  });
});

describe("talk", () => {
  it("reads a reply line by line, a line split across chunks included", async () => {
    const parts = ['{"type":"chunk","text":"he', 'llo"}\n{"type":"tool","name":"Read"}\n', '{"type":"done","session_id":"s1"}'];
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        parts.forEach((p) => c.enqueue(new TextEncoder().encode(p)));
        c.close();
      },
    });
    const seen: unknown[] = [];
    await readLines(body, (l) => seen.push(l));
    expect(seen).toEqual([{ type: "chunk", text: "hello" }, { type: "tool", name: "Read" }, { type: "done", session_id: "s1" }]);
  });
});

describe("insights", () => {
  it("fills every day of the window, oldest first, a day with no spend at zero", () => {
    const days = lastDays([{ day: "2026-10-03", usd: 4 }, { day: "2026-10-01", usd: null }], 4, new Date(2026, 9, 4));
    expect(days).toEqual([
      { day: "2026-10-01", usd: 0 },
      { day: "2026-10-02", usd: 0 },
      { day: "2026-10-03", usd: 4 },
      { day: "2026-10-04", usd: 0 },
    ]);
  });
});

describe("release", () => {
  it("sums what a release gathers by kind, most first", () => {
    expect(kinds([{ type: "fix" }, { type: "feat" }, { type: "feat" }, { type: "" }])).toBe("2 feat, 1 fix, 1 other");
  });
});

describe("features", () => {
  it("finds the UI of each feature that has one, by its folder", () => {
    expect(Object.keys(FEATURE_UIS).sort()).toEqual(["notices", "release", "vault"]);
    expect(FEATURE_UIS.release.project).toBeTypeOf("function");
    expect(FEATURE_UIS.vault.page?.label).toBe("Vault");
    expect(FEATURE_UIS.vault.unit).toBeTypeOf("function");
    expect(FEATURE_UIS.notices.topbar).toBeTypeOf("function");
  });
});

describe("agents", () => {
  const row = (over: Partial<AgentRow>): AgentRow =>
    ({ key: "x", group: "stage", row: {}, builtin: {}, edited: [], problems: [], chip: "ok", ...over }) as AgentRow;

  it("sets and reads one value deep in a part, dropping what is left empty", () => {
    const model = { id: "a", effort: "low" };
    expect(get(model, ["effort"])).toBe("low");
    expect(put(model, ["effort"], "high")).toEqual({ id: "a", effort: "high" });
    expect(put({ novel: { model: { id: "a" } } }, ["novel", "model", "id"], undefined)).toBeUndefined();
    expect(put(undefined, ["novel", "ceilings", "turns"], 3)).toEqual({ novel: { ceilings: { turns: 3 } } });
  });

  it("sends only the parts the draft changed", () => {
    expect(changes({ model: { id: "a" }, body: "same" }, { model: { id: "b" }, body: "same" })).toEqual(["model"]);
  });

  it("says when an agent runs, and what needs a look first", () => {
    const helper = row({ key: "scout", group: "helper", row: { name: "Scout" } });
    const impl = row({ key: "impl", row: { name: "Uruz", trigger: { state: "impl" }, helpers: ["scout"] } });
    expect(triggerWords(impl)).toBe("on state impl");
    expect(triggerWords(helper, [impl, helper])).toBe("started by Uruz");
    expect(triggerWords(row({ row: { trigger: { engine: "chat" } } }))).toBe("when you talk to Leif");
    expect(triggerWords(row({ group: "triggered", row: { trigger: { schedule: { hours: 24 }, manual: true, leif: true } } }))).toBe("every 24 h, on request");
    expect(triggerWords(row({ group: "triggered", row: { trigger: { event: { name: "unit.shipped", after_hours: 168 }, manual: true } } }))).toBe("7 days after a ship, on request");
    expect(attention(row({ problems: ["bad"], chip: "failed" }))?.label).toBe("Cannot run");
    expect(attention(row({ chip: "costly" }))?.tone).toBe("amber");
    expect(attention(row({}))).toBeNull();
  });

  it("lists your periodic agent with the periodic ones, and one nothing runs under your pack", () => {
    const yours = (over: Partial<AgentRow>) => row({ pack: "local", ...over } as Partial<AgentRow>);
    expect(groupOf(yours({ group: "triggered", row: { trigger: { schedule: { hours: 24 } } } }))).toBe("triggered");
    expect(groupOf(yours({ group: "stage" }))).toBe("stage");
    expect(groupOf(yours({ group: "engine" }))).toBeNull();
    expect(groupOf(row({ group: "engine", row: { trigger: { engine: "chat" } } }))).toBe("engine");
  });

  it("reads the workspace the address names, else the one the draft's run is of, else the first", () => {
    const list = [{ name: "coscc", path: "/a" }, { name: "scratch", path: "/b" }];
    expect(pickWorkspace(list, "scratch", "")?.path).toBe("/b");
    expect(pickWorkspace(list, "", "/b")?.path).toBe("/b");
    expect(pickWorkspace(list, "", null)).toBeUndefined();
    expect(pickWorkspace(list, "", "")?.path).toBe("/a");
    expect(pickWorkspace(list, "gone", "")?.path).toBe("/a");
    expect(pickWorkspace([], "", "")).toBeUndefined();
  });
});

describe("a pack's choices", () => {
  const proc = (ref: string) => ({ ref, name: ref.split("/")[1] }) as never;
  const packs = [
    { name: "coscc-sdlc", on: true, own: false, process: "coscc-sdlc/full", processes: [proc("coscc-sdlc/full"), proc("coscc-sdlc/short")] },
    { name: "local", on: true, own: true, process: "coscc-sdlc/full", processes: [proc("local/changelog")] },
    { name: "extra", on: false, own: false, process: "coscc-sdlc/full", processes: [proc("extra/one")] },
  ];

  it("offers every process of every pack that is on in one select, showing the stored default", () => {
    const { stored, choices } = walkChoices(packs);
    expect(stored).toBe("coscc-sdlc/full");
    expect(choices.map((c) => c.ref)).toEqual(["coscc-sdlc/full", "coscc-sdlc/short", "local/changelog"]);
    expect(choices[2].label).toBe("changelog · yours");
  });

  it("keeps the stored default in view when its pack is off", () => {
    const off = packs.map((p) => ({ ...p, process: "extra/one" }));
    expect(walkChoices(off).choices.map((c) => c.ref)).toContain("extra/one");
  });

  it("drops the drafted agent once the loaded rows hold it", () => {
    const d = { why: "w", agent: { key: "changelog", fields: {}, body: "" } };
    expect(unsavedAgent(d, [{ key: "impl" }])?.key).toBe("changelog");
    expect(unsavedAgent(d, [{ key: "impl" }, { key: "changelog" }])).toBeUndefined();
    expect(unsavedAgent(d, [])).toBeUndefined();
  });
});

describe("whenWords", () => {
  it("reads a condition in plain words", () => {
    expect(whenWords({ field: "judgement", is: "ready" })).toBe("ready");
    expect(whenWords({ field: "unmeasured", is: "non-empty" })).toBe("unmeasured is non-empty");
    expect(whenWords({ guard: "ship-ready" })).toBe("ship ready");
    expect(whenWords([{ field: "judgement", is: "ready" }, { guard: "dependency-merged" }])).toBe("ready and dependency merged");
    expect(whenWords(undefined)).toBe("");
  });
});

describe("build: a new agent", () => {
  it("makes a key from words and says why a key will not do", () => {
    expect(slugKey("Add a Line!")).toBe("add-a-line");
    expect(slugKey("9 lives")).toBe("lives");
    expect(keyProblem("tidy", ["impl"])).toBeNull();
    expect(keyProblem("impl", ["impl"])).toMatch(/already/);
    expect(keyProblem("Tidy", [])).toMatch(/lowercase/);
    expect(keyProblem("a".repeat(25), [])).toMatch(/24/);
  });

  it("puts a refusal's reason beside its field", () => {
    expect(fieldOf("key tidy is taken")).toBe("key");
    expect(fieldOf("name must be text")).toBe("name");
    expect(fieldOf("ceilings.usd must be number")).toBeNull();
  });
});

describe("build: a process", () => {
  const impl = { key: "impl", row: { output: { kind: "artifact", fields: { judgement: { enum: ["ready", "not-ready"] }, "unmeasured?": { list: "U" } } } } } as unknown as AgentRow;

  it("lists what a way may ask of an output", () => {
    expect(fieldOptions(impl)).toEqual([
      { field: "judgement", values: ["ready", "not-ready"] },
      { field: "unmeasured", values: ["non-empty", "empty"] },
    ]);
    expect(fieldOptions(undefined)).toEqual([]);
  });

  it("builds steps into a process, each going on to the next", () => {
    let d = blankDraft("tiny");
    d = addStep(d, { agent: "intent" });
    d = addStep(d, { agent: "tidy" });
    d = addStep(d, { action: "open-pr" });
    d = addStep(d, { agent: "review" });
    d = addStep(d, { action: "merge" });
    const p = toProcess(d);
    expect(p.start).toBe("intent");
    expect(Object.keys(p.states)).toEqual(["intent", "tidy", "pr", "review", "ship"]);
    expect(p.states.intent.next).toEqual([{ to: "tidy" }]);
    expect(p.states.pr).toEqual({ action: "open-pr", next: [{ to: "review" }] });
    expect(p.states.ship).toEqual({ action: "merge", when: { guard: "ship-ready" } });
  });

  it("adds a way on with its condition, ahead of the otherwise", () => {
    let d = addStep(addStep(addStep(blankDraft("x"), { agent: "impl" }), { action: "open-pr" }), { agent: "review" });
    d = setStep(d, "review", { ways: [{ to: "impl", cond: { field: "verdict", is: "changes-requested" }, rest: [] }] });
    const p = toProcess(d);
    expect(p.states.review.next).toEqual([{ to: "impl", when: { field: "verdict", is: "changes-requested" } }]);
    d = setStep(d, "impl", { ways: [{ to: "review", cond: { guard: "fast-lane" }, rest: [] }] });
    expect(toProcess(d).states.impl.next).toEqual([{ to: "review", when: { guard: "fast-lane" } }, { to: "pr" }]);
  });

  it("reads a process back into the steps it was built from", () => {
    const d = addStep(addStep(addStep(blankDraft("x"), { agent: "intent" }), { agent: "impl" }), { agent: "review" });
    const d2 = setStep(d, "review", { ways: [{ to: "impl", cond: { field: "verdict", is: "changes-requested" }, rest: [] }] });
    const p = toProcess(d2);
    expect(toProcess(fromProcess("x", p))).toEqual(p);
    expect(fromProcess("x", p).steps.map((s) => [s.key, s.then, s.ways.length])).toEqual([["intent", true, 0], ["impl", true, 0], ["review", false, 1]]);
  });

  it("removes a step with the ways that led to it, and moves one", () => {
    let d = addStep(addStep(addStep(blankDraft("x"), { agent: "a" }), { agent: "b" }), { agent: "c" });
    d = setStep(d, "c", { ways: [{ to: "a", cond: null, rest: [] }] });
    const gone = removeStep(d, "a");
    expect(gone.steps.map((s) => s.key)).toEqual(["b", "c"]);
    expect(gone.steps[1].ways).toEqual([]);
    const moved = moveStep(d, "c", -1);
    expect(moved.steps.map((s) => s.key)).toEqual(["a", "c", "b"]);
    expect(toProcess(moved).states.c.next).toEqual([{ to: "a" }, { to: "b" }]);
    expect(toProcess(moved).states.b.next).toBeUndefined();
    expect(moveStep(d, "a", -1)).toBe(d);
  });

  it("keeps a second copy of an agent apart and follows a changed agent", () => {
    let d = addStep(addStep(blankDraft("x"), { agent: "impl" }), { agent: "impl" });
    expect(d.steps.map((s) => s.key)).toEqual(["impl", "impl-2"]);
    d = setAgent(d, "impl-2", "review");
    expect(d.steps.map((s) => [s.key, s.agent])).toEqual([["impl", "impl"], ["review", "review"]]);
  });

  it("puts a check's reasons beside the step they name", () => {
    const got = reasonsByStep(["tiny.ship: a review state is not on every path to it", "tiny.review.next[0]: 'x' is no state", "tiny: no path reaches the end", "local/tiny.tidy: its input intent is not produced on every path to it"], ["intent", "tidy", "review", "ship"]);
    expect(got.byStep.ship).toEqual(["a review state is not on every path to it"]);
    expect(got.byStep.review).toEqual(["'x' is no state"]);
    expect(got.byStep.tidy).toHaveLength(1);
    expect(got.rest).toEqual(["tiny: no path reaches the end"]);
  });

  it("renames a step with the ways to it, and says which input is missing", () => {
    let d = addStep(addStep(addStep(blankDraft("x"), { agent: "a" }), { agent: "tidy" }), { agent: "review" });
    d = setStep(d, "review", { ways: [{ to: "tidy", cond: null, rest: [] }] });
    const r = renameStep(d, "tidy", "impl");
    expect(r.steps.map((s) => s.key)).toEqual(["a", "impl", "review"]);
    expect(r.steps[2].ways[0].to).toBe("impl");
    expect(renameStep(d, "tidy", "a")).toBe(d);
    expect(renameStep(d, "tidy", "Bad Name")).toBe(d);
    expect(missingInput("tiny.review: its input impl is not produced on every path to it")).toBe("impl");
    expect(missingInput("tiny: no path reaches the end")).toBeNull();
  });
});

describe("Dagaz's draft fills the forms", () => {
  const agent = {
    key: "tidy",
    body: "Read the interventions.",
    fields: {
      name: "Tidy",
      model: { id: "claude-sonnet-5-5[1m]", effort: "low" },
      tools: { Read: "allow", Bash: "ask" },
      input: { artifacts: [], outputs: [], answers: false, findings: false, data: ["interventions"] },
      output: { kind: "proposal", version: 1, fields: {} },
      trigger: { manual: true, leif: true },
      ceilings: { turns: 4, usd: 0.3 },
    },
  };
  const process = {
    name: "docs",
    process: {
      start: "intent",
      end: "shipped",
      states: {
        intent: { agent: "intent", next: [{ to: "impl" }] },
        impl: { agent: "impl", next: [{ to: "pr", when: { field: "judgement", is: "ready" } }] },
        pr: { action: "open-pr", next: [{ to: "review" }] },
        review: { agent: "review", next: [{ to: "impl", when: { field: "verdict", is: "changes-requested" } }, { to: "ship" }] },
        ship: { action: "merge", when: { guard: "ship-ready" } },
      },
    },
  };

  it("reads a draft from the run's end, and nothing from an end without one", () => {
    expect(draftOf({ draft: { why: "w", agent } })?.agent?.key).toBe("tidy");
    expect(draftOf({ draft: { why: "w" } })).toBeNull();
    expect(draftOf({})).toBeNull();
    expect(draftOf({ draft: { why: "w", agent: { key: "x" } } })).toBeNull();
  });

  it("a drafted process becomes the editor's steps in walk order", () => {
    const d = draftOf({ draft: { why: "w", process } });
    const steps = fromProcess(d!.process!.name, d!.process!.process);
    expect(steps.name).toBe("docs");
    expect(steps.steps.map((s) => s.key)).toEqual(["intent", "impl", "pr", "review", "ship"]);
    expect(toProcess(steps).states.ship).toEqual(process.process.states.ship);
  });

  it("a drafted row reads as the parts a person checks", () => {
    const parts = Object.fromEntries(draftParts(agent.fields).map((p) => [p.label, p.value]));
    expect(parts.Runs).toBe("when you press Run, when Leif asks");
    expect(parts.Reads).toBe("interventions");
    expect(parts["Hands back"]).toBe("proposals for Up next");
    expect(parts.Ceilings).toBe("$0.30 a run, 4 turns");
    const tools = draftTools(agent.fields, [{ name: "Read", effect: "read", tier: "low", server: "", feature: "", on: true }]);
    expect(tools).toEqual([
      { name: "Read", policy: "allow", effect: "read", tier: "low" },
      { name: "Bash", policy: "ask", effect: "not in the catalog", tier: "" },
    ]);
  });

  it("every remaining part of a drafted row is shown", () => {
    const parts = Object.fromEntries(
      draftParts({ ...agent.fields, default: "on", cwd: "trunk", helpers: ["scout"], skills: ["write-intent"], variants: { novel: { model: { id: "claude-opus-5-5" } } }, warning: "Paid.", output: { kind: "artifact", by: "app" } }).map((p) => [p.label, p.value]),
    );
    expect(parts.Default).toMatch(/^on: runs by itself/);
    expect(parts["Works in"]).toMatch(/trunk/);
    expect(parts.Helpers).toBe("scout");
    expect(parts.Skills).toBe("write-intent");
    expect(parts.Variants).toMatch(/^novel: /);
    expect(parts.Warning).toBe("Paid.");
    expect(parts["Written by"]).toBe("the app, from its reply");
  });

  it("a row with its own schedule says so, louder when it starts on", () => {
    expect(runsByItself(agent.fields)).toBe("");
    expect(runsByItself({ trigger: { schedule: { hours: 24 } }, default: "on" })).toMatch(/^Runs by itself every 24 h, paid/);
    expect(runsByItself({ trigger: { event: { name: "unit.shipped" } }, default: "off" })).toMatch(/starts off/);
  });

  it("saving an agent drafted beside a process goes back to the process", () => {
    expect(afterAgentSaved({ why: "w", agent, process }, "tidy", "r1")).toBe("/may-do?draft=r1");
    expect(afterAgentSaved({ why: "w", agent }, "tidy", "r1")).toBe("/agents/tidy");
  });

  it("the live line says what the run is doing", () => {
    expect(liveLine([])).toBe("Starting…");
    expect(liveLine([{ kind: "tool_use", name: "mcp__cos__submit" }])).toMatch(/checks/);
    expect(liveLine([{ kind: "tool_result", is_error: true }])).toMatch(/refused/);
    expect(liveLine([{ kind: "end" }])).toBe("Finished.");
  });
});

describe("screens", () => {
  it("call every hook on every render: none after a ?, : or && on its line", () => {
    const sources = import.meta.glob<string>(["../screens/*.tsx", "../components/*.tsx", "../shell/*.tsx"], { query: "?raw", import: "default", eager: true });
    for (const [file, text] of Object.entries(sources)) {
      const bad = text.split("\n").filter((l) => /(\?|&&|\|\||[^:]:)\s*use[A-Z]\w*\(/.test(l.replace(/\/\/.*/, "")));
      expect(bad, file).toEqual([]);
    }
  });

  it("names a changed part as the page does, not by its record's key", () => {
    const saved = { model: { id: "a", effort: "low" }, ceilings: { turns: 5, usd: 1 } };
    expect(changedParts({ model: { id: "a", effort: "high" } }, saved)).toEqual(["effort"]);
    expect(changedParts({ ceilings: { turns: 9, usd: 2 }, model: { id: "b", effort: "low" } }, saved)).toEqual(["turns", "spend", "model"]);
    expect(changedParts({ "skill:impl": "x" }, { "skill:impl": "y" })).toEqual(["impl skill"]);
  });
});

describe("process steps that ask", () => {
  const agent = (key: string, fields: Record<string, unknown>) => ({ key, row: { output: { fields } } }) as unknown as AgentRow;
  const rows = [agent("intent", { questions: {}, "judgement?": {} }), agent("review", { verdict: {} })];
  const packs = [{ processes: [{ states: { impl: { agent: "impl", rerun: ["answers"] }, spec: { agent: "spec", rerun: ["fresh", "answers"] } } }] }] as unknown as Parameters<typeof rerunFor>[2];

  it("starts an asking agent's step on answers, and a built-in's own ways when the packs have them", () => {
    expect(asks(rows[0])).toBe(true);
    expect(rerunFor("intent", rows, [])).toEqual(["answers"]);
    expect(rerunFor("review", rows, [])).toEqual([]);
    expect(rerunFor("spec", rows, packs)).toEqual(["fresh", "answers"]);
  });

  it("keeps the ways a rerun may go through a copy of a process", () => {
    const d = addStep(blankDraft("x"), { agent: "intent" }, rerunFor("intent", rows, []));
    const p = toProcess(d);
    expect(p.states.intent.rerun).toEqual(["answers"]);
    expect(toProcess(fromProcess("y", p)).states.intent.rerun).toEqual(["answers"]);
    expect(toProcess(addStep(blankDraft("x"), { agent: "review" })).states.review.rerun).toBeUndefined();
  });

  it("says nowhere yet for an agent no state or trigger runs", () => {
    expect(triggerWords({ key: "mine", group: "engine", row: {} } as unknown as AgentRow)).toBe("nowhere yet");
  });
});
describe("shortcuts", () => {
  it("goes to Leif on G then L, and does not toggle the panel", () => {
    expect(shortcut("g", false)).toEqual({ act: "arm" });
    expect(shortcut("l", true)).toEqual({ go: "/leif" });
    expect(shortcut("l", false)).toEqual({ act: "leif" });
    expect(shortcut("?", false)).toEqual({ act: "help" });
  });
});

describe("a run's header", () => {
  it("reads outcome, cost, turns, time, model and starter", () => {
    const e = (o: object) => ({ run: "r", seq: 1, at: 0, ...o }) as never;
    const facts = runFacts([e({ kind: "config", model: "claude-sonnet-4-5", effort: "high" }), e({ kind: "result", cost_usd: 1.5, num_turns: 12, duration_ms: 125000 })], { status: "ended", outcome: "done", started_by: "autopilot" });
    expect(Object.fromEntries(facts.map((f) => [f.label, f.value]))).toMatchObject({ Outcome: "done", Cost: "$1.50", Turns: "12", Took: "2 min 5 s", "Started by": "the autopilot" });
    expect(runFacts([], { status: "running" })[0].value).toBe("running");
  });
});

describe("the decisions filter", () => {
  const row = (unit: string, text: string) => ({ unit, text, question: "", name: "Leif", artifact: "spec", n: 1, date: "" }) as never;
  it("keeps the rows with every word", () => {
    const rows = [row("0001_a", "use sqlite"), row("0002_b", "use postgres")];
    expect(filterDecided(rows, "use SQLITE")).toHaveLength(1);
    expect(filterDecided(rows, "  ")).toHaveLength(2);
    expect(filterDecided(rows, "0002")).toHaveLength(1);
  });
});

describe("by-agent rows with no run", () => {
  it("say why", () => expect(noRuns({ steps: 3 })).toMatch(/3 of its steps.*no run to open/));
});
describe("work and needs you", () => {
  const unit = (over: Record<string, unknown>) => ({ why: "", phase: "started", open: 2, hold: null, missing: [], paused: null, next_stage: "spec", ...over }) as unknown as Unit;
  const next = (over: Partial<NextStep>) => ({ stage: "spec", blocked: false, reasons: [], ...over }) as NextStep;

  it("counts no open questions on a dropped or shipped unit", () => {
    expect(liveQuestions(unit({}))).toBe(2);
    expect(liveQuestions(unit({ why: "dropped" }))).toBe(0);
    expect(liveQuestions(unit({ why: "finished" }))).toBe(0);
    expect(liveQuestions(unit({ why: "dropped" }), 1)).toBe(0);
  });

  it("offers the run for a stage whose only reason is missing", () => {
    expect(runnable(next({}))).toBe(true);
    expect(runnable(next({ blocked: true, reasons: ["missing"] }))).toBe(true);
    expect(runnable(next({ blocked: true, reasons: ["missing", "waiting-on"] }))).toBe(false);
    expect(runnable(next({ blocked: true, reasons: [] }))).toBe(false);
    expect(runnable(next({ gate: "closed" }))).toBe(false);
    expect(runnable(next({ stage: null }))).toBe(false);
    expect(runnable(undefined)).toBe(false);
  });

  it("says what a run is: model, effort and ceilings", () => {
    const c = { model: "claude-sonnet-5-5", effort: "medium", ceilings: { max_turns: 40, max_budget_usd: 3 } } as never;
    expect(consequence(c)).toBe("Runs Sonnet 5.5 at medium effort, at most 40 turns and $3.00. Spends account quota.");
  });

  it("reads an output field plainly and leaves empty ones out", () => {
    expect(fieldLabel("rests_on")).toBe("Rests on");
    expect(fieldLabel("judgement")).toBe("Judgement");
    expect([[], "", null, {}, { a: [] }].every(isEmpty)).toBe(true);
    expect(["x", ["x"], 0].some(isEmpty)).toBe(false);
  });

  it("says an unknown inbox item is not found, with or without other items", () => {
    expect(inboxView(3, true, false)).toBe("missing");
    expect(inboxView(0, true, false)).toBe("missing");
    expect(inboxView(0, false, false)).toBe("empty");
    expect(inboxView(3, true, true)).toBe("list");
    expect(inboxView(3, false, false)).toBe("list");
  });

  it("reads list items of an output as one line", () => {
    expect(itemLine({ n: 1, text: "When?", recommendation: "Today" })).toBe("1. When? \u2014 recommends: Today");
    expect(itemLine({ n: 2, text: "Why?", recommendation: "" })).toBe("2. Why?");
    expect(itemLine({ id: "U1", verdict: "holds", rests_on: [] })).toBe("U1 \u2014 Verdict: holds");
    expect(itemLine({ path: "a.py", note: "x" })).toBe("Path: a.py \u00b7 Note: x");
  });

  it("filters work by every word in the title or the code", () => {
    const u = (name: string, number: number) => ({ workspace: { name: "coscc" }, number, name });
    const list = [u("0077_a-newer-database", 77), u("0018_command-filter", 18)];
    expect(filterUnits(list, "").length).toBe(2);
    expect(filterUnits(list, "DATABASE newer").map((x) => x.number)).toEqual([77]);
    expect(filterUnits(list, "cos-18").map((x) => x.number)).toEqual([18]);
    expect(filterUnits(list, "zzz")).toEqual([]);
  });

  it("ends a feature page's wait when the answer cannot come", () => {
    expect(featureView(false, false, "error", false)).toBe("none");
    expect(featureView(false, false, "ready", false)).toBe("none");
    expect(featureView(false, false, "loading", false)).toBe("wait");
    expect(featureView(false, true, "ready", false)).toBe("wait");
    expect(featureView(false, false, "ready", true)).toBe("wait");
    expect(featureView(true, true, "loading", false)).toBe("page");
  });

  it("names a model as a person reads it and keeps an id the list lacks", () => {
    expect(modelOptions("claude-sonnet-5-5[1m]", "Default").find((o) => o.value === "claude-sonnet-5-5[1m]")?.label).toBe("Sonnet 5.5");
    expect(modelOptions("my-model", "Default").map((o) => o.value)).toContain("my-model");
    expect(modelOptions(undefined, "Default")[0]).toEqual({ value: "", label: "Default" });
  });
});

describe("runRow", () => {
  const run = { workspace: "/w", unit: "", outcome: "done", at: "", turns: null, cost_usd: null, row_hash: "", run: "", skipped: false, detail: "", started_by: "" };
  it("opens the log of a unitless run and names who started it", () => {
    expect(runRow({ ...run, run: "abc", started_by: "leif" }, "ws")).toMatchObject({ to: "/run/ws/abc", title: "Run by Leif" });
    expect(runRow({ ...run, run: "abc", started_by: "schedule" }, "ws").title).toBe("Run by the schedule");
  });
  it("makes a skip a muted line, not a link", () => {
    expect(runRow({ ...run, skipped: true, detail: "nothing new" }, "ws")).toMatchObject({ to: "", title: "Skipped — nothing new", muted: true });
  });
  it("says a run with no kept log has none", () => {
    expect(runRow(run, "ws")).toMatchObject({ to: "", title: "Run — no log kept", muted: true });
  });
  it("sends a unit's run without a log to its unit", () => {
    expect(runRow({ ...run, unit: "0007_a-thing" }, "ws").to).toBe("/unit/ws/7");
  });
});

describe("startedBy", () => {
  it("says who in plain words", () => {
    expect(["manual", "person", "leif", "schedule", "event", "autopilot", "", null].map(startedBy)).toEqual(["you", "you", "Leif", "the schedule", "an event", "the autopilot", "", ""]);
  });
});
