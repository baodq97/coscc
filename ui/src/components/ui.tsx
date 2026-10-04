// The shared parts every screen is built from. The look lives in `styles.css`; these only
// give it one shape in code, so a state (loading, empty, error) reads the same everywhere.

import type { ReactNode } from "react";
import { Icon, type IconName } from "../lib/icons";

export function Button({
  children,
  kind = "",
  size = "",
  icon,
  onClick,
  disabled,
  title,
}: {
  children?: ReactNode;
  kind?: "" | "primary" | "ghost" | "danger";
  size?: "" | "sm" | "lg";
  icon?: IconName;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button className={`btn ${kind} ${size}`} onClick={onClick} disabled={disabled} title={title}>
      {icon && <Icon name={icon} size={size === "sm" ? 13 : 14} />}
      {children}
    </button>
  );
}

export function Chip({ children, tone = "", square }: { children: ReactNode; tone?: "" | "accent" | "amber" | "red" | "green" | "plain"; square?: boolean }) {
  return <span className={`chip ${tone} ${square ? "sq" : ""}`}>{children}</span>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>;
}

export function Dot({ tone = "" }: { tone?: "" | "live" | "amber" | "red" | "green" }) {
  return <span className={`dot ${tone}`} />;
}

export function Meter({ value, max }: { value: number; max: number }) {
  const pct = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className={`meter ${pct > 70 ? "warn" : ""}`}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

/** A page's head: what the screen is for, in one line under its name. */
export function PageHead({ title, lede, actions }: { title: string; lede?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="row" style={{ alignItems: "flex-start", marginBottom: 8 }}>
      <div className="grow">
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0, letterSpacing: "-0.01em" }}>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Empty({ icon, title, children, actions }: { icon: IconName; title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <div className="em-ic">
        <Icon name={icon} size={20} />
      </div>
      <h3>{title}</h3>
      {children && <div>{children}</div>}
      {actions && (
        <div className="row" style={{ justifyContent: "center", marginTop: 14 }}>
          {actions}
        </div>
      )}
    </div>
  );
}

/** What went wrong, that nothing else broke, and the way to try again. */
export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <Empty icon="warn" title="Could not load this" actions={onRetry && <Button icon="refresh" onClick={onRetry}>Try again</Button>}>
      {error.message}. The rest of the studio still works.
    </Empty>
  );
}

/** Rows of grey bars while a list loads, so the wait looks like the list it becomes. */
export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div className="lrow" key={i}>
          <span className="sk" style={{ width: 54, height: 10 }} />
          <span className="sk" style={{ width: `${40 + ((i * 17) % 35)}%`, height: 10 }} />
        </div>
      ))}
    </div>
  );
}

/** A screen that is named and placed in the shell but not built yet. */
export function Planned({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <div className="page mid">
      <PageHead title={title} />
      <Empty icon={icon} title="Coming next">
        {children}
      </Empty>
    </div>
  );
}
