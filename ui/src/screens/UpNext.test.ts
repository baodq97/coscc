import { describe, expect, it } from "vitest";
import type { UpNext } from "../api.gen";
import { showSaved } from "./UpNext";

const record = { by: "owner", reason: "0001 taken off" } as UpNext["shortlist_record"];

describe("the last-saved line", () => {
  it("is hidden when the shortlist is empty", () => {
    expect(showSaved({ shortlist: [], shortlist_record: record })).toBe(false);
  });
  it("shows with one item and a record, not without a record", () => {
    const one = [{ unit: "0001_a" }] as UpNext["shortlist"];
    expect(showSaved({ shortlist: one, shortlist_record: record })).toBe(true);
    expect(showSaved({ shortlist: one, shortlist_record: null })).toBe(false);
  });
});
