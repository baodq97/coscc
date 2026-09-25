"""`0091` proof: a step whose agent reads a screenshot is not ended by the SDK's line ceiling.

Plain: no session, no quota, no network. A fake CLI in a temporary directory prints one JSON
line of a chosen length and exits; the SDK's real `SubprocessCLITransport` reads it, given
the options `coscc.sessions._options` builds. It pushes one line of 32 MiB through a pipe.
Each claim prints PASS or FAIL:

- baseline: the SDK's own default options raise `CLIJSONDecodeError` on a line of
  8 388 608 characters, so the fake CLI reproduces the incident of run `c58b7e48`.
- R2: `_options`' options read that line as one message.
- R3: `_options`' options still raise on a line of `MAX_BUFFER + 1`, naming `MAX_BUFFER`.
- R6 on a fixture `cos.db`: `--measure` passes, fails and waits where `spec.md` R6 says.

No `SubprocessCLITransport` to import, or a `connect()` that fails, is exit 2: not measured,
not passed (`spec.md` C3).

`--measure` (R6) reads `<COS_DATA_DIR>/cos.db` with `mode=ro`, never through `Data` (`0076`),
and the merge time from this unit's `ship.md` under any `<COS_DATA_DIR>/units/*/.cos/`. The
window runs from it to `2026-10-17T00:00:00+07:00`, the end of 2026-10-16 -- the time zone is
chosen, not sourced. A step is counted when its `end` row is a board step's (it carries
`run`), of `impl` or `review`, in the window, and its `step_events` hold a `tool_use` of
`Read` whose `file_path` is `.screens/*-1440x900.png`. A counted step ended normally when it
is `done` or `exhausted` with `turns` set (`spec.md ## Answers, câu 1`), and died on the
buffer when its `detail` names `CLIJSONDecodeError`. Any counted step dead on the buffer is
exit 1; otherwise one that ended normally is exit 0; otherwise a closed window is exit 1 and
an open one exit 2. It writes only `<COS_DATA_DIR>/measurements/0091-<stamp>.json`. Run it at
a terminal (inside a step it reads only that step's own data root), and before `step_events`
of the counted steps are purged, 30 days after each (`spec.md` C4).

What it does not measure: how long a real CLI's line is for a given image -- only that the
ceiling reaches the transport (`spec.md` C1); whether the model saw the image; a chat, Gebo,
estimate or terminal session, none of which records `step_events`; a step that opened the
image through `Bash` rather than `Read`.

Exit codes: 0 pass, 1 broken, 2 environment not ready.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
UNIT = "0091_a-step-dies-when-its-agent-reads-a-screenshot"
WINDOW_END = datetime.fromisoformat("2026-10-17T00:00:00+07:00")
STAGES = ("impl", "review")
NORMAL = ("done", "exhausted")
SCREENS, IMAGE = ".screens", "*-1440x900.png"
BUFFER_ERROR = "CLIJSONDecodeError"
# Eight times the SDK's default of 1 048 576 (`spec.md` R2).
LINE = 8_388_608
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")


def say(line: str) -> None:
    print(line, flush=True)


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


# Copied from `scripts/verify_0080.py:70-104`; the proofs do not import each other.
def shipped_at(ship: Path) -> datetime | None:
    """The first ISO-8601 timestamp under `## What went out`, in UTC; `None` if there is none."""
    try:
        text = ship.read_text(encoding="utf-8")
    except OSError:
        return None
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## What went out"
            continue
        if inside:
            body.append(line)
    m = ISO.search("\n".join(body))
    if not m:
        return None
    zone = m.group(2) or "Z"
    zone = "+00:00" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
    try:
        at = datetime.fromisoformat(m.group(1) + zone)
    except ValueError:
        return None
    return at.astimezone(timezone.utc)


def _when(at: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def screenshot(event: dict) -> str | None:
    """The path a `Read` of a 1440x900 screenshot opened, or `None` for any other call."""
    if event.get("name") != "Read" or not isinstance(event.get("input"), dict):
        return None
    path = event["input"].get("file_path")
    if not isinstance(path, str):
        return None
    p = PurePosixPath(path)
    return path if p.parent.name == SCREENS and fnmatch(p.name, IMAGE) else None


def measure(root: Path, now: datetime | None = None) -> int:
    """R6. Everything it prints, it also writes to one file under `measurements/`."""
    now = now or datetime.now(timezone.utc)
    ships = sorted(root.glob(f"units/*/.cos/{UNIT}/ship.md"))
    merged = next((t for t in (shipped_at(p) for p in ships) if t is not None), None)
    if merged is None:
        say(f"no merge line in any {UNIT}/ship.md under {root}/units: nothing to measure yet")
        return EXIT_ENV
    db = root / "cos.db"
    if not db.is_file():
        say(f"no {db}")
        return EXIT_ENV
    buffer_anywhere = 0
    boards: list[dict] = []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        for (raw,) in conn.execute("SELECT record FROM runs WHERE kind = 'end' ORDER BY id"):
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            at = _when(r.get("at"))
            if at is None or not (merged <= at < WINDOW_END):
                continue
            if BUFFER_ERROR in str(r.get("detail") or ""):
                buffer_anywhere += 1
            if r.get("stage") in STAGES and r.get("run"):
                boards.append(r)
        steps: list[dict] = []
        purged = 0
        for r in boards:
            row = conn.execute("SELECT purged_at FROM step_runs WHERE run = ?", (r["run"],)).fetchone()
            if row is not None and row[0] is not None:
                purged += 1
            read: list[str] = []
            for (raw,) in conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND kind = 'tool_use' ORDER BY seq",
                (r["run"],),
            ):
                try:
                    path = screenshot(json.loads(raw))
                except ValueError:
                    continue
                if path is not None:
                    read.append(path)
            if not read:
                continue
            detail = str(r.get("detail") or "")
            steps.append({
                "run": r["run"], "unit": r.get("unit"), "stage": r.get("stage"), "at": r.get("at"),
                "outcome": r.get("outcome"), "turns": r.get("turns"),
                "read": [{"path": p, "bytes": os.path.getsize(p) if os.path.isfile(p) else None}
                         for p in read],
                "normal": r.get("outcome") in NORMAL and r.get("turns") is not None,
                "buffer": BUFFER_ERROR in detail,
                "detail": detail[:300] or None,
            })
    normal = [s for s in steps if s["normal"]]
    buffer = [s for s in steps if s["buffer"]]
    closed = now >= WINDOW_END
    if buffer:
        result, code = "trượt: một bước chết vì buffer", EXIT_BROKEN
    elif normal:
        result, code = "đạt", EXIT_PASS
    elif closed:
        result, code = "trượt: cửa sổ đã đóng, không bước nào kết thúc bình thường", EXIT_BROKEN
    else:
        result, code = "chưa đo được: cửa sổ còn mở", EXIT_ENV
    done = sum(1 for s in normal if s["outcome"] == "done")
    exhausted = sum(1 for s in normal if s["outcome"] == "exhausted")
    say(f"merged {merged.isoformat()}, window to {WINDOW_END.isoformat()}: "
        f"{len(steps)} board {'/'.join(STAGES)} steps read a {SCREENS}/{IMAGE}")
    for s in steps:
        files = ", ".join(f"{f['path']} ({f['bytes'] if f['bytes'] is not None else 'gone'})"
                          for f in s["read"])
        say(f"  {s['run']} {s['unit']} {s['stage']} {s['outcome']} turns={s['turns']}: {files}")
        if s["detail"] and not s["normal"]:
            say(f"    detail: {s['detail']}")
    say(f"  ended normally: {len(normal)} (done {done}, exhausted {exhausted}); dead on the buffer: {len(buffer)}")
    say(f"  {BUFFER_ERROR} in any end row of the window, for information: {buffer_anywhere}")
    say(f"  {'/'.join(STAGES)} board steps of the window whose events were purged: {purged}")
    say(f"→ {result}")
    out = root / "measurements" / f"0091-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "merged": merged.isoformat(), "window_end": WINDOW_END.isoformat(), "result": result,
        "counted": len(steps), "normal": len(normal), "done": done, "exhausted": exhausted,
        "buffer": len(buffer), "buffer_anywhere": buffer_anywhere, "purged": purged,
        "steps": steps, "exit": code,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


class Unmeasurable(Exception):
    """The transport could not be built or started: nothing was measured."""


def claim(ok: bool, text: str, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {text}{': ' + detail if detail and not ok else ''}")
    return ok


def fake_cli(where: Path, length: int) -> Path:
    """A CLI that answers nothing, prints one JSON line of `length` characters and exits."""
    cli = where / "claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "head = '{\"type\":\"user\",\"pad\":\"'\n"
        "tail = '\"}'\n"
        f"sys.stdout.write(head + 'A' * ({length} - len(head) - len(tail)) + tail + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    cli.chmod(0o755)
    return cli


async def read_line(transport_class, options) -> list[dict]:
    """What the SDK's transport makes of the fake CLI's one line. `CLIJSONDecodeError`
    passes through; a transport that cannot start is `Unmeasurable`."""
    transport = transport_class(prompt="", options=options)
    try:
        try:
            with mock.patch.dict(os.environ, {"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1"}):
                await transport.connect()
        except Exception as e:  # noqa: BLE001 - anything here means nothing was read
            raise Unmeasurable(f"connect: {type(e).__name__}: {e}") from e
        return [message async for message in transport.read_messages()]
    finally:
        await transport.close()


def outcome(transport_class, options) -> tuple[str, str]:
    """`("messages", types)` or `("raised", message)`; `Unmeasurable` propagates."""
    from claude_agent_sdk import CLIJSONDecodeError

    try:
        got = asyncio.run(read_line(transport_class, options))
    except CLIJSONDecodeError as e:
        return "raised", str(e)
    return "messages", ",".join(str(m.get("type")) for m in got)


def transport_claims(tmp: Path) -> bool | None:
    """baseline, R2, R3. `None` when the environment cannot answer."""
    try:
        import claude_agent_sdk as sdk
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    except ImportError as e:
        say(f"environment: the SDK's transport cannot be imported ({e})")
        return None
    from coscc import sessions
    from coscc.config import Config

    def built(length: int, where: Path):
        where.mkdir(parents=True)
        options = sessions._options(Config(), str(where), None, data_dir=str(where))
        options.cli_path = str(fake_cli(where, length))
        return options

    try:
        base = tmp / "baseline"
        base.mkdir(parents=True)
        # The one hand-built `ClaudeAgentOptions` here: what every session had before `0091`.
        default = sdk.ClaudeAgentOptions(cli_path=str(fake_cli(base, LINE)), cwd=str(base))
        kind, text = outcome(SubprocessCLITransport, default)
        ok = claim(kind == "raised" and "maximum buffer size of 1048576 " in text,
                   f"baseline: the SDK's default options raise on a line of {LINE}", f"{kind}: {text[:200]}")
        kind, text = outcome(SubprocessCLITransport, built(LINE, tmp / "r2"))
        ok &= claim(kind == "messages" and text == "user",
                    f"R2: _options' options read a line of {LINE} as one user message", f"{kind}: {text[:200]}")
        kind, text = outcome(SubprocessCLITransport, built(sessions.MAX_BUFFER + 1, tmp / "r3"))
        ok &= claim(kind == "raised" and f"maximum buffer size of {sessions.MAX_BUFFER} " in text,
                    f"R3: _options' options raise on a line of MAX_BUFFER + 1 = {sessions.MAX_BUFFER + 1}",
                    f"{kind}: {text[:200]}")
    except Unmeasurable as e:
        say(f"environment: {e}")
        return None
    return ok


def measure_fixture(tmp: Path) -> bool:
    from coscc.data import Data
    from coscc.journal import Journal

    closed, open_ = "2026-10-17T00:00:00+07:00", "2026-10-10T00:00:00+00:00"
    big, small = ".screens/settings-1440x900.png", ".screens/settings-390x844.png"
    dead = f"{BUFFER_ERROR}: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes..."
    # (stage, outcome, turns, what it read, when, detail)
    cases = (
        ("pass", [("impl", "done", 12, big, "2026-10-02", None)], closed, EXIT_PASS),
        ("exhausted-counts", [("review", "exhausted", 80, big, "2026-10-02", None)], open_, EXIT_PASS),
        ("buffer", [("impl", "done", 12, big, "2026-10-02", None),
                    ("review", "failed", None, big, "2026-10-03", dead)], open_, EXIT_BROKEN),
        ("no-turns", [("impl", "done", None, big, "2026-10-02", None)], closed, EXIT_BROKEN),
        ("not-counted", [("impl", "done", 12, small, "2026-10-02", None),
                         ("plan", "done", 3, big, "2026-10-03", None),
                         ("impl", "done", 12, "shots/settings-1440x900.png", "2026-10-04", None)],
         open_, EXIT_ENV),
        ("not-counted-closed", [("impl", "done", 12, small, "2026-10-02", None),
                                ("plan", "done", 3, big, "2026-10-03", None)], closed, EXIT_BROKEN),
        ("before-merge", [("impl", "failed", None, big, "2026-09-30", dead)], open_, EXIT_ENV),
        ("no-merge-line", [("impl", "done", 12, big, "2026-10-02", None)], open_, EXIT_ENV),
    )
    ok = True
    for case, steps, now, want in cases:
        root = tmp / case
        ship = root / "units" / "slot" / ".cos" / UNIT / "ship.md"
        ship.parent.mkdir(parents=True)
        went = "Merged 2026-10-01T10:00:00Z." if case != "no-merge-line" else "Not merged yet."
        ship.write_text(f"# Ship\nStatus: accepted.\n\n## What went out\n\n{went}\n", encoding="utf-8")
        j, data = Journal(root / "work", root), Data(root)
        for i, (stage, end, turns, path, day, detail) in enumerate(steps):
            run = f"run-{case}-{i}"
            unit = f"00{i}_x"
            at = datetime.fromisoformat(f"{day}T00:00:00+00:00")
            ms = int(at.timestamp() * 1000)
            j.append({"kind": "end", "workspace": "/w", "unit": unit, "stage": stage, "outcome": end,
                      "at": at.isoformat(), "turns": turns, "detail": detail, "run": run})
            data.step_run_open(run, str(root / "work"), "/w", unit, stage, ms)
            data.step_events_add(run, [
                {"run": run, "seq": 1, "at": ms, "kind": "tool_use", "id": "t1", "name": "Glob",
                 "input": {"pattern": "**/*.png"}},
                {"run": run, "seq": 2, "at": ms, "kind": "tool_use", "id": "t2", "name": "Read",
                 "input": {"file_path": f"/tree/{unit}/{path}"}},
            ])
        if case == "pass":
            # A step with no `run` (a terminal's, or a chat's) is never counted, and a step
            # whose events are gone is counted apart.
            j.append({"kind": "end", "workspace": "/w", "unit": "008_x", "stage": "impl", "outcome": "failed",
                      "at": "2026-10-05T00:00:00+00:00", "detail": dead})
            j.append({"kind": "end", "workspace": "/w", "unit": "009_x", "stage": "review", "outcome": "done",
                      "at": "2026-10-05T00:00:00+00:00", "turns": 4, "run": "run-purged"})
            data.step_run_open("run-purged", str(root / "work"), "/w", "009_x", "review", 0)
            with sqlite3.connect(root / "cos.db") as conn:
                conn.execute("UPDATE step_runs SET purged_at = '2026-10-06' WHERE run = 'run-purged'")
        code = measure(root, datetime.fromisoformat(now))
        ok &= claim(code == want, f"R6: --measure on a fixture cos.db, case {case}: exit {want}", f"exit {code}")
        if case == "pass":
            report = json.loads(sorted((root / "measurements").glob("0091-*.json"))[-1].read_text(encoding="utf-8"))
            got = (report["counted"], report["buffer_anywhere"], report["purged"])
            ok &= claim(got == (1, 1, 1),
                        "R6: a step with no run is not counted but its error is, and a purged step is counted apart",
                        f"counted, buffer_anywhere, purged = {got}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="R6, on the real <COS_DATA_DIR>")
    args = parser.parse_args()
    if args.measure:
        return measure(data_root())
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        transport = transport_claims(tmp / "transport")
        if transport is None:
            return EXIT_ENV
        ok = transport
        ok &= measure_fixture(tmp / "measure")
    say("all claims pass" if ok else "some claims failed")
    return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
