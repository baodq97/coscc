import { describe, expect, it } from "vitest";
import { summary } from "../../../coscc/features/release/ui/index";

const unit = (name: string, type: string) => ({ name, type, pr: null, sha: "a".repeat(40), subject: name });
const commit = (subject: string) => ({ sha: "b".repeat(40), subject });

describe("the release card's summary", () => {
  it("names the units and the commits without a unit", () => {
    const units = [unit("a", "feat"), unit("b", "feat"), unit("c", "fix"), unit("d", "fix")];
    const unmatched = [commit("feat: agents and packs (idea 0006) (#259)"), commit("feat: agents at work (idea 0007) (#260)")];
    const { text, lines } = summary({ count: 4, units, unmatched, last_tag: "v0.15.0" });
    expect(text).toBe("4 units (2 feat, 2 fix) and 2 commits without a unit since v0.15.0");
    expect(lines).toEqual(unmatched.map((c) => c.subject));
    expect(lines.join("\n")).toContain("#259");
    expect(lines.join("\n")).toContain("#260");
    expect(lines.join("\n")).not.toContain("b".repeat(40));
  });

  it("says one unit and one commit in the singular, and leaves out what is none", () => {
    expect(summary({ count: 1, units: [unit("a", "fix")], unmatched: [commit("x (#1)")], last_tag: "v1" }).text).toBe(
      "1 unit (1 fix) and 1 commit without a unit since v1",
    );
    expect(summary({ count: 3, units: [unit("a", "fix"), unit("b", "fix"), unit("c", "fix")], unmatched: [], last_tag: "v1" }).text).toBe(
      "3 units (3 fix) since v1",
    );
    expect(summary({ count: 0, units: [], unmatched: [commit("x (#1)"), commit("y (#2)")], last_tag: "v1" }).text).toBe(
      "0 units and 2 commits without a unit since v1",
    );
  });
});
