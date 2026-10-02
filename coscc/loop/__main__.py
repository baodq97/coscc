"""`python -m coscc.loop <command>`: `cos.mjs`'s command line, read the way it reads it.

Flags sit anywhere (`.claude/scripts/cos.mjs:3370-3510`); every misuse prints `cos.mjs`'s words
and exits 2. Each command is a module's `run(args, out, err) -> int`, imported only when asked.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field

from coscc.loop import COS, LOCAL_ONLY, NEEDS_STATE, ROOT, STATE_READERS


@dataclass
class Args:
    """What every command is handed: the words after it, and what the flags said."""

    cmd: str
    rest: list[str]
    cos_dir: str
    repo_dir: str | None
    limit: int
    state: object = None
    reserve_from: list[str] = field(default_factory=list)


# The module that answers each command; each has `run(args, out, err) -> int`.
COMMANDS = {
    "status": "rules",
    "gate": "rules",
    "next": "rules",
    "new-path": "paths",
    "new-idea": "paths",
    "unit-branch": "branch",
    "pr-text": "branch",
    "rerun": "rerun",
    "screens": "repo_rules",
    "meta": "paths",
    "check-branch": "branch",
    "check-tag": "branch",
    "check-version": "branch",
}

USAGE = [
    "usage: cos.mjs [--root <dir>] <command>",
    "  reading a .cos/ (these take --root):",
    "    status [--json] | gate <unit> <stage> [--repo <dir>] | next <unit> [--repo <dir>] | "
    "new-path [--reserve-from <dir>]... <slug> | new-idea <slug> | unit-branch <unit> | "
    "pr-text <unit> | rerun <unit> [<stage>] | screens <unit> [--repo <dir>] | "
    "meta [<unit> [<artifact>]...]",
    "    status, gate, next, rerun, unit-branch, pr-text and screens need --state <file|->, "
    "the app's snapshot",
    "  describing this checkout (these do not):",
    "    check-branch [name] | check-tag <tag> | check-version",
]


def _line(stream):
    def write(line: str) -> None:
        stream.write(f"{line}\n")

    return write


def _misuse(cmd, rooted: bool, reserve_from: list[str], repo_arg, state) -> list[str]:
    """The lines a flag given to a command that takes none of it says, in `cos.mjs`'s order."""
    if cmd not in COMMANDS:
        return USAGE
    if rooted and cmd in LOCAL_ONLY:
        return [
            f"--root does not apply to `{cmd}`: it reports on the checkout this script lives in,",
            "  not on a .cos/ somewhere else. Run it from the repository you mean.",
        ]
    if reserve_from and cmd != "new-path":
        return [f"--reserve-from applies only to `new-path`, not to `{cmd}`."]
    if repo_arg is not None and cmd not in ("gate", "next", "screens"):
        return [f"--repo applies only to `gate` and `next`, and to `screens`, not to `{cmd}`."]
    if state is not None and cmd not in STATE_READERS:
        readers = ", ".join(f"`{c}`" for c in STATE_READERS)
        return [f"--state applies only to {readers}, not to `{cmd}`."]
    return []


def parse(argv: list[str], err) -> Args | int:
    """The `Args` of `argv`, or the exit code of a misuse, having said why."""
    root_at = argv.index("--root") if "--root" in argv else -1
    if root_at != -1 and not (argv[root_at + 1 :] or [""])[0]:
        err("--root needs a directory")
        return 2
    cos_dir = (
        str(COS) if root_at == -1 else os.path.join(os.path.abspath(argv[root_at + 1]), ".cos")
    )
    after_root = (
        argv
        if root_at == -1
        else [w for i, w in enumerate(argv) if i not in (root_at, root_at + 1)]
    )

    reserve_from: list[str] = []
    repo_arg = None
    state_arg = None
    words = []
    i = 0
    while i < len(after_root):
        flag = after_root[i]
        if flag == "--peer":
            err("--peer is gone since 0135: --state carries every workspace a link may name")
            return 2
        if flag not in ("--reserve-from", "--repo", "--state"):
            words.append(flag)
            i += 1
            continue
        if not (after_root[i + 1 :] or [""])[0]:
            err(
                "--state needs a file, or - for stdin"
                if flag == "--state"
                else f"{flag} needs a directory"
            )
            return 2
        value = after_root[i + 1]
        if flag == "--repo":
            repo_arg = value
        elif flag == "--state":
            state_arg = value
        else:
            reserve_from.append(value)
        i += 2
    cmd = words[0] if words else None
    rest = words[1:]

    repo_dir = (
        os.path.abspath(repo_arg) if repo_arg is not None else str(ROOT) if root_at == -1 else None
    )

    from coscc.loop.model import review_rounds

    try:
        limit = review_rounds()
    except ValueError as e:
        err(str(e))
        return 2

    state = None
    if state_arg is not None:
        from coscc.loop.snapshot import StateError, load

        try:
            state = load(state_arg)
        except StateError as e:
            err(str(e))
            return 2

    said = _misuse(cmd, root_at != -1, reserve_from, repo_arg, state)
    for line in said:
        err(line)
    if said:
        return 2
    if state is None and cmd in STATE_READERS:
        # R2: the app's database stands in for a snapshot nobody passed.
        from coscc.loop.snapshot import from_db

        state = from_db(cos_dir)
        if state is None:
            err(f"{cmd} {NEEDS_STATE}")
            return 2
    return Args(cmd or "", rest, cos_dir, repo_dir, limit, state, reserve_from)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="surrogateescape")  # ty: ignore[unresolved-attribute]
    out, err = _line(sys.stdout), _line(sys.stderr)
    args = parse(sys.argv[1:] if argv is None else argv, err)
    if isinstance(args, int):
        return args
    module = importlib.import_module(f"coscc.loop.{COMMANDS[args.cmd]}")
    return module.run(args, out, err)


if __name__ == "__main__":
    raise SystemExit(main())
