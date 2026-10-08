import { describe, expect, it, vi } from "vitest";
import { failureWords, until, startedBy, ago, mdBlocks, mdSpans, modelName, money, toolName, unitCode, unitTitle } from "./format";
import { match } from "./router";
import { findUnit, needsYou, failedLink, proposalLink, readBoard, onBoardRead, boardsOf, NOT_WAITED, type PlacedUnit } from "./boards";
import { consequence, liveQuestions, runnable, unitState, type Unit } from "./model";
import { matches } from "./stream";
import { api, fill, readLines } from "./api";
import { FEATURE_UIS } from "./feature";
import { slugOf } from "../screens/NewWork";
import { inUnit, merged, runFacts, toolLines, toolSummary } from "../screens/RunLog";
import { shortcut } from "../shell/Shell";
import { noRuns } from "../screens/Insights";
import { moved } from "../screens/UpNext";
import { lastDays } from "../screens/Insights";
import { kinds } from "../../../coscc/features/release/ui/index";
import { onWords } from "../screens/AgentActivity";
import { statusWords, attention, groupOf, pickWorkspace, triggerWords } from "../screens/Agents";
import { whenWords } from "../components/process";
import { facesOf } from "./pack";
import type { PackShown } from "../api.gen";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Inline } from "../components/ui";
import { submitWords } from "../screens/RunLog";
import { runCount } from "../screens/AgentActivity";
import { proposingWords } from "../components/Proposals";
import { builtinOf, chainTo, changedParts, unsavedWords, changes, errorIsHere, get, modelOptions, plainReasons, put, savedApart } from "../screens/AgentPage";
import { runRow, resultWords, shallowWords } from "../screens/AgentActivity";
import { fieldLabel, isEmpty, itemLine } from "../screens/UnitPage";
import { inboxView } from "../screens/Inbox";
import { featureView } from "../screens/Feature";
import { filterUnits } from "../screens/Work";
import type { NextStep } from "../api.gen";
import type { AgentRow } from "../api.gen";
import { afterAgentSaved, answersText, draftOf, draftParts, draftTools, keepDraft, keptDraft, liveLine, runsByItself, unsavedAgent, walkChoices } from "./build";
import { asks, rerunFor, addStep, blankDraft, fieldOf, fieldOptions, fromProcess, keyProblem, missingInput, moveStep, reasonsByStep, renameStep, removeStep, setAgent, setStep, slugKey, toProcess } from "./build";
import { sandboxed, sandboxLine, sandboxOf, nameTaken } from "./build";

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
    attention_reason: "", process: "p/full", missing: [], idea: "", rank: null, effort: null, paused: null, waiting: null,
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
    expect(toolLines({ command: "P=http://x\ncurl $P\njq .\n\n" })).toEqual({ first: "P=http://x", more: 2, full: "P=http://x\ncurl $P\njq ." });
    expect(toolLines({ command: "ls" }).more).toBe(0);
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
    // A trigger and its default are one write; the rest one write each.
    expect(savedApart(["default", "model", "trigger"])).toEqual(["model", "trigger"]);
    expect(savedApart(["default", "model"])).toEqual(["default", "model"]);
    const chained = chainTo({ schedule: { hours: 24 }, manual: true }, "telemetry-audit");
    expect(chained.trigger).toEqual({ schedule: { hours: 24 }, manual: true, event: { name: "agent-run.ended", from: "telemetry-audit" } });
    expect(chained.default).toBe("off");
    expect(chainTo(chained.trigger, "", "on")).toEqual({ trigger: { schedule: { hours: 24 }, manual: true }, default: "on" });
    // A refusal naming one part of the trigger shows under that part alone.
    const circle = "trigger.event.from: Laguz runs after Echo runs after Laguz";
    expect(errorIsHere(circle, "trigger", "trigger.event.from")).toBe(true);
    expect(errorIsHere(circle, "trigger", "trigger.schedule.hours")).toBe(false);
    expect(errorIsHere("default: a row another agent starts is off", "default", "default")).toBe(true);
    expect(plainReasons(`${circle}: agents cannot start each other in a circle; default: off until you turn it on`)).toBe(
      "Laguz runs after Echo runs after Laguz: agents cannot start each other in a circle; off until you turn it on",
    );
    expect(plainReasons("skills: Kenaz runs a stage of your process, so it needs at least one skill")).toBe("Kenaz runs a stage of your process, so it needs at least one skill");
    expect(plainReasons("tools.Write: no such tool in the catalog")).toBe("tools.Write: no such tool in the catalog");
  });

  it("says when an agent runs, and what needs a look first", () => {
    const helper = row({ key: "scout", group: "helper", row: { name: "Scout" } });
    const impl = row({ key: "impl", row: { name: "Uruz", trigger: { state: "impl" }, helpers: ["scout"] } });
    expect(triggerWords(impl)).toBe("when a unit reaches impl");
    expect(triggerWords(helper, [impl, helper])).toBe("started by Uruz");
    expect(triggerWords(row({ row: { trigger: { engine: "chat" } } }))).toBe("when you talk to Leif");
    expect(triggerWords(row({ group: "triggered", row: { trigger: { schedule: { hours: 24 }, manual: true, leif: true } } }))).toBe("daily, when you or Leif ask");
    expect(triggerWords(row({ group: "triggered", row: { trigger: { event: { name: "unit.shipped", after_hours: 168 }, manual: true } } }))).toBe("7 days after a ship, when you or Leif ask");
    const laguz = row({ key: "telemetry-audit", group: "triggered", row: { name: "Laguz", trigger: { schedule: { hours: 24 } } } });
    const after = (event: object) => row({ group: "triggered", row: { trigger: { event: { name: "agent-run.ended", ...event }, manual: true } } });
    expect(triggerWords(after({ from: "telemetry-audit" }), [laguz])).toBe("after Laguz, when you or Leif ask");
    expect(triggerWords(after({ from: "telemetry-audit", after_hours: 2 }), [laguz])).toBe("2 h after Laguz ends, when you or Leif ask");
    expect(triggerWords(after({ from: "gone" }), [laguz])).toBe("after gone, when you or Leif ask");
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

  it("a draft that asks first is read with its questions, and its gaps with it", () => {
    const questions = [{ n: 1, text: "Which code?", recommendation: "All of it" }];
    const gaps = [{ part: "trigger", need: "every morning", instead: "every 24 h" }];
    const d = draftOf({ draft: { why: "w", questions, gaps } });
    expect(d?.questions).toEqual(questions);
    expect(d?.gaps).toEqual(gaps);
    expect(d?.agent).toBeUndefined();
    expect(draftOf({ draft: { why: "w", agent, gaps } })?.gaps).toEqual(gaps);
    expect(draftOf({ draft: { why: "w", questions: [] } })).toBeNull();
  });

  it("answers go back under their questions, an empty answer as the recommendation", () => {
    const qs = [
      { n: 1, text: "Which code?", recommendation: "All of it" },
      { n: 2, text: "When?", recommendation: "Weekly" },
    ];
    expect(answersText(qs, ["Only coscc/", " "])).toBe("1. Which code?\n   Only coscc/\n2. When?\n   Weekly");
  });

  it("a kept draft is per project until it is set aside", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) });
    keepDraft("agent", "/a", { run: "r1", words: "tell me" });
    expect(keptDraft("agent", "/a")).toEqual({ run: "r1", words: "tell me" });
    expect(keptDraft("agent", "/b")).toBeNull();
    expect(keptDraft("process", "/a")).toBeNull();
    keepDraft("agent", "/a", null);
    expect(keptDraft("agent", "/a")).toBeNull();
    localStorage.setItem("coscc.draft.agent./a", "{not json");
    expect(keptDraft("agent", "/a")).toBeNull();
    vi.unstubAllGlobals();
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

  it("waits on no read that counts runs before any screen shows: agents' names come with the packs", () => {
    const sources = import.meta.glob<string>(["./pack.tsx", "../shell/*.tsx"], { query: "?raw", import: "default", eager: true });
    expect(Object.keys(sources).length).toBeGreaterThan(1);
    for (const [file, text] of Object.entries(sources)) expect(text.includes('"/api/agents"'), file).toBe(false);
  });

  it("names a changed part as the page does, not by its record's key", () => {
    const saved = { model: { id: "a", effort: "low" }, ceilings: { turns: 5, usd: 1 } };
    expect(changedParts({ model: { id: "a", effort: "high" } }, saved)).toEqual(["effort"]);
    expect(changedParts({ ceilings: { turns: 9, usd: 2 }, model: { id: "b", effort: "low" } }, saved)).toEqual(["turns", "spend", "model"]);
    const tr = { trigger: { event: { name: "e", from: "a" } }, default: "on" };
    const was = { trigger: { event: { name: "f", from: "b" } }, default: "off" };
    expect(changedParts(tr, was)).toEqual(["starts after", "on or off by default"]);
    expect(unsavedWords(tr, was)).toBe("2 unsaved changes: starts after, on or off by default");
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
    expect(runFacts([e({ kind: "end", outcome: "cancelled" })], { status: "running" })[0].value).toBe("cancelled");
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
    expect(runnable(next({ blocked: true, reasons: ["review-incomplete"] }))).toBe(true);
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
  const run = { workspace: "/w", unit: "", outcome: "done", at: "", turns: null, cost_usd: null, row_hash: "", run: "", skipped: false, detail: "", started_by: "", made: null, session: false, verdict: "", refused: null, helpers: null, shallow: false };
  it("opens the log of a unitless run and names who started it", () => {
    expect(runRow({ ...run, run: "abc", started_by: "leif" }, "ws")).toMatchObject({ to: "/run/ws/abc", title: "Run by Leif" });
    expect(runRow({ ...run, run: "abc", started_by: "schedule" }, "ws").title).toBe("Run by the schedule");
  });
  it("makes a skip a muted line, not a link", () => {
    expect(runRow({ ...run, skipped: true, detail: "nothing new" }, "ws")).toMatchObject({ to: "", title: "Skipped — nothing new", muted: true });
  });
  it("says a run with no kept log has none", () => {
    expect(runRow(run, "ws")).toMatchObject({ to: "", title: "An earlier run — no log kept", muted: true });
  });
  it("sends a unit's run without a log to its unit", () => {
    expect(runRow({ ...run, unit: "0007_a-thing" }, "ws").to).toBe("/unit/ws/7");
  });
});

describe("builtinOf", () => {
  const skill = (over: object) => ({ name: "write-spec", text: "mine", builtin: "", edited: false, ...over });
  it("keeps an unedited part as the built-in's, so a reset of it is a no-op", () => {
    const got = builtinOf({ row: { model: { id: "m" }, ceilings: { usd: 5 } }, builtin: { ceilings: { usd: 2 } }, edited: ["ceilings"], skills: [skill({}), skill({ name: "b", text: "new", builtin: "old", edited: true })] } as never);
    expect(got.model).toEqual({ id: "m" });
    expect(got.ceilings).toEqual({ usd: 2 });
    expect(got["skill:write-spec"]).toBe("mine");
    expect(got["skill:b"]).toBe("old");
  });
  it("leaves out a part the owner added that the built-in lacks", () => {
    const got = builtinOf({ row: { description: "mine" }, builtin: {}, edited: ["description"], skills: [] } as never);
    expect("description" in got).toBe(false);
  });
});

describe("what a run made", () => {
  const run = { made: null, verdict: "", refused: null, shallow: false } as unknown as Parameters<typeof resultWords>[0];
  it("says proposed N, a verdict, or nothing", () => {
    expect(resultWords({ ...run, made: 2 })).toBe("proposed 2");
    expect(resultWords({ ...run, made: 0 })).toBe("proposed nothing");
    expect(resultWords({ ...run, verdict: "not-met" })).toBe("verdict: not met");
    expect(resultWords(run)).toBe("");
  });
  it("flags a run that was refused calls or left a criterion unclear", () => {
    expect(shallowWords({ ...run, refused: 1 })).toBe("1 call was refused");
    expect(shallowWords({ ...run, refused: 3 })).toBe("3 calls were refused");
    expect(shallowWords({ ...run, verdict: "unclear" })).toBe("left a criterion unclear");
    expect(shallowWords(run)).toBe("");
  });
});

describe("startedBy", () => {
  it("says who in plain words", () => {
    expect(["manual", "person", "leif", "schedule", "event", "autopilot", "", null].map(startedBy)).toEqual(["you", "you", "Leif", "the schedule", "an event", "the autopilot", "", ""]);
  });
});

describe("a sandboxed Bash", () => {
  it("reads as what it reaches and where it writes, and edits back into the row's form", () => {
    const box = sandboxOf({ sandbox: { network: ["127.0.0.1:3000"] } });
    expect(box).toEqual(["127.0.0.1:3000"]);
    expect(sandboxLine(box ?? [])).toBe("Bash, sandboxed: network 127.0.0.1:3000; writes its scratch folder only.");
    expect(sandboxLine([])).toBe("Bash, sandboxed: network none; writes its scratch folder only.");
    expect(sandboxOf("allow")).toBeNull();
    expect(sandboxOf({ sandbox: {} })).toEqual([]);
    expect(sandboxed("127.0.0.1:3000, localhost:9090 ")).toEqual({ sandbox: { network: ["127.0.0.1:3000", "localhost:9090"] } });
    expect(sandboxed("")).toEqual({ sandbox: { network: [] } });
    const [t] = draftTools({ tools: { Bash: { sandbox: { network: ["127.0.0.1:3000"] } } } }, []);
    expect(t.policy).toBe("Bash, sandboxed: network 127.0.0.1:3000; writes its scratch folder only.");
  });
});

describe("until and statusWords", () => {
  const now = Date.parse("2026-10-07T10:00:00Z");
  it("says when something comes", () => {
    expect(until("2026-10-07T09:59:00Z", now)).toBe("due");
    expect(until("2026-10-07T10:12:00Z", now)).toBe("in 12 min");
    expect(until("2026-10-07T15:00:00Z", now)).toBe("in 5 h");
    expect(until("2026-10-09T10:00:00Z", now)).toBe("in 2 d");
  });
  const row = { on: true, on_in: ["a", "b"], off_reason: "", last: null, next_at: null } as unknown as AgentRow;
  it("tells on, last and next in one line", () => {
    const said = statusWords({ ...row, last: { at: "", made: 3 } as AgentRow["last"], next_at: "2999-01-01T00:00:00Z" }, "a");
    expect(said).toMatch(/^On here \(also on in b\) · ran .*, last run proposed 3 · next in \d+ d$/);
  });
  it("says why an agent is off and that it never ran", () => {
    expect(statusWords({ ...row, on: false, on_in: [], off_reason: "a run stopped at its ceiling" }, "a")).toBe("Off here: a run stopped at its ceiling · never ran");
  });
  it("tells a stage agent's last run and says it is always on", () => {
    expect(statusWords({ ...row, on: null }, "a")).toBe("Always on · never ran");
    expect(statusWords({ ...row, on: null, last: { at: "", made: null } as AgentRow["last"] }, "a")).toMatch(/^Always on · ran /);
  });
  it("says a failed or stopped last run as it ended, and the tile's line has no 'never ran' mid-line", () => {
    const failed = { ...row, last: { at: "", made: null, outcome: "failed" } as AgentRow["last"], next_at: "2999-01-01T00:00:00Z" };
    expect(statusWords(failed, "a")).toMatch(/ · failed .* · next in/);
    expect(onWords({ ...row, next_at: "2999-01-01T00:00:00Z" }, "a")).toMatch(/^On here \(also on in b\) · next in \d+ d$/);
    expect(attention({ ...failed, problems: [], chip: "failed", running: { run: "r", started: "" } } as unknown as AgentRow)).toBeNull();
  });
  it("says running now, not never ran or next, while the first run is in flight", () => {
    const said = statusWords({ ...row, running: { run: "r", started: "" }, next_at: "2999-01-01T00:00:00Z" }, "a");
    expect(said).toBe("On here (also on in b) · running now");
  });
});

describe("failedLink", () => {
  it("opens the run, or the agent's page in its workspace when no log was kept", () => {
    expect(failedLink({ workspace: "my proj", agent: "scan", run: "r1" })).toBe("/run/my%20proj/r1");
    expect(failedLink({ workspace: "my proj", agent: "scan", run: "" })).toBe("/agents/scan?ws=my%20proj");
  });
});

describe("proposalLink", () => {
  it("opens the proposal on its project's Up next", () => {
    expect(proposalLink({ workspace: "my proj", id: 31 })).toBe("/up-next?ws=my%20proj#proposal-31");
  });
});

describe("needsYou", () => {
  const unit = (over: object) => ({ why: "", phase: "full", open: 0, ...over }) as unknown as PlacedUnit;
  it("counts a failed agent run beside the proposals", () => {
    expect(needsYou([], [1, 2], [{}]).total).toBe(3);
  });
  it("counts the units that need a person and the proposals once, for every screen", () => {
    const got = needsYou([unit({ open: 2 }), unit({ paused: { at: "impl" } }), unit({}), unit({ why: "dropped", open: 1 })], ["p1", "p2", "p3"]);
    expect(got.units.length).toBe(2);
    expect(got.total).toBe(5);
  });
});

describe("matches with something left out", () => {
  const run = { subject: "agent-run.started", workspace: "w", agent: "a", run: "r" };
  it("leaves out a subject but never the reconnect replay", () => {
    expect(matches(run, [""], "", ["agent-run."])).toBe(false);
    expect(matches({ subject: "step.ended", workspace: "w", unit: "u", going_down: false }, [""], "", ["agent-run."])).toBe(true);
    expect(matches({ subject: "", workspace: "" }, [""], "", ["agent-run.", "chat-turn."])).toBe(true);
  });
});

describe("an agent's markdown and tool names", () => {
  it("reads emphasis, code and only safe links", () => {
    const line = "**#31** uses `step_events`, see [run](/run/proj/a) or [x](javascript:alert(1))";
    const spans = mdSpans(line);
    expect(spans.slice(0, 5)).toEqual([
      { kind: "b", text: "#31" },
      { kind: "text", text: " uses " },
      { kind: "code", text: "step_events" },
      { kind: "text", text: ", see " },
      { kind: "link", text: "run", href: "/run/proj/a" },
    ]);
    // A link anywhere but the app or the web stays words.
    expect(spans.slice(5).every((s) => s.kind === "text")).toBe(true);
    expect(spans.slice(5).map((s) => s.text).join("")).toBe(" or [x](javascript:alert(1))");
  });
  it("groups lines into headings, lists, paragraphs and code", () => {
    const blocks = mdBlocks("## Why\n- one\n- two\n\nText\nmore\n```\ncode **x**\n```\n1. first");
    expect(blocks.map((b) => b.kind)).toEqual(["h", "ul", "p", "pre", "ol"]);
    expect(blocks[1].lines).toHaveLength(2);
    expect(blocks[3].code).toBe("code **x**");
  });
  it("names a tool by its own name", () => {
    expect(toolName("mcp__cos__proposals")).toBe("proposals");
    expect(toolName("mcp__code-graph__explore")).toBe("explore");
    expect(toolName("Read")).toBe("Read");
  });
});

describe("submitWords and inline marks", () => {
  it("says a submit in plain words", () => {
    expect(submitWords({ proposals: [] })).toBe("handed back 0 proposals");
    expect(submitWords({ proposals: [{ a: 1 }] })).toBe("handed back 1 proposal");
    expect(submitWords({ why: "x" })).toBe("handed back its result");
  });
  it("shows code inside bold as code, with no backticks left", () => {
    const html = renderToStaticMarkup(createElement(Inline, { text: "**`GET /api/units`:** and `x`" }));
    expect(html).toBe("<b><code>GET /api/units</code><span>:</span></b><span> and </span><code>x</code>");
  });
});

describe("runCount", () => {
  it("counts skipped runs apart, in the group as in the tile", () => {
    const runs = [...Array(12).fill({ skipped: false }), ...Array(3).fill({ skipped: true })];
    expect(runCount(runs)).toBe("12 runs, 3 skipped");
    expect(runCount(12, 3)).toBe("12 runs, 3 skipped");
    expect(runCount([{ skipped: false }])).toBe("1 run");
  });
});

describe("proposingWords", () => {
  it("says a follower starts after its leader, naming the leader's state", () => {
    const base = { key: "echo", name: "Echo", on: false, after: "Laguz", after_on: false };
    expect(proposingWords(base)).toBe("starts after Laguz (off here)");
    expect(proposingWords({ ...base, after_on: true })).toBe("starts after Laguz");
    expect(proposingWords({ ...base, after: "", after_on: null })).toBe("off here, runs when you press Run now");
  });
});

describe("failureWords", () => {
  it("says a killed process plainly and keeps the raw text apart", () => {
    const raw = "the session failed: Command failed with exit code 143 (exit code: 143) Error output: Check stderr output for details";
    const got = failureWords(raw);
    expect(got.plain).toBe("The agent's process was stopped (exit 143) before it finished");
    expect(got.raw).toBe(raw);
  });
  it("says a signal exit plainly, negative or shifted", () => {
    for (const n of [-9, -15, 137, 143]) {
      const raw = `the session failed: Command failed with exit code ${n} (exit code: ${n})`;
      expect(failureWords(raw).plain).toBe(`The agent's process was stopped (exit ${n}) before it finished`);
    }
    expect(failureWords("exit code: 1").plain).toContain("broke off");
  });
  it("leaves a detail with no exit code as it is", () => {
    expect(failureWords("the ceiling was reached")).toEqual({ plain: "the ceiling was reached", raw: "" });
  });
});

describe("api.get", () => {
  it("shares one request between two reads of the same address in flight", async () => {
    const fetched = vi.fn(async () => new Response(JSON.stringify({ units: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetched);
    const [a, b] = await Promise.all([api.get("/api/units", { cwd: "/w" }), api.get("/api/units", { cwd: "/w" })]);
    expect(fetched).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    await api.get("/api/units", { cwd: "/w" });
    expect(fetched).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });
});

describe("the boards in view", () => {
  const ws = (path: string) => ({ name: path.slice(1), path, label: path.slice(1), source: "store" as const, missing: false });
  const [a, b] = [ws("/w/a"), ws("/w/b")];

  it("share one read of each board between the parts of the page asking at once", async () => {
    const done: ((v: { units: [] }) => void)[] = [];
    const get = vi.spyOn(api, "get").mockImplementation((() => new Promise((r) => done.push(r))) as never);
    // The sidebar and the open screen each read every board after the same burst of events.
    const readers = [a, b].flatMap((w) => [readBoard(w), readBoard(w)]);
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls.map((c) => c[1])).toEqual([{ cwd: "/w/a" }, { cwd: "/w/b" }]);
    done.forEach((r) => r({ units: [] }));
    const got = await Promise.all(readers);
    expect(got[0]).toBe(got[1]);
    // A settled read is dropped: the next burst reads again.
    const again = readBoard(a);
    expect(get).toHaveBeenCalledTimes(3);
    done[2]({ units: [] });
    await again;
    get.mockRestore();
  });

  it("reads the board a board.read names at once, and leaves it out of the 400 ms wait", async () => {
    vi.useFakeTimers();
    let emit: (data: string) => void = () => {};
    vi.stubGlobal(
      "EventSource",
      class {
        constructor() {
          emit = (data) => (this as unknown as { onmessage: (m: { data: string }) => void }).onmessage({ data });
        }
        addEventListener() {}
        close() {}
      },
    );
    const get = vi.spyOn(api, "get").mockResolvedValue({ units: [] } as never);
    const got = vi.fn();
    const stop = onBoardRead([a, b], got);
    emit(JSON.stringify({ subject: "board.read", workspace: "/w/b" }));
    emit(JSON.stringify({ subject: "step.ended", workspace: "/w/a", unit: "0001_x" }));
    await vi.advanceTimersByTimeAsync(0);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/api/units", { cwd: "/w/b" });
    expect(got).toHaveBeenCalledWith({ workspace: b, board: { units: [] } });
    // A path listed otherwise than the app resolved it names no board: each is read.
    emit(JSON.stringify({ subject: "board.read", workspace: "/real/b" }));
    await vi.advanceTimersByTimeAsync(0);
    expect(get).toHaveBeenCalledTimes(3);
    stop();
    get.mockRestore();
    vi.unstubAllGlobals();
    vi.useRealTimers();

    const read = { subject: "board.read", workspace: "/w/b" };
    expect(matches(read, [""], "", NOT_WAITED)).toBe(false);
    expect(matches({ subject: "step.ended", workspace: "/w/b", unit: "0001_x" }, [""], "", NOT_WAITED)).toBe(true);
    expect(matches({ subject: "", workspace: "" }, [""], "", NOT_WAITED)).toBe(true);
  });

  it("reads once more after a read in flight when a board.read comes, and shares that read", async () => {
    const done: ((v: { units: string[] }) => void)[] = [];
    const get = vi.spyOn(api, "get").mockImplementation((() => new Promise((r) => done.push(r))) as never);
    // The 400 ms read after a step ended is in flight while the held read it started ends.
    const before = readBoard(a);
    const [ready, also] = [readBoard(a, true), readBoard(a, true)];
    expect(also).toBe(ready);
    expect(get).toHaveBeenCalledTimes(1);
    done[0]({ units: ["held before"] });
    expect((await before).board).toEqual({ units: ["held before"] });
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    done[1]({ units: ["read after"] });
    expect((await ready).board).toEqual({ units: ["read after"] });
    // With none in flight, a board.read reads at once.
    const now = readBoard(a, true);
    expect(get).toHaveBeenCalledTimes(3);
    done[2]({ units: [] });
    await now;
    get.mockRestore();
  });

  it("hands a reader waiting on a slow board the last answer of each, not the one it began with", async () => {
    const done = new Map<string, ((v: { units: string[] }) => void)[]>();
    const get = vi.spyOn(api, "get").mockImplementation(((_: string, q: { cwd: string }) =>
      new Promise((r) => done.set(q.cwd, [...(done.get(q.cwd) ?? []), r]))) as never);
    // A tick reads both boards; b is slow. A board.read of a reads a again once the tick's read of a ends.
    const tick = Promise.all([readBoard(a), readBoard(b)]);
    const ready = readBoard(a, true);
    done.get("/w/a")![0]({ units: ["old"] });
    await vi.waitFor(() => expect(done.get("/w/a")).toHaveLength(2));
    done.get("/w/a")![1]({ units: ["new"] });
    await ready;
    done.get("/w/b")![0]({ units: [] });
    await tick;
    expect(boardsOf([a, b]).map((x) => x.board)).toEqual([{ units: ["new"] }, { units: [] }]);
    get.mockRestore();
  });
});

describe("a name another agent has", () => {
  const rows = [{ key: "pr-review", row: { name: "Tiwaz" } }, { key: "x", row: {} }];
  it("is refused before the save, without case", () => {
    expect(nameTaken("tiwaz", rows)).toMatch(/another agent's name/);
    expect(nameTaken("Tidy", rows)).toBeNull();
    expect(nameTaken("", rows)).toBeNull();
  });
  it("is not taken by the row it names", () => {
    expect(nameTaken("Tiwaz", rows, "pr-review")).toBeNull();
  });
});

describe("facesOf", () => {
  const sdlc = {
    agents: [{ key: "estimate", name: "Berkanan", glyph: "ᛒ" }],
    app_agents: [{ key: "pr", name: "Ansuz", glyph: "ᚨ" }, { key: "ship", name: "Othala", glyph: "ᛟ" }],
  } as PackShown;
  it("names the stages the app runs and the rows by their runes", () => {
    const faces = facesOf([sdlc]);
    expect([faces.pr?.name, faces.ship?.name, faces.estimate?.name]).toEqual(["Ansuz", "Othala", "Berkanan"]);
    expect(faces.pr?.glyph).toBe("ᚨ");
  });
});
