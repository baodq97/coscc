import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// An effect written `useEffect(() => call(), …)` returns what `call` returns; when that is a
// Promise (`scrollIntoView` in newer browsers), React calls it as the cleanup and the page goes white.
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !name.includes(".test.") ? [path] : [];
  });
}

describe("effects", () => {
  it("never return the value of an expression", () => {
    const roots = [join(__dirname, ".."), join(__dirname, "../../../coscc/features")];
    const bad = roots
      .flatMap(sources)
      .flatMap((p) => readFileSync(p, "utf8").split("\n").map((line, i) => [p, i + 1, line] as const))
      .filter(([, , line]) => /use(Layout)?Effect\(\(\) => [^{(]/.test(line))
      .map(([p, n]) => `${p}:${n}`);
    expect(bad).toEqual([]);
  });
});
