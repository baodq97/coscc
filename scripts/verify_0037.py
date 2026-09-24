#!/usr/bin/env python3
"""Proof, and measuring tool, for the store's `0037_board-sessions-run-without-claude-codes-system-prompt`.

A board step holding any tool now runs on Claude Code's preset system prompt instead of the
empty one the SDK sends when none is set. Four modes:

    (default)    the proof. No session, no quota, no network; temporary data root.
                 (a) `Runner.run` -> `Sessions.stream` -> `_options`, with only the SDK
                     client replaced: the six stages with tools reach the client holding the
                     preset, `idea` and `intent` holding `None`.
                 (b) the installed SDK's own command builder, fed those options: with the
                     preset there is no `--system-prompt ""`, without it there still is.
                     This is the SDK's private API; if it cannot be called that is exit 2,
                     never a pass.
                 (c) the `start` record carries `system_prompt`, read as a reader reads it.
                 (d) `--measure`'s verdict on fixture logs: pass, fail on more refusals,
                     and "not enough sessions".
    --baseline   reads `<COS_DATA_DIR>/cos.db` read-only and the transcripts; prints the
                 sessions the intent names as the "before" branch and writes them to
                 `<COS_DATA_DIR>/measurements/0037_baseline.json`. No session.
    --measure    the intent's outcome: the first two `impl` steps that ran on the preset
                 against the two "before" sessions of the same model family. Writes
                 `<COS_DATA_DIR>/measurements/0037_measure.json`. No session.
    --paid       **spends real money**: `claude -p` six times, three per branch, on the one
                 fixed task the intent measured. Needs `claude` on `PATH`.

    0  pass (the outcome held, for --measure / --paid)
    1  fail (a claim, or the outcome, did not hold)
    2  the environment could not answer, or there are not enough sessions to compare

`--baseline` and `--measure` open `cos.db` with `mode=ro` and read the `runs` table
directly, never through `coscc.data.Data`, so they neither upgrade the schema nor trip
`data.Incompatible` on a database newer than this checkout.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

STAGES = ["idea", "intent", "spec", "plan", "impl", "pr", "review", "ship"]
WITH_TOOLS = ["spec", "plan", "impl", "pr", "review", "ship"]
PRESET = {"type": "preset", "preset": "claude_code"}

# The "before" branch, as `intent.md ## Answers, câu 2` names it.
SONNET, OPUS = "claude-sonnet-5", "claude-opus-5"
BASELINE_UNITS = {SONNET: ("0031_", "0032_", "0025_"), OPUS: ("0019_",)}
OPUS_BASELINE_DAY = "2026-09-24"

# What `coscc/runner.py` says when it refuses a prose stage's reply (`check_reply`,
# `merge_review` and the Answers re-check). Matched by prefix on `end.detail`.
REFUSALS = (
    "the session returned nothing",
    "the reply carries no `Status:` line",
    "the reply adds no review round",
    "the reply changes an earlier review round",
)

# The fixed task the intent measured on 2026-09-24.
PAID_TASK = (
    "In this repository, find the function that calls `worktrees.remove_if_finished` in "
    "`coscc/service.py`, and count the lines of `coscc/runner.py`. Answer in two lines."
)
PAID_RUNS = 3


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}",
          flush=True)
    return ok


# ---------------------------------------------------------------------------
# Reading the run log and the transcripts (--baseline, --measure)
# ---------------------------------------------------------------------------


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


def transcripts_root() -> Path:
    return Path(os.environ.get("COS_TRANSCRIPTS_DIR") or "~/.claude/projects").expanduser()


def load_steps(db: Path) -> list[dict]:
    """Every `start` paired with the next `end` of the same (root, workspace, unit, stage).

    A `start` with no `end` after it (a step still running, or a process that died) is
    left out: it has no outcome and no cost to compare.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, at, root, workspace, unit, stage, kind, record FROM runs "
            "WHERE kind IN ('start', 'end') ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    open_starts: dict[tuple, dict] = {}
    steps: list[dict] = []
    for rid, at, root, ws, unit, stage, kind, raw in rows:
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        key = (root, ws, unit, stage)
        if kind == "start":
            open_starts[key] = {"id": rid, "at": at, "unit": unit, "stage": stage, "start": rec}
        elif key in open_starts:
            step = open_starts.pop(key)
            step["end"] = rec
            steps.append(step)
    return steps


def family_of(step: dict) -> str:
    used = step["end"].get("models_used") or []
    for fam in (OPUS, SONNET):
        if any(str(m).startswith(fam) for m in used):
            return fam
    return ""


def prompt_of(step: dict) -> str:
    return str(step["start"].get("system_prompt", "") or "")


def first_call_input(session_id: str) -> int | None:
    """Input of the first model call: input + cache creation + cache read, from the
    transcript's first `assistant` record. `None` when there is no transcript."""
    if not session_id:
        return None
    for path in glob.glob(str(transcripts_root() / "*" / f"{session_id}.jsonl")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if item.get("type") != "assistant":
                        continue
                    usage = (item.get("message") or {}).get("usage") or {}
                    return int(
                        (usage.get("input_tokens") or 0)
                        + (usage.get("cache_creation_input_tokens") or 0)
                        + (usage.get("cache_read_input_tokens") or 0)
                    )
        except OSError:
            continue
    return None


def row_of(step: dict) -> dict:
    end = step["end"]
    sid = str(end.get("session_id") or "")
    return {
        "session_id": sid,
        "unit": step["unit"],
        "at": step["at"],
        "model": ",".join(end.get("models_used") or []) or str(step["start"].get("model") or ""),
        "family": family_of(step),
        "system_prompt": prompt_of(step),
        "outcome": str(end.get("outcome") or ""),
        "cost_usd": float(end.get("cost_usd") or 0.0),
        "denials": int(end.get("denials") or 0),
        "turns": end.get("turns"),
        "first_call_input": first_call_input(sid),
        "detail": str(end.get("detail") or ""),
    }


def baseline(steps: list[dict], family: str) -> list[dict]:
    """The two most recent "before" `impl` steps of one model family."""
    picked = []
    for s in steps:
        if s["stage"] != "impl" or prompt_of(s) or family_of(s) != family:
            continue
        if not s["unit"].startswith(BASELINE_UNITS[family]):
            continue
        if family == OPUS and not str(s["at"]).startswith(OPUS_BASELINE_DAY):
            continue
        picked.append(s)
    return [row_of(s) for s in picked[-2:]]


def after(steps: list[dict]) -> list[dict]:
    """The first two `impl` steps, in any workspace, that ran on the preset."""
    return [row_of(s) for s in steps if s["stage"] == "impl" and prompt_of(s) == "claude_code"][:2]


def per_done(rows: list[dict]) -> float:
    done = sum(1 for r in rows if r["outcome"] == "done")
    total = sum(r["cost_usd"] for r in rows)
    return total / done if done else math.inf


def verdict(before: list[dict], later: list[dict]) -> tuple[bool, list[str]]:
    """The two conditions of `intent.md ## Proposed outcome`, nothing else."""
    cost_b, cost_a = per_done(before), per_done(later)
    den_b = sum(r["denials"] for r in before)
    den_a = sum(r["denials"] for r in later)
    cost_ok = cost_a <= cost_b
    # `spec.md ## Answers, câu 1`: a baseline of 0 is met by staying at 0. More refusals
    # fail whatever the cost did (`intent.md ## Answers, câu 5`).
    den_ok = den_a < den_b or (den_b == 0 and den_a == 0)
    lines = [
        f"cost per done impl step: before {cost_b:.4f} USD, after {cost_a:.4f} USD -> "
        f"{'held' if cost_ok else 'NOT held'}",
        f"grant refusals: before {den_b}, after {den_a} -> {'held' if den_ok else 'NOT held'}",
    ]
    return cost_ok and den_ok, lines


def print_rows(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    print("| session | unit | model | system_prompt | outcome | cost_usd | denials | turns | first-call input |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        fci = "null" if r["first_call_input"] is None else r["first_call_input"]
        print(f"| {r['session_id']} | {r['unit']} | {r['model']} | {r['system_prompt'] or '\"\"'} | "
              f"{r['outcome']} | {r['cost_usd']:.4f} | {r['denials']} | {r['turns']} | {fci} |")


def save(name: str, payload: dict) -> Path:
    out = data_root() / "measurements"
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def open_log() -> list[dict] | None:
    db = data_root() / "cos.db"
    if not db.is_file():
        print(f"no run log at {db}")
        return None
    return load_steps(db)


def run_baseline() -> int:
    steps = open_log()
    if steps is None:
        return EXIT_ENV
    found = {fam: baseline(steps, fam) for fam in (SONNET, OPUS)}
    for fam, rows in found.items():
        print_rows(f"baseline {fam} ({len(rows)} of 2)", rows)
    path = save("0037_baseline.json", {"baseline": found})
    print(f"\nwritten: {path}")
    short = [fam for fam, rows in found.items() if len(rows) < 2]
    if short:
        print(f"fewer than two baseline sessions for: {', '.join(short)} — "
              "that family is compared with --paid instead")
        return EXIT_ENV
    return EXIT_PASS


def run_measure() -> int:
    steps = open_log()
    if steps is None:
        return EXIT_ENV
    later = after(steps)
    print_rows(f"after: impl steps on the preset ({len(later)} of 2)", later)

    # Not part of the verdict: every prose stage with tools on the preset, and whether its
    # reply was refused (spec C2), and the after-branch's own outcomes (C3).
    prose = [row_of(s) for s in steps
             if s["stage"] in ("spec", "plan", "review") and prompt_of(s) == "claude_code"]
    print("\nprose stages on the preset (not in the verdict):")
    for r, s in zip(prose, [s for s in steps if s["stage"] in ("spec", "plan", "review")
                            and prompt_of(s) == "claude_code"]):
        refused = r["detail"].startswith(REFUSALS)
        print(f"  {s['unit']} {s['stage']} {r['outcome']}"
              f"{'  <-- REPLY REFUSED: ' + r['detail'][:120] if refused else ''}")
    if not prose:
        print("  (none yet)")

    payload: dict = {"after": later, "prose": prose}
    if len(later) < 2:
        print("\nfewer than two impl steps have run on the preset — reinstall and restart "
              "the app, run impl from the board, then ask again; or run --paid")
        save("0037_measure.json", payload)
        return EXIT_ENV
    fams = {r["family"] for r in later}
    if len(fams) != 1 or "" in fams:
        print(f"\nthe two after-sessions are not one model family ({sorted(fams)}); run --paid")
        save("0037_measure.json", payload)
        return EXIT_ENV
    fam = fams.pop()
    before = baseline(steps, fam)
    print_rows(f"before: baseline {fam} ({len(before)} of 2)", before)
    payload["before"] = before
    if len(before) < 2:
        print(f"\nfewer than two baseline sessions for {fam}; run --paid")
        save("0037_measure.json", payload)
        return EXIT_ENV
    ok, lines = verdict(before, later)
    print()
    for line in lines:
        print(line)
    payload["verdict"] = {"held": ok, "lines": lines}
    print(f"\nwritten: {save('0037_measure.json', payload)}")
    print("outcome HELD" if ok else "outcome did NOT hold")
    return EXIT_PASS if ok else EXIT_BROKEN


def run_paid(model: str) -> int:
    claude = shutil.which("claude")
    if claude is None:
        print("no `claude` on PATH — --paid cannot run")
        return EXIT_ENV
    branches = {"before": ["--system-prompt", ""], "after": []}
    rows: dict[str, list[dict]] = {"before": [], "after": []}
    for name, extra in branches.items():
        for i in range(PAID_RUNS):
            cmd = [claude, "-p", PAID_TASK, "--model", model, "--setting-sources", "",
                   "--tools", "Read,Grep,Glob,Bash", "--allowedTools", "Read,Grep,Glob,Bash",
                   "--output-format", "json", *extra]
            proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
            try:
                out = json.loads(proc.stdout)
            except (json.JSONDecodeError, ValueError):
                print(f"{name} run {i + 1}: no JSON from claude (exit {proc.returncode}): "
                      f"{(proc.stderr or proc.stdout)[-300:]}")
                return EXIT_ENV
            row = {
                "session_id": str(out.get("session_id") or ""), "unit": "paid",
                "model": model, "family": "", "system_prompt": "" if extra else "claude_code",
                "outcome": "failed" if out.get("is_error") else "done",
                "cost_usd": float(out.get("total_cost_usd") or 0.0),
                "denials": len(out.get("permission_denials") or []),
                "turns": out.get("num_turns"), "detail": "",
            }
            row["first_call_input"] = first_call_input(row["session_id"])
            rows[name].append(row)
    print_rows("before (--system-prompt \"\")", rows["before"])
    print_rows("after (preset)", rows["after"])
    ok, lines = verdict(rows["before"], rows["after"])
    print()
    for line in lines:
        print(line)
    print(f"\nwritten: {save('0037_paid.json', {**rows, 'model': model, 'held': ok})}")
    return EXIT_PASS if ok else EXIT_BROKEN


# ---------------------------------------------------------------------------
# The proof (default mode)
# ---------------------------------------------------------------------------


def claim_a(tmp: Path) -> tuple[bool, dict[str, object]]:
    """The whole chain, with only the SDK client replaced. Returns options per stage."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    from coscc import sessions as sessions_mod
    from coscc.config import Config
    from coscc.journal import Journal
    from coscc.runner import Runner

    seen: dict[str, object] = {}
    current = {"stage": "", "dir": Path()}

    class FakeClient:
        def __init__(self, options):
            seen[current["stage"]] = options

        async def connect(self):
            return None

        async def query(self, text):
            return None

        async def disconnect(self):
            return None

        async def receive_response(self):
            stage = current["stage"]
            text = f"# {stage}: proof\nStatus: accepted.\n"
            if stage == "review":
                text = "# Review: proof\nStatus: accepted.\n\n## Round 1\n"
            if stage in ("impl", "pr", "ship"):
                (current["dir"] / f"{stage}.md").write_text(text, encoding="utf-8")
                text = "written"
            yield AssistantMessage(content=[TextBlock(text=text)], model="proof",
                                   session_id=f"proof-{stage}")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id=f"proof-{stage}",
                                total_cost_usd=0.0, model_usage={})

    ws = tmp / "ws"
    unit = "0001_proof"
    directory = ws / ".cos" / unit
    directory.mkdir(parents=True)
    (directory / "intent.md").write_text("# Intent: proof\nStatus: accepted.\n", encoding="utf-8")
    journal = Journal(str(ws), str(tmp / "data"))
    live = sessions_mod.Sessions(Config(workspaces=(str(ws),)))
    live.membership = lambda _d: True
    runner = Runner(sessions=live, journal=journal)
    outcomes = {}
    original = sessions_mod.ClaudeSDKClient
    sessions_mod.ClaudeSDKClient = FakeClient
    try:
        for stage in STAGES:
            current["stage"], current["dir"] = stage, directory

            async def go():
                return [ev async for ev in runner.run(
                    workspace=str(ws), directory=directory, journal_key=str(ws), unit=unit,
                    stage=stage, artifact=f"{stage}.md", stages=STAGES, mode="manual",
                )]

            outcomes[stage] = asyncio.run(go())[-1][1]["outcome"]
    finally:
        sessions_mod.ClaudeSDKClient = original

    ok = True
    for stage in STAGES:
        opts = seen.get(stage)
        want = PRESET if stage in WITH_TOOLS else None
        got = getattr(opts, "system_prompt", "<no options>")
        ok &= say(got == want, f"(a) {stage}: the client is built with system_prompt={want}",
                  f"got {got!r} (outcome {outcomes.get(stage)})")
    ok &= say(all(v == "done" for v in outcomes.values()),
              "(a) every stage ran to done through the real runner and session layer",
              str(outcomes))
    claim_a.journal = journal  # handed to (c)
    claim_a.ws = str(ws)
    return ok, seen


def claim_b(seen: dict[str, object]) -> int:
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    except Exception as e:  # noqa: BLE001 - private API; any failure is "cannot answer"
        print(f"(b) cannot import the SDK's command builder: {type(e).__name__}: {e}")
        return EXIT_ENV

    def empty_pair(cmd: list[str]) -> bool:
        return any(cmd[i] == "--system-prompt" and cmd[i + 1] == ""
                   for i in range(len(cmd) - 1))

    ok = True
    for stage in STAGES:
        try:
            t = SubprocessCLITransport(prompt="", options=seen[stage])
            t._cli_path = "claude"
            cmd = t._build_command()
        except Exception as e:  # noqa: BLE001
            print(f"(b) the SDK's command builder could not be called: {type(e).__name__}: {e}")
            return EXIT_ENV
        if stage in WITH_TOOLS:
            ok &= say(not empty_pair(cmd) and "--system-prompt" not in cmd,
                      f"(b) {stage}: the CLI command carries no --system-prompt at all",
                      " ".join(cmd[:12]))
        else:
            ok &= say(empty_pair(cmd), f'(b) {stage}: the CLI command still carries --system-prompt ""',
                      " ".join(cmd[:12]))
    return EXIT_PASS if ok else EXIT_BROKEN


def claim_c() -> bool:
    records = claim_a.journal.records(claim_a.ws, kind="start")
    got = {r["stage"]: r.get("system_prompt", "") for r in records}
    ok = True
    for stage in STAGES:
        want = "claude_code" if stage in WITH_TOOLS else ""
        ok &= say(got.get(stage) == want, f"(c) {stage}: start.system_prompt == {want!r}",
                  f"got {got.get(stage)!r}")
    return ok


def claim_d(tmp: Path) -> bool:
    """`--measure` against fixture logs written by the real journal."""
    from coscc.journal import Journal

    def fixture(name: str, after_denials: int, drop_one_after: bool = False) -> Path:
        root = tmp / name
        j = Journal(str(root / "ws"), str(root))

        def step(unit, prompt, model, cost, denials, at, outcome="done"):
            j.append({"kind": "start", "at": at, "workspace": "w", "unit": unit,
                      "stage": "impl", "mode": "manual", "system_prompt": prompt})
            j.append({"kind": "end", "at": at, "workspace": "w", "unit": unit,
                      "stage": "impl", "outcome": outcome, "cost_usd": cost,
                      "denials": denials, "turns": 10, "session_id": f"{unit}-{prompt}",
                      "models_used": [model]})

        # A pre-0037 record with no `system_prompt` field at all counts as "".
        j.append({"kind": "start", "at": "2026-09-23T00:00:00", "workspace": "w",
                  "unit": "0031_x", "stage": "impl", "mode": "manual"})
        j.append({"kind": "end", "at": "2026-09-23T00:00:00", "workspace": "w",
                  "unit": "0031_x", "stage": "impl", "outcome": "done", "cost_usd": 2.0,
                  "denials": 3, "session_id": "s-old", "models_used": ["claude-sonnet-5[1m]"]})
        step("0032_y", "", "claude-sonnet-5[1m]", 2.0, 3, "2026-09-23T01:00:00")
        step("0050_a", "claude_code", "claude-sonnet-5[1m]", 1.5, after_denials,
             "2026-09-30T00:00:00")
        if not drop_one_after:
            step("0051_b", "claude_code", "claude-sonnet-5[1m]", 1.5, after_denials,
                 "2026-10-01T00:00:00")
        return root

    def measure(root: Path) -> int:
        saved = os.environ.get("COS_DATA_DIR")
        os.environ["COS_DATA_DIR"] = str(root)
        try:
            return run_measure()
        finally:
            if saved is None:
                os.environ.pop("COS_DATA_DIR", None)
            else:
                os.environ["COS_DATA_DIR"] = saved

    ok = True
    ok &= say(measure(fixture("pass", 1)) == EXIT_PASS,
              "(d) --measure: cheaper and fewer refusals is exit 0")
    ok &= say(measure(fixture("more", 4)) == EXIT_BROKEN,
              "(d) --measure: more refusals is exit 1 even though it is cheaper")
    ok &= say(measure(fixture("short", 1, drop_one_after=True)) == EXIT_ENV,
              "(d) --measure: one after-session only is exit 2")
    before = [{"outcome": "done", "cost_usd": 1.0, "denials": 0}]
    ok &= say(verdict(before, [{"outcome": "done", "cost_usd": 1.0, "denials": 0}])[0],
              "(d) a baseline of 0 refusals is met by staying at 0")
    ok &= say(not verdict(before, [{"outcome": "exhausted", "cost_usd": 0.1, "denials": 0}])[0],
              "(d) an after-branch with no done step costs infinity per done step")
    return ok


def run_proof() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verify_0037-"))
    os.environ["COS_DATA_DIR"] = str(tmp / "data")
    os.environ["COS_WORKING_DIR"] = str(tmp)
    os.environ["COS_TRANSCRIPTS_DIR"] = str(tmp / "no-transcripts")
    try:
        ok_a, seen = claim_a(tmp)
        b = claim_b(seen)
        ok_c = claim_c()
        ok_d = claim_d(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if b == EXIT_ENV:
        print("claim (b) could not be answered — not a pass")
        return EXIT_ENV
    ok = ok_a and b == EXIT_PASS and ok_c and ok_d
    print("\nall claims held" if ok else "\nat least one claim did not hold")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--measure", action="store_true")
    g.add_argument("--paid", action="store_true")
    p.add_argument("--model", default="claude-sonnet-5[1m]",
                   help="--paid only: the model both branches run on")
    args = p.parse_args()
    if args.baseline:
        return run_baseline()
    if args.measure:
        return run_measure()
    if args.paid:
        return run_paid(args.model)
    return run_proof()


if __name__ == "__main__":
    raise SystemExit(main())
