import { describe, expect, it } from "vitest";
import type { PackShown, Waiting } from "../api.gen";
import { isEngineStage, runWords, waitingWhy } from "./UnitPage";

const packs = [
  {
    processes: [
      {
        ref: "core/feature",
        states: { build: { agent: "builder" }, pr: { action: "open-pr" }, ship: { action: "merge" } },
      },
    ],
  },
] as unknown as PackShown[];

describe("the run button's words", () => {
  it("reads a state with an action as the engine's, with no session or quota in it", () => {
    expect(isEngineStage(packs, "core/feature", "pr")).toBe(true);
    const w = runWords("Pull request", true);
    for (const text of [w.button, w.confirm, w.note]) {
      expect(text).not.toMatch(/Claude session/i);
      expect(text).not.toMatch(/quota/i);
    }
    expect(w.note).toContain("without an agent");
  });
  it("keeps the session wording for a state an agent runs", () => {
    expect(isEngineStage(packs, "core/feature", "build")).toBe(false);
    const w = runWords("Build", false);
    expect(w.note).toBe("Runs a real Claude session and spends account quota.");
    expect(w.confirm).toBe("Spend quota on Build?");
    expect(w.button).toBe("Run Build");
  });
  it("finds the state in any pack when the unit's process is in none", () => {
    expect(isEngineStage(packs, "gone/proc", "ship")).toBe(true);
    expect(isEngineStage(packs, "gone/proc", "nothing")).toBe(false);
  });
});

describe("the waiting line", () => {
  const now = Date.parse("2026-01-01T10:00:00Z");
  const w = { code: "session-limit", why: "The account reached its session limit.", moves_it: "The autopilot runs it again once the limit resets.", until: "" } as Waiting;

  it("says why as it is with no reset", () => {
    expect(waitingWhy(w, now)).toBe("The account reached its session limit.");
  });
  it("adds the reset as a relative time when there is one", () => {
    expect(waitingWhy({ ...w, until: "2026-01-01T12:00:00+00:00" }, now)).toBe("The account reached its session limit; it resets in 2 h.");
    expect(waitingWhy({ ...w, until: "2025-12-31T10:00:00Z" }, now)).toBe("The account reached its session limit; it resets now.");
  });
  it("leaves out a reset it cannot read", () => {
    expect(waitingWhy({ ...w, until: "soon" }, now)).toBe(w.why);
  });
});
