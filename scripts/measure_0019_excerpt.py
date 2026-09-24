"""How long must a failed step's transcript tail be to still hold its measurements?

`0019_a-failed-step-destroys-the-work-that-succeeded` plan step 3 (`spec.md ## Answers,
câu 2`). Reads one real session transcript, assembles it with the same
`coscc.sessions.transcript_excerpt` the runner uses (no limit), and for every `Bash`
call whose command matches `--match` prints how far from the *end* of that assembled
string its output starts, in characters. The largest such distance is the smallest
`runner.ATTEMPT_EXCERPT` that would have kept every one of those outputs.

    uv run python scripts/measure_0019_excerpt.py --dir <session cwd> --session <prefix> \
        --match '<regex>'

Reads only; spends nothing. Exit 0 with the figures, 1 when no command matched, 2 when
the session cannot be found or read.
"""

from __future__ import annotations

import argparse
import re
import sys

import claude_agent_sdk as sdk

from coscc.sessions import _tool_result_text, transcript_excerpt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="the session's working directory")
    ap.add_argument("--session", required=True, help="a session id or a unique prefix of one")
    ap.add_argument(
        "--match", required=True, action="append",
        help="regex a Bash command must match; repeat it for alternatives",
    )
    args = ap.parse_args()

    try:
        found = [
            s.session_id
            for s in sdk.list_sessions(directory=args.dir)
            if s.session_id.startswith(args.session)
        ]
    except Exception as e:  # noqa: BLE001 — reported, not raised
        print(f"cannot list sessions in {args.dir}: {type(e).__name__}: {e}")
        return 2
    if len(found) != 1:
        print(f"prefix {args.session!r} matched {len(found)} sessions in {args.dir}")
        return 2
    session_id = found[0]

    full, total = transcript_excerpt(session_id, args.dir, 10**12)
    pattern = re.compile("|".join(f"(?:{m})" for m in args.match))

    commands: dict[str, str] = {}
    outputs: list[tuple[str, str]] = []
    for m in sdk.get_session_messages(session_id, directory=args.dir):
        if m.parent_tool_use_id or m.parent_agent_id:
            continue
        content = (m.message or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                command = str((block.get("input") or {}).get("command", ""))
                if pattern.search(command):
                    commands[block.get("id", "")] = command
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                outputs.append(
                    (commands[block["tool_use_id"]], _tool_result_text(block.get("content")))
                )

    print(f"session {session_id}: {total} characters assembled")
    distances = []
    for command, text in outputs:
        text = text.strip()
        at = full.rfind(text) if text else -1
        if at < 0:
            print(f"  not found in the assembled string: {command[:100]}")
            continue
        distance = total - at
        distances.append(distance)
        print(f"  {distance:>8}  {command[:100]}")
    if not distances:
        print("no matching Bash output found")
        return 1
    print(f"max {max(distances)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
