// The design system in one page: colours, type, controls and every state a screen can be in.

import { AgentAvatar, LeifAvatar } from "../lib/icons";
import { Button, Chip, Dot, Empty, ErrorState, Kbd, Meter, PageHead, SkeletonRows } from "../components/ui";

const SWATCHES = ["--bg", "--panel", "--side", "--line", "--text", "--text-2", "--text-3", "--accent", "--amber", "--red", "--green"];

export function System() {
  return (
    <div className="page">
      <PageHead title="Design system" lede="One accent: indigo means Leif or live, and nothing else. Amber needs you, red stopped, green passed." />
      <div className="sec-h">Colour</div>
      <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
        {SWATCHES.map((s) => (
          <div key={s} className="col gap4" style={{ alignItems: "center" }}>
            <span style={{ width: 56, height: 36, borderRadius: 7, background: `var(${s})`, border: "1px solid var(--line-2)" }} />
            <span className="mono faint">{s}</span>
          </div>
        ))}
      </div>
      <div className="sec-h">Controls</div>
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <Button kind="primary" icon="check">Primary</Button>
        <Button>Default</Button>
        <Button kind="ghost">Ghost</Button>
        <Button kind="danger" icon="x">Danger</Button>
        <Button size="sm">Small</Button>
        <Button disabled>Disabled</Button>
        <Kbd>⌘</Kbd>
        <Kbd>K</Kbd>
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginTop: 10 }}>
        <Chip>Neutral</Chip>
        <Chip tone="accent">Leif · live</Chip>
        <Chip tone="amber">Needs you</Chip>
        <Chip tone="red">Stopped</Chip>
        <Chip tone="green">Passed</Chip>
        <Chip square tone="plain">Fix</Chip>
        <Dot tone="live" />
        <Dot tone="amber" />
        <Dot tone="red" />
        <Dot tone="green" />
      </div>
      <div className="row" style={{ gap: 10, marginTop: 12 }}>
        <LeifAvatar />
        <LeifAvatar size="lg" />
        <LeifAvatar size="xl" />
        {["intent", "spec", "plan", "impl", "review", "integrate"].map((s) => (
          <AgentAvatar key={s} stage={s} size="lg" />
        ))}
      </div>
      <div style={{ maxWidth: 240, marginTop: 14 }}>
        <Meter value={92} max={120} />
      </div>
      <div className="sec-h">States</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div className="card">
          <SkeletonRows rows={3} />
        </div>
        <div className="card">
          <Empty icon="check" title="Nothing needs you">Leif will bring you the next question or merge.</Empty>
        </div>
        <div className="card">
          <ErrorState error={new Error("The board did not answer in 30 s")} onRetry={() => {}} />
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div className="leif-say">
            <LeifAvatar />
            <div className="bubble">
              <div className="leif-name">
                Leif <Dot tone="live" />
              </div>
              Writing the plan for COS-162 · 3 min
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
