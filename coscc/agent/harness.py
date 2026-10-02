"""Where the rules this app runs live (the skills, `states.json`), and the refusal when they are
not there. The loop that decides on them is `coscc.loop`, code of the package itself.

`root()` prefers the packaged copy (`coscc/_harness/`, generated at release and gitignored)
and falls back to the checkout's `.claude/`. Nothing here looks inside a workspace: a path
is only ever under this app's own installation or checkout.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

import coscc
from coscc import frontend
from coscc.agent import agents, models

# The package root, `coscc/`: the wheel holds `_harness/` there, and the checkout's `.claude/`
# sits beside it.
_HERE = Path(coscc.__file__).resolve().parent

# The unit state set and its lanes, inside the package so a wheel carries them.
STATES_PATH = _HERE / "units" / "states.json"
LANES_PATH = _HERE / "units" / "lanes.json"

# Where the release puts the harness inside the wheel.
PACKAGE_HARNESS = _HERE / "_harness"

# The checkout's own copy, one level up beside `coscc/`.
CHECKOUT_HARNESS = _HERE.parent / ".claude"

_SKILLS = Path("skills")
SKILL_FILE = "SKILL.md"


class MissingRules(RuntimeError):
    """The rules for a step could not be found; carries the paths searched.

    Raised, never returned as empty: a step must not spend quota on a prompt with no rules.
    """


def child_env() -> dict[str, str]:
    """The environment the loop child (`python -m coscc.loop`, see `coscc.loop.run`) runs in.

    Built up, never filtered down (see `gitops.child_env`): it needs no secret, so it is given
    none.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LC_ALL": "C",
        "NO_COLOR": "1",
    }
    # Passed only when set, so an unset variable keeps meaning "the loop's own default".
    # The `review` and `ship` gates run `git` and `gh`; `gh` finds its login through `HOME`, so a
    # machine logging in with `GH_TOKEN` alone sees those gates closed. No secret is passed down.
    rounds = os.environ.get("COS_REVIEW_ROUNDS")
    if rounds:
        env["COS_REVIEW_ROUNDS"] = rounds
    return env


def _outside(entry: str, roots: list[Path]) -> bool:
    try:
        p = Path(entry).resolve()
    except OSError, ValueError:
        return False
    return not any(p == r or r in p.parents for r in roots)


def clean_path(workspace: str | os.PathLike[str] | None) -> str:
    """`PATH` without any entry under the workspace or the installed package, whose `.venv/bin`
    would run the workspace's code instead of the tree's.
    """
    roots = [_HERE]
    if workspace:
        roots.append(Path(workspace).expanduser().resolve())
    parts = [e for e in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep) if e]
    return os.pathsep.join(e for e in parts if _outside(e, roots))


def is_packaged() -> bool:
    """Whether this install carries its own harness: the packaged `skills/` directory and the
    state set the wheel check (`wheel_complaints`) also asks for. Half a harness does not count."""
    return (PACKAGE_HARNESS / _SKILLS).is_dir() and STATES_PATH.is_file()


def root() -> Path:
    """The `.claude`-shaped directory this app reads its rules from."""
    return PACKAGE_HARNESS if is_packaged() else CHECKOUT_HARNESS


def skills_dir() -> Path:
    return root() / _SKILLS


def read_skill(*names: str) -> str:
    """The first skill that exists, by name, in order. Raises `MissingRules` naming every
    path tried."""
    tried = []
    for name in names:
        path = skills_dir() / name / SKILL_FILE
        tried.append(str(path))
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise MissingRules("no rules found; looked at " + ", ".join(tried))


# --- what a runnable wheel must contain --------------------------------------

# Uses `frontend._LAYOUT` rather than repeating "build/client".
_WEB = frontend.PACKAGE_WEB.name
_HARNESS = PACKAGE_HARNESS.name

# The build stamp `scripts/build_wheel.sh` writes; an installed copy learns its commit only here.
BUILD_STAMP = "_build.json"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _posix(*parts: object) -> str:
    return Path("coscc", *[str(p) for p in parts]).as_posix()


def wheel_complaints(wheel: str | Path) -> list[str]:
    """Everything wrong with `wheel`, as sentences. Empty means it would run.

    Each entry names a wheel that installs cleanly and then fails differently: no frontend
    (page 404s), no compile marker (service is `active` and serves nothing), no skills, no
    `states.json`, no build stamp with a 40-hex commit. The stamp is checked though committed:
    whether a file arrives by `git` or by a copy step is invisible to the installed copy. Skills
    are counted, not listed by name.
    """
    path = Path(wheel)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as e:
        return [f"{path} could not be read as a wheel: {e}"]

    index = _posix(_WEB, frontend._LAYOUT, "index.html")
    marker = _posix(_WEB, frontend.MARKER)
    skills_prefix = _posix(_HARNESS, _SKILLS) + "/"
    state_set = _posix(STATES_PATH.relative_to(_HERE))

    out = []
    if index not in names:
        out.append(f"no {index} — it would install and serve no page")
    if marker not in names:
        out.append(f"no {marker} — it would report active and never serve")
    found = sum(1 for n in names if n.startswith(skills_prefix) and n.endswith("/" + SKILL_FILE))
    if not found:
        out.append(f"no {skills_prefix}*/{SKILL_FILE} — every step would refuse to run")
    if state_set not in names:
        out.append(f"no {state_set} — no transition could be read or written")
    # Without it no guard is chosen for any transition.
    lanes = _posix(LANES_PATH.relative_to(_HERE))
    if lanes not in names:
        out.append(f"no {lanes} — no transition could be guarded")
    # Without it every stage falls back to `COS_MODEL`, and nothing fails.
    model_set = _posix(models.DEFAULT_PATH.relative_to(_HERE))
    if model_set not in names:
        out.append(f"no {model_set} — every stage would run on COS_MODEL, silently")
    # Without it no session is told its name, and nothing fails.
    agent_set = _posix(agents.DEFAULT_PATH.relative_to(_HERE))
    if agent_set not in names:
        out.append(f"no {agent_set} — no session would be told its agent's name, silently")
    # Without it the board shows `commit unknown`.
    stamp = _posix(BUILD_STAMP)
    if stamp not in names:
        out.append(f"no {stamp} — the board could not say which commit it runs")
    else:
        with zipfile.ZipFile(path) as archive:
            try:
                commit = json.loads(archive.read(stamp)).get("commit")
            except ValueError, AttributeError:
                commit = None
        if not isinstance(commit, str) or not _FULL_SHA.fullmatch(commit):
            out.append(
                f"{stamp} carries no 40-hex commit — the board could not say which commit it runs"
            )

    # The copy step takes two named directories, never `.claude/` whole: `.claude/settings.local.json`
    # is personal and a wheel is published. Matched on basename and extension, not substring, so a
    # skill named `write-settings` is not refused.
    leaked = sorted(
        n
        for n in names
        if n.startswith(_posix(_HARNESS) + "/")
        and Path(n).name.startswith("settings")
        and n.endswith(".json")
    )
    if leaked:
        out.append(f"carries settings files that must never be published: {', '.join(leaked)}")
    return out
