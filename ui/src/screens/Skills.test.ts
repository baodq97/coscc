import { describe, expect, it } from "vitest";
import { skillProblem } from "./Skills";
import { toggleSkill } from "../components/SkillPicker";

describe("a new skill", () => {
  it("is refused before saving for a bad name, empty text or past 16 KB", () => {
    expect(skillProblem("", "x")).toMatch(/name/);
    for (const bad of ["../x", "A", "a/b", "-x", "x".repeat(41)]) expect(skillProblem(bad, "x")).toMatch(/lowercase/);
    expect(skillProblem("ok", "  ")).toMatch(/Write/);
    expect(skillProblem("ok", "é".repeat(8001))).toMatch(/16 KB/);
    expect(skillProblem("x".repeat(40), "x".repeat(16_000))).toBe("");
  });
});

describe("the skill picker", () => {
  it("adds at the end, removes, and never names one twice", () => {
    expect(toggleSkill(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleSkill(["a", "b"], "a")).toEqual(["b"]);
    expect(toggleSkill(toggleSkill([], "a"), "a")).toEqual([]);
  });
});
