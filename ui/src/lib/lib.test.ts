import { describe, expect, it } from "vitest";
import { ago, modelName, money, unitCode, unitTitle } from "./format";
import { match } from "./router";
import { unitState, type Unit } from "./model";
import { matches } from "./stream";
import { fill } from "./api";
import { slugOf } from "../screens/NewWork";
import { merged, toolSummary } from "../screens/RunLog";
import { moved } from "../screens/UpNext";

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
    attention_reason: "", idea: "", repo: "", rank: null, effort: null,
  };

  it("reads a paused hold as paused", () => {
    expect(unitState({ ...base, hold: { state: "paused", by: "owner", date: "2026-10-04", reason: "" } }).group).toBe("Paused");
  });

  it("puts a unit with open questions under Needs you", () => {
    expect(unitState({ ...base, open: 2 })).toEqual({ group: "Needs you", label: "2 questions" });
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
