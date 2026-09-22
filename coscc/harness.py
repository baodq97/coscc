"""Where the rules this app runs live, and the refusal when they are not there.

Two things in this app are read from outside `coscc/`: the compiled frontend, and the
harness — `cos.mjs`, which decides what a stage's status means, and the skills, which are
the text a step is told to follow. `0011` gave the frontend a packaged home
(`coscc/frontend.py:90`) and a release step that copies it there. The harness got neither,
and `.cos/0012_installed-copy-runs-no-stage/intent.md` is the measurement of what that
cost: on `v0.2.2` installed from the release, `coscc/board.py` answered 400 and a step ran
with 4.569 characters of its rules missing and no record that they had been.

This module is the second half of that decision, and it is deliberately the *same* half.
`root()` mirrors `coscc/frontend.py:106-113`: **the packaged copy wins when it is there.**
A wheel has no checkout to fall back to, and a checkout has no `coscc/_harness/` unless
somebody built one.

**It is not a second copy of the rules.** `.claude/CLAUDE.md` says `cos.mjs` is the one
place the loop is defined and nothing may hold a second copy of it, so `coscc/_harness/`
is generated at release time and gitignored — exactly what `coscc/_web/` already is
(`.gitignore:2`). The cost of that choice is written down in
`.cos/0012_installed-copy-runs-no-stage/spec.md` C3: what makes a wheel correct is a build
step, not a file anyone can read in the tree. `wheel_complaints` below is the answer to
that — the check that would have caught `v0.2.2`, written so a person can run it on their
own machine and not only so CI can.

**Which copy runs is still the security decision `coscc/board.py:8-14` made.** Nothing here
ever looks inside a workspace. A workspace is a repository cloned from a URL somebody
typed; this module only ever answers with a path under this app's own installation or its
own checkout.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from coscc import frontend

_HERE = Path(__file__).resolve().parent

# Where the release puts the harness inside the wheel. `pyproject.toml:29-30` ships
# everything under `coscc/`, so this is the one place a packaged tree can live -- the same
# sentence, for the same reason, as `coscc/frontend.py:88-90`.
PACKAGE_HARNESS = _HERE / "_harness"

# The checkout's own copy, one level up beside `coscc/`. This is the `parent.parent` that
# `coscc/board.py:31` and `coscc/runner.py:39` each computed for themselves until 0012 --
# one bug that arrived as two symptoms, because two places held the same formula.
CHECKOUT_HARNESS = _HERE.parent / ".claude"

_SCRIPTS = Path("scripts")
_SKILLS = Path("skills")
SCRIPT_NAME = "cos.mjs"
SKILL_FILE = "SKILL.md"


class MissingRules(RuntimeError):
    """The rules for a step could not be found, carrying the paths that were searched.

    Raised rather than returned. Until 0012 this case returned an empty string and the
    step ran anyway (`coscc/runner.py`, *"Missing is not fatal"*), which is how a step came
    to spend real quota on a prompt with no rules in it and leave a record indistinguishable
    from one that had them. `spec.md` C2 records that this reverses a decision that had
    reasons written down.
    """


def is_packaged() -> bool:
    """Whether this install carries its own harness."""
    return (PACKAGE_HARNESS / _SCRIPTS / SCRIPT_NAME).is_file()


def root() -> Path:
    """The `.claude`-shaped directory this app reads its rules from."""
    return PACKAGE_HARNESS if is_packaged() else CHECKOUT_HARNESS


def script() -> Path:
    """`cos.mjs`. May not exist -- callers say so in their own vocabulary."""
    return root() / _SCRIPTS / SCRIPT_NAME


def skills_dir() -> Path:
    return root() / _SKILLS


def read_skill(*names: str) -> str:
    """The first skill that exists, by name, in order.

    Raises `MissingRules` naming every path tried. The names are the caller's, because
    which skill a stage uses is the caller's business; where skills live is this module's.
    """
    tried = []
    for name in names:
        path = skills_dir() / name / SKILL_FILE
        tried.append(str(path))
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise MissingRules("no rules found; looked at " + ", ".join(tried))


# --- what a runnable wheel must contain --------------------------------------
#
# `.github/workflows/release.yml:111-113` already wrote the reasoning for the frontend
# half: without the copy step the wheel still builds, still installs, and is only missing
# something -- and nothing else in the workflow notices. That was true of the harness too,
# for three releases. This function is that check generalised, and it lives here rather
# than in YAML so the same answer is available to someone building a wheel by hand.

# Reaching for `frontend._LAYOUT` rather than writing "build/client" again is deliberate.
# `release.yml:75-81` records what that string means and why flattening it breaks the page;
# a second copy of it here is the drift that comment exists to prevent.
_WEB = frontend.PACKAGE_WEB.name
_HARNESS = PACKAGE_HARNESS.name


def _posix(*parts: object) -> str:
    return Path("coscc", *[str(p) for p in parts]).as_posix()


def wheel_complaints(wheel: str | Path) -> list[str]:
    """Everything wrong with `wheel`, as sentences. Empty means it would run.

    Four entries, and each one names a wheel that installs cleanly and then fails in a
    different way:

    - no frontend: the page 404s while `/api/health` answers;
    - no compile marker: the service reports `active` and serves nothing at all (measured
      2026-09-22 on a clean Debian 13 VM, `coscc/frontend.py:64-70`);
    - no `cos.mjs`: the Board answers 400 (measured 2026-09-22 on `v0.2.2`);
    - no skills: a step runs without its rules.

    The skills check counts rather than naming nine, because nine is today's number
    (`.cos/0012_installed-copy-runs-no-stage/spec.md` C4) and a list copied by hand stops
    being true the day a tenth is written.
    """
    path = Path(wheel)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as e:
        return [f"{path} could not be read as a wheel: {e}"]

    index = _posix(_WEB, frontend._LAYOUT, "index.html")
    marker = _posix(_WEB, frontend.MARKER)
    cos = _posix(_HARNESS, _SCRIPTS, SCRIPT_NAME)
    skills_prefix = _posix(_HARNESS, _SKILLS) + "/"

    out = []
    if index not in names:
        out.append(f"no {index} — it would install and serve no page")
    if marker not in names:
        out.append(f"no {marker} — it would report active and never serve")
    if cos not in names:
        out.append(f"no {cos} — the Board would answer 400 on every read")
    found = sum(1 for n in names if n.startswith(skills_prefix) and n.endswith("/" + SKILL_FILE))
    if not found:
        # Not "would run without its rules" -- that was true until 0012 and this same
        # change is what ended it. A wheel in this shape reads the Board fine and refuses
        # every Run, which is a different thing to go looking for.
        out.append(f"no {skills_prefix}*/{SKILL_FILE} — every step would refuse to run")

    # The copy step takes two named directories, never `.claude/` whole. This is what says
    # so out loud: `.claude/settings.local.json` is a personal file (`.gitignore:19`) and a
    # wheel is published. `plan.md` Risk 6.
    # Matched on the basename and the extension, not on the substring. `"settings" in n`
    # was the first version of this line and it would refuse a release over a skill
    # legitimately named `write-settings` -- a check that fires on correct input is worse
    # than the check it replaced, because the way past it is to delete it.
    leaked = sorted(
        n for n in names
        if n.startswith(_posix(_HARNESS) + "/")
        and Path(n).name.startswith("settings")
        and n.endswith(".json")
    )
    if leaked:
        out.append(f"carries settings files that must never be published: {', '.join(leaked)}")
    return out
