#!/usr/bin/env python3
"""R3 (`spec.md`): count grant refusals, other errors and the largest tool result in one
session's own transcript.

No session, no quota, no network: this reads a `.jsonl` file already on disk, either found
under `~/.claude/projects/*/<id>.jsonl` from a session id, or given directly as a path — so
a transcript from another machine can be counted too, since the transcript itself is never
committed here (`.claude/CLAUDE.md`).

Exit codes:

    0  the transcript was read and counted
    2  no such session id, and no such file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc import transcript  # noqa: E402

EXIT_PASS, EXIT_ENV = 0, 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="a session id, or a path to a .jsonl transcript")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)

    given = Path(args.session)
    path = given if given.is_file() else transcript.find(args.session)
    if path is None:
        print(
            f"no transcript found for {args.session!r}: not a file, and no "
            "~/.claude/projects/*/<id>.jsonl matches it",
            file=sys.stderr,
        )
        return EXIT_ENV

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    result = transcript.summarize(lines)
    result["session_id"] = args.session

    if args.json:
        print(json.dumps(result))
    else:
        for key in (
            "session_id", "grant_refusals", "other_errors",
            "largest_result_chars", "largest_result_tool",
        ):
            print(f"{key}: {result[key]}")

    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
