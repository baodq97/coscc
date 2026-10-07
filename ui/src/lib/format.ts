// How numbers, times and names read on screen: no raw ids, no model strings.

import type { Paused } from "../api.gen";

export function money(x: number | null | undefined, digits = 2): string {
  if (x == null) return "—";
  return "$" + x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const minutes = Math.round((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** When something comes: "due", "in 12 min", "in 5 h", "in 2 d". */
export function until(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const minutes = Math.round((Date.parse(iso) - now) / 60_000);
  if (minutes < 1) return "due";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} d`;
}

/** `claude-sonnet-5-5[1m]` reads "Sonnet 5.5". */
export function modelName(model: string | null | undefined): string {
  if (!model) return "—";
  const m = model.match(/claude-(\w+)-(\d+)-(\d+)/);
  return m ? `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : model;
}

/** A unit's short code: `COS-162`, from the workspace name and the unit number. */
export function unitCode(workspace: string, number: number | string): string {
  const prefix = workspace.replace(/[^a-z]/gi, "").slice(0, 3).toUpperCase() || "U";
  return `${prefix}-${Number(number)}`;
}

/** `0162_shipped-units-are-run-again` reads "Shipped units are run again". */
export function unitTitle(slug: string): string {
  const words = slug.replace(/^\d+_/, "").replace(/-/g, " ");
  return words ? words[0].toUpperCase() + words.slice(1) : slug;
}

/** What a card says of a run held at a ceiling: `Paused at $1.00 of $2.00`, or `Paused at 40 of 40 turns`. */
export function pausedAt(p: Paused): string {
  return p.ceiling === "turns" ? `Paused at ${p.turns ?? "?"} of ${p.max_turns ?? "?"} turns` : `Paused at ${money(p.usd)} of ${money(p.max_usd)}`;
}

const WHO: Record<string, string> = { manual: "you", person: "you", leif: "Leif", schedule: "the schedule", event: "an event", autopilot: "the autopilot" };

/** Who started a run, in plain words ("you", "Leif", "the schedule"); `""` when unknown. */
export function startedBy(by: string | null | undefined): string {
  return by ? (WHO[by] ?? by) : "";
}

/** A failed run's `detail` in one plain line: "(exit 143)" says the agent's process was stopped (a signal), other
 * exits that it broke off; any other detail is shown as it is. The raw text stays in `raw`. */
export function failureWords(detail: string | null | undefined): { plain: string; raw: string } {
  const raw = (detail ?? "").trim();
  const code = /exit code[: ]+(\d+)/i.exec(raw)?.[1];
  if (!code) return { plain: raw, raw: "" };
  const n = Number(code);
  const plain = n > 128 ? `The agent's process was stopped (exit ${n}) before it finished` : `The agent's process broke off (exit ${n}) before it finished`;
  return { plain, raw };
}

/** `mcp__cos__proposals` reads "proposals": a tool by its own name, not its server's. */
export function toolName(name: string): string {
  return name.replace(/^mcp__[\w-]+?__/, "");
}

/** One run of text in a line of markdown: plain, bold, italic, code, or a link. */
export type MdSpan = { kind: "text" | "b" | "i" | "code" | "link"; text: string; href?: string };
/** One block of markdown: a paragraph, a heading, a list, or code. */
export type MdBlock = { kind: "p" | "h" | "ul" | "ol" | "pre"; lines: MdSpan[][]; code?: string };

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)|\*[^*\s][^*\n]*\*)/g;

/** The spans of one line. A link goes only to the app (`/…`) or the web (`http(s)://…`). */
export function mdSpans(line: string): MdSpan[] {
  const out: MdSpan[] = [];
  let at = 0;
  for (const m of line.matchAll(INLINE)) {
    if (m.index > at) out.push({ kind: "text", text: line.slice(at, m.index) });
    const s = m[0];
    if (s.startsWith("**")) out.push({ kind: "b", text: s.slice(2, -2) });
    else if (s.startsWith("`")) out.push({ kind: "code", text: s.slice(1, -1) });
    else if (s.startsWith("[")) {
      const [, text, href] = s.match(/^\[([^\]]+)\]\(([^)]+)\)$/) ?? [];
      out.push(/^(\/(?!\/)|https?:\/\/)/.test(href ?? "") ? { kind: "link", text, href } : { kind: "text", text: s });
    } else out.push({ kind: "i", text: s.slice(1, -1) });
    at = m.index + s.length;
  }
  if (at < line.length) out.push({ kind: "text", text: line.slice(at) });
  return out;
}

/** Markdown as blocks: what an agent writes (headings, lists, code, emphasis), never HTML. */
export function mdBlocks(text: string): MdBlock[] {
  const blocks: MdBlock[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      while (++i < lines.length && !lines[i].startsWith("```")) code.push(lines[i]);
      blocks.push({ kind: "pre", lines: [], code: code.join("\n") });
      continue;
    }
    const list = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.*)$/);
    const kind = !line.trim() ? null : /^#{1,6}\s/.test(line) ? "h" : list ? (list[1] ? "ul" : "ol") : "p";
    if (!kind) continue;
    const body = kind === "h" ? line.replace(/^#+\s+/, "") : list ? list[2] : line;
    const last = blocks[blocks.length - 1];
    if (last && last.kind === kind && kind !== "h" && lines[i - 1]?.trim()) last.lines.push(mdSpans(body));
    else blocks.push({ kind, lines: [mdSpans(body)] });
  }
  return blocks;
}
