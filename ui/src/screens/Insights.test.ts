import { describe, expect, it } from "vitest";
import { agentName } from "./Insights";

describe("a by-agent row", () => {
  it("shows the name the Agents page shows for the key", () => {
    const face = (key: string) => ({ build: { name: "Fehu", glyph: "F" } })[key as "build"] ?? { name: key, glyph: "?" };
    expect(agentName("build", face)).toBe("Fehu");
  });
});
