#!/usr/bin/env python3
"""Refuse a wheel that would install cleanly and then not work.

`.github/workflows/release.yml` calls this, and so can a person:

    uv run python scripts/check_wheel.py dist/coscc-0.2.3-py3-none-any.whl

    0  the wheel carries everything it needs
    1  it does not, and every missing thing is named on stderr
    2  no wheel was named, or the path is not there

The decision lives in `coscc.harness.wheel_complaints`, not here, so the same answer is
available to a test (`coscc/harness_test.py`) without running a subprocess. This file is
only the part that turns it into an exit code.

**Why this exists as a file rather than as four lines of `grep` in a workflow.**
`.cos/0012_installed-copy-runs-no-stage/spec.md` C1: the copy step that makes a wheel
correct lives in CI, so a wheel built by hand on somebody's machine is built without it
and there is nothing in the tree that would say so. This does not close that hole -- no
check closes a hole by existing -- but it is what makes closing it possible without
reading a YAML file first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc.harness import wheel_complaints  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <wheel>", file=sys.stderr)
        return 2
    wheel = Path(argv[1])
    if not wheel.is_file():
        print(f"no such wheel: {wheel}", file=sys.stderr)
        return 2

    complaints = wheel_complaints(wheel)
    if not complaints:
        print(f"{wheel.name}: carries a frontend and a harness")
        return 0
    for line in complaints:
        print(f"{wheel.name}: {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
