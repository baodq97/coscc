// One process drawn as a diagram: its states in walk order down the main line, each with its agent
// (glyph and name) or the engine's action, and every other way on as a labelled arrow. Plain SVG.

import { useId } from "react";
import type { Condition, ProcessShown, State } from "../api.gen";
import { Rune } from "../lib/icons";
import { agentFace, stageLabel } from "../lib/pack";

const NODE_W = 176;
const NODE_H = 42;
const GAP = 30;
const ROW = NODE_H + GAP;
const LANE0 = NODE_W + 22;
const LANE_STEP = 30;
const SIDE_W = 138;

const ACTIONS: Record<string, string> = { "open-pr": "Opens the pull request", merge: "Merges it" };

/** A `when` in words: `ready`, `unmeasured is non-empty`, `ship-ready`; a list reads "and". */
export function whenWords(when: State["when"]): string {
  const list: Condition[] = when === undefined ? [] : Array.isArray(when) ? when : [when];
  return list
    .map((c) => (c.guard ? c.guard.replace(/-/g, " ") : c.is === "non-empty" || c.is === "empty" ? `${c.field} is ${c.is}` : (c.is ?? "")))
    .join(" and ");
}

type Edge = { from: string; to: string; label: string };

function walk(p: ProcessShown): { main: string[]; side: string[]; edges: Edge[] } {
  const main: string[] = [];
  for (let at = p.start; at && !main.includes(at); ) {
    main.push(at);
    const ways = p.states[at]?.next ?? [];
    at = ways.length ? ways[ways.length - 1].to : "";
  }
  const edges = Object.entries(p.states).flatMap(([from, st]) =>
    (st.next ?? []).map((w, i, all) => ({ from, to: w.to, label: whenWords(w.when) || (i < all.length - 1 || !main.includes(w.to) ? "otherwise" : "") })),
  );
  return { main, side: Object.keys(p.states).filter((k) => !main.includes(k)), edges };
}

export function ProcessDiagram({ process, current, done = [] }: { process: ProcessShown; current?: string; done?: string[] }) {
  const id = useId();
  const { main, side, edges } = walk(process);
  const y = (k: string) => main.indexOf(k) * ROW;
  const mid = (k: string) => y(k) + NODE_H / 2;

  // A side state sits beside the main states that lead to it or come back from it.
  const sideY: Record<string, number> = {};
  for (const s of side) {
    const near = edges.filter((e) => e.to === s || e.from === s).map((e) => (e.to === s ? e.from : e.to)).filter((k) => main.includes(k));
    sideY[s] = near.length ? near.reduce((n, k) => n + mid(k), 0) / near.length - NODE_H / 2 : 0;
  }
  const sideX = LANE0 + 3 * LANE_STEP + 40;

  // Jumps along the main line bulge to the right, the longest outermost.
  const jumps = edges.filter((e) => main.includes(e.from) && main.includes(e.to) && main.indexOf(e.to) !== main.indexOf(e.from) + 1);
  const lanes = [...jumps].sort((a, b) => Math.abs(main.indexOf(b.to) - main.indexOf(b.from)) - Math.abs(main.indexOf(a.to) - main.indexOf(a.from)));
  const width = side.length ? sideX + SIDE_W + 8 : LANE0 + Math.max(1, lanes.length) * LANE_STEP + 150;
  const height = main.length * ROW - GAP + 4;

  const node = (k: string, x: number, top: number, w: number) => {
    const st = process.states[k];
    const cls = ["pd-node", k === current ? "now" : "", done.includes(k) ? "done" : "", st.optional ? "opt" : ""].join(" ");
    return (
      <g key={k} className={cls} transform={`translate(${x} ${top})`}>
        <rect width={w} height={NODE_H} rx={8} />
        {st.agent ? (
          <>
            <foreignObject x={10} y={9} width={16} height={16}><Rune glyph={agentFace(st.agent).glyph} size={14} /></foreignObject>
            <text x={32} y={17} className="pd-name">{agentFace(st.agent).name}</text>
          </>
        ) : (
          <text x={12} y={17} className="pd-name">{ACTIONS[st.action ?? ""] ?? st.action}</text>
        )}
        <text x={st.agent ? 32 : 12} y={32} className="pd-sub">{[stageLabel(k), st.agent ? "" : "the app", st.optional ? "optional" : ""].filter(Boolean).join(" · ")}</text>
        {done.includes(k) && <path d="m-14 0 3 3 6-6" transform={`translate(${w - 10} ${NODE_H / 2})`} className="pd-check" />}
      </g>
    );
  };

  const label = (x: number, yy: number, text: string, anchor: "start" | "middle" = "start") =>
    text ? (
      <text x={x} y={yy} textAnchor={anchor} className="pd-edge">
        {text}
      </text>
    ) : null;

  return (
    <div className="pd-wrap">
      <svg className="pd" viewBox={`0 0 ${width} ${height}`} width={width} role="img" aria-label={`The ${process.name} process`}>
        <defs>
          <marker id={`${id}a`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M1 1 7 4 1 7" className="pd-head" />
          </marker>
        </defs>
        {main.slice(1).map((k, i) => {
          const way = edges.find((e) => e.from === main[i] && e.to === k);
          return (
            <g key={`m${k}`}>
              <path d={`M${NODE_W / 2} ${y(main[i]) + NODE_H} V${y(k) - 1}`} className="pd-line" markerEnd={`url(#${id}a)`} />
              {label(NODE_W / 2 + 8, y(k) - GAP / 2 + 4, way?.label ?? "")}
            </g>
          );
        })}
        {lanes.map((e, i) => {
          const lx = LANE0 + i * LANE_STEP;
          const ya = mid(e.from);
          const yb = mid(e.to);
          return (
            <g key={`j${e.from}${e.to}${i}`}>
              <path d={`M${NODE_W} ${ya} H${lx} V${yb} H${NODE_W + 2}`} className="pd-line side" markerEnd={`url(#${id}a)`} />
              {label(lx + 7, (ya + yb) / 2 + 4, e.label)}
            </g>
          );
        })}
        {edges
          .filter((e) => side.includes(e.from) !== side.includes(e.to))
          .map((e, i) => {
            const out = side.includes(e.to);
            const m = out ? e.from : e.to;
            const s = out ? e.to : e.from;
            const ym = mid(m) + (out ? -5 : 5);
            const ys = sideY[s] + NODE_H / 2 + (out ? -5 : 5);
            const d = out ? `M${NODE_W} ${ym} H${sideX - 14} L${sideX - 1} ${ys}` : `M${sideX} ${ys} H${sideX - 14} L${NODE_W + 2} ${ym}`;
            return (
              <g key={`s${e.from}${e.to}${i}`}>
                <path d={d} className="pd-line side" markerEnd={`url(#${id}a)`} />
                {label(sideX - 20, (out ? Math.min(ym, ys) : Math.max(ym, ys)) + (out ? -6 : 13), e.label, "middle")}
              </g>
            );
          })}
        {main.map((k) => node(k, 0, y(k), NODE_W))}
        {side.map((k) => node(k, sideX, sideY[k], SIDE_W))}
      </svg>
    </div>
  );
}
