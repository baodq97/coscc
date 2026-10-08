import { describe, expect, it } from "vitest";
import type { PackShown } from "../api.gen";
import { isEngineStage, runWords } from "./UnitPage";

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
