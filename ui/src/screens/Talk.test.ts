import { describe, expect, it } from "vitest";
import type { ChatSession } from "../api.gen";
import { showHint, talkRows } from "./Talk";

const mine = (id: string, summary: string): ChatSession => ({ session_id: id, summary, last_modified: 1, created_at: null, git_branch: null, resumable: true });

describe("the side list while the first message is sent", () => {
  it("has a row for it, so the hint is gone", () => {
    const rows = talkRows([], null, "hi");
    expect(rows).toHaveLength(1);
    expect(rows[0].summary).toBe("hi");
    expect(showHint(rows, null, true)).toBe(false);
  });
  it("keeps one row, the real one, once the turn is done", () => {
    const real = mine("s1", "hi");
    expect(talkRows([real], real, null)).toEqual([real]);
    expect(talkRows([], real, null)).toEqual([real]);
  });
  it("shows the hint only with nothing to list, no session and nothing sent", () => {
    expect(showHint(talkRows([], null, null), null, false)).toBe(true);
    expect(showHint([], null, true)).toBe(false);
    expect(showHint([], mine("s1", "x"), false)).toBe(false);
  });
});
