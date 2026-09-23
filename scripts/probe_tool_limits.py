#!/usr/bin/env python3
"""`0032_impl-fills-its-context-with-whole-files-and-refusals` C4: does the SDK actually
honour the input `permission_gate(..., cap_reads=True)` rewrites, and does the CLI's own
`BASH_MAX_OUTPUT_LENGTH` and `Grep`'s `head_limit` behave the way the unit's plan assumed?

Unit tests only check what the callback *returns* — they cannot see whether the CLI runs
the rewritten input or the original one (plan.md Risk 3). This opens one real session,
with the same construction path an `impl` step gets, and reads the session's own transcript
afterwards to answer four things (plan.md step 9, C4.1-C4.4):

  C4.1  a `Read` with no `limit` on a large file was actually run with one added
  C4.2  what the model itself says about whether that `Read` looked truncated to it
  C4.3  whether any tool result in the transcript still ran past 25000 characters —
        `intent.md`'s tiêu chí 2 ceiling — which would mean the SDK ignored a rewrite, or
        that `head_limit` (a line count) let one very long line through (plan.md Risk 2)
  C4.4  whether `BASH_MAX_OUTPUT_LENGTH` actually bounded the `Bash` result

**It spends real quota.** One session on `claude-sonnet-5[1m]`, reading a ~100000-character
file, running `seq 1 40000`, and grepping a file with five 20000-character lines — all
three deliberately larger than the ceilings this unit adds, so the probe is testing what a
step meeting a real large file, not a synthetic one, actually gets back.

**A measured departure from plan.md step 9's literal check.** The plan says to fail when
"input Read trong transcript không mang limit" — the transcript's own `tool_use` block for
`Read` naming no `limit`. A real run (session `aeec2480-aa70-4d2a-b140-66dee926b8d2`,
2026-09-23) showed that check cannot pass even when the cap plainly worked: the transcript
recorded the model's *requested* input, `{"file_path": "big.txt"}`, with no `limit` key,
while the actual result was 22007 characters back from a ~101000-character file — the
override took effect on what ran, not on what the transcript says was asked for. So the
pass condition here is what `intent.md` tiêu chí 2 actually asks: no result over the
ceiling. `read_input` is still reported, for a reader who wants to see this gap themselves.

Exit codes:

    0  the session ran and no tool result exceeded 25000 characters
    1  a tool result exceeded 25000 characters, or the transcript could not be found
    2  no `claude` on PATH, or the CLI reports it is not logged in
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_agent_sdk as sdk  # noqa: E402

from coscc import policy, transcript  # noqa: E402
from coscc.config import Config  # noqa: E402
from coscc.runner import Denials, permission_gate  # noqa: E402
from coscc.sessions import _options  # noqa: E402

EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

MODEL = "claude-sonnet-5[1m]"
RESULT_CEILING = 25000  # intent.md tiêu chí 2

# 100000 characters, short lines — plan.md step 9.
BIG_LINES = 1000
BIG_LINE_LEN = 92  # (92 + 8) * 1000 = 100000
# Five lines, 20000 characters each — the Risk 2 shape: head_limit bounds lines, not chars.
WIDE_LINE_LEN = 20000
WIDE_LINES = 5

PROMPT = """\
This directory has two files, big.txt and wide.txt. Do exactly these four things, in \
order, then stop:

1. Read big.txt. Do not pass a limit or an offset — read it exactly as you normally would.
2. Run this command: seq 1 40000
3. Search wide.txt with Grep for a pattern that matches every line, for example ".". Use \
Grep's content output mode (output_mode "content"), not files_with_matches, so the matched \
line text itself comes back to you, not just the file's name.
4. Reply with exactly one line, nothing else: "TRUNCATED: yes" if step 1's Read told you \
its content was cut off or incomplete, or "TRUNCATED: no" if it read as a complete file.
"""


def _write_fixtures(root: Path) -> None:
    (root / "big.txt").write_text(
        "\n".join("x" * BIG_LINE_LEN for _ in range(BIG_LINES)) + "\n", encoding="utf-8"
    )
    (root / "wide.txt").write_text(
        "\n".join("y" * WIDE_LINE_LEN for _ in range(WIDE_LINES)) + "\n", encoding="utf-8"
    )


async def _run(cwd: str) -> dict:
    grant = policy.grant_for("impl")
    config = Config()
    denials = Denials()
    options = _options(
        config, cwd, None,
        max_turns=grant.max_turns,
        can_use_tool=permission_gate(grant, cwd, denials, cap_reads=True),
        tools=list(grant.tools),
        max_budget_usd=grant.max_budget_usd,
        model=MODEL,
    )

    client = sdk.ClaudeSDKClient(options=options)
    await client.connect()
    session_id = ""
    reply = ""
    try:
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, sdk.AssistantMessage):
                if message.session_id:
                    session_id = message.session_id
                for block in message.content:
                    if isinstance(block, sdk.TextBlock):
                        reply += block.text
            elif isinstance(message, sdk.ResultMessage):
                session_id = message.session_id or session_id
    finally:
        await client.disconnect()

    return {"session_id": session_id, "reply": reply.strip(), "denials": denials.count}


def _tool_details(lines: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    """Every `tool_use` this transcript recorded (name and input, keyed by id) and the
    character size of the `tool_result` that answered each one. Local to this script,
    deliberately not folded into `coscc/transcript.py`: that module counts refusals and the
    single largest result for R3; this needs each call's own name and input as well.
    """
    calls: dict[str, dict] = {}
    sizes: dict[str, int] = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                tid = block.get("id")
                if tid:
                    calls[str(tid)] = {
                        "name": block.get("name"), "input": block.get("input") or {},
                    }
            elif kind == "tool_result":
                tid = str(block.get("tool_use_id") or "")
                sizes[tid] = len(transcript._text_of(block.get("content")))
    return calls, sizes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if shutil.which("claude") is None:
        print("no claude on PATH — this measurement cannot run without it", file=sys.stderr)
        return EXIT_ENV

    with tempfile.TemporaryDirectory(prefix="probe-tool-limits-") as tmp:
        _write_fixtures(Path(tmp))
        try:
            run = asyncio.run(_run(tmp))
        except sdk.CLIConnectionError as e:
            print(f"claude could not be reached: {type(e).__name__}: {e}", file=sys.stderr)
            return EXIT_ENV
        except sdk.ProcessError as e:
            text = str(e).lower()
            if "login" in text or "auth" in text:
                print(f"claude is not logged in: {e}", file=sys.stderr)
                return EXIT_ENV
            raise

    path = transcript.find(run["session_id"]) if run["session_id"] else None
    if path is None:
        print(f"no transcript found for session {run['session_id']!r}", file=sys.stderr)
        result = {**run, "ok": False, "reason": "no transcript"}
        print(json.dumps(result) if args.json else result)
        return EXIT_BROKEN

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    calls, sizes = _tool_details(lines)

    read_call = next(
        (
            (tid, c) for tid, c in calls.items()
            if c["name"] == "Read" and str(c["input"].get("file_path", "")).endswith("big.txt")
        ),
        None,
    )
    bash_call = next((tid for tid, c in calls.items() if c["name"] == "Bash"), None)
    grep_call = next((tid for tid, c in calls.items() if c["name"] == "Grep"), None)

    read_has_limit = bool(read_call and "limit" in read_call[1]["input"])
    largest = max(sizes.values()) if sizes else 0

    result = {
        "session_id": run["session_id"],
        "reply": run["reply"],
        "grant_refusals_in_this_run": run["denials"],
        "read_input": read_call[1]["input"] if read_call else None,
        # See the docstring: this is reported, not what pass/fail is judged on.
        "read_input_carried_a_limit": read_has_limit,
        "read_result_chars": sizes.get(read_call[0]) if read_call else None,
        "bash_result_chars": sizes.get(bash_call) if bash_call else None,
        "grep_result_chars": sizes.get(grep_call) if grep_call else None,
        "largest_result_chars": largest,
    }

    if args.json:
        print(json.dumps(result))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")

    ok = largest <= RESULT_CEILING
    if not ok:
        print(f"FAIL: largest_result_chars={largest} (ceiling {RESULT_CEILING})", file=sys.stderr)
        return EXIT_BROKEN
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
