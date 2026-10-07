import { describe, expect, it } from "vitest";
import { skillProblem, usesWords, whose } from "./Skills";
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

describe("a skill's line", () => {
  it("says where it comes from in words", () => {
    expect(whose({ own: true, builtin: false, edited: false, pack: "local" })).toBe("Yours");
    expect(whose({ own: false, builtin: true, edited: false, pack: "coscc-sdlc" })).toBe("Built in");
    expect(whose({ own: false, builtin: true, edited: true, pack: "coscc-sdlc" })).toBe("Built in, edited by you");
    expect(whose({ own: false, builtin: false, edited: false, pack: "audits" })).toBe("audits");
  });
  it("counts its uses", () => {
    const now = Date.parse("2026-10-07T12:00:00Z");
    expect(usesWords({ uses_30d: 0, last_used: "" }, "", now)).toBe("Not counted yet");
    expect(usesWords({ uses_30d: 0, last_used: "" }, "2026-10-07T10:00:00Z", now)).toBe("No use since Oct 7");
    expect(usesWords({ uses_30d: 1, last_used: "2026-10-07T11:00:00Z" }, "2026-10-07T10:00:00Z", now)).toMatch(/^1 use since Oct 7, last /);
    expect(usesWords({ uses_30d: 2, last_used: "2026-10-07T11:00:00Z" }, "2026-08-01T00:00:00Z", now)).toMatch(/^2 uses in 30 days, last /);
  });
});
