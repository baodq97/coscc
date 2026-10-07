// The shared parts every screen is built from. The look lives in `styles.css`; these only
// give it one shape in code, so a state (loading, empty, error) reads the same everywhere.

import { useEffect, type ReactNode } from "react";
import { Icon, type IconName } from "../lib/icons";
import { mdBlocks, type MdSpan } from "../lib/format";
import { Link } from "../lib/router";

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


/** A box over the screen for one small job; Esc or a press outside closes it. */
export function Dialog({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dlg${wide ? " wide" : ""}`} role="dialog" aria-label={title}>
        <div className="dlg-h">{title}</div>
        <div className="dlg-b">{children}</div>
      </div>
    </div>
  );
}

function Spans({ spans }: { spans: MdSpan[] }) {
  return (
    <>
      {spans.map((s, i) =>
        s.kind === "b" ? <b key={i}>{s.text}</b> : s.kind === "i" ? <i key={i}>{s.text}</i> : s.kind === "code" ? <code key={i}>{s.text}</code> : s.kind === "link" ? (s.href?.startsWith("/") ? <Link key={i} to={s.href}>{s.text}</Link> : <a key={i} href={s.href} target="_blank" rel="noreferrer">{s.text}</a>) : <span key={i}>{s.text}</span>,
      )}
    </>
  );
}

/** An agent's words as markdown reads them: emphasis, lists, code, links; never raw `**`. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {mdBlocks(text).map((b, i) =>
        b.kind === "pre" ? (
          <pre key={i}>{b.code}</pre>
        ) : b.kind === "ul" || b.kind === "ol" ? (
          (() => {
            const List = b.kind;
            return (
              <List key={i}>
                {b.lines.map((l, j) => (
                  <li key={j}>
                    <Spans spans={l} />
                  </li>
                ))}
              </List>
            );
          })()
        ) : b.kind === "h" ? (
          <div key={i} className="md-h">
            <Spans spans={b.lines[0]} />
          </div>
        ) : (
          <p key={i}>
            {b.lines.map((l, j) => (
              <span key={j}>
                {j > 0 && <br />}
                <Spans spans={l} />
              </span>
            ))}
          </p>
        ),
      )}
    </div>
  );
}
