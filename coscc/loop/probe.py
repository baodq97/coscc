"""The probe: the one way `review`, `ship` and `screens` reach outside the unit's own files.

The UI standard and its globs, and `make_probe`. The probe runs `git` and `gh` through `PATH` in the repository the unit's code
lives in, and reads three files of it; a test hands the rules a fake with the same five calls.
Each command answers `{"code", "out", "err"}`; a command that could not start is code -1.
"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
from typing import Any

from coscc.loop import JS_SPACE, read_text, split_lines, trim

# The UI standard: its rules for a person to read, and in its front-matter `paths:` the one list
# of files that count as the app's screens. Read from the repository being diffed.
UI_STANDARD = ".claude/rules/ui-standard.md"
# Where `scripts/capture_screens.py` writes its manifest, relative to the repository.
SCREENS_MANIFEST = ".screens/manifest.json"

# JavaScript's `\s` and `.`, for the regexes below that run over lines of a file.
S = "[" + re.escape(JS_SPACE) + "]"
NOT_S = "[^" + re.escape(JS_SPACE) + "]"
DOT = "[^\n\r  ]"

PATHS_KEY = re.compile(f"paths:{S}*")
PATHS_ITEM = re.compile(f"{S}+-{S}+({DOT}+?){S}*")
QUOTED = re.compile(f"([\"'])({DOT}*)\\1")


def parse_standard(text):
    """The globs under `paths:` in the front-matter between the first two `---` lines, quotes
    dropped. `[]` when there is no front-matter, no `paths:`, or nothing under it."""
    lines = split_lines(text)
    if trim(lines[0]) != "---":
        return []
    end = next((i for i, line in enumerate(lines) if i > 0 and trim(line) == "---"), -1)
    if end == -1:
        return []
    globs = []
    in_paths = False
    for line in lines[1:end]:
        if PATHS_KEY.fullmatch(line):
            in_paths = True
            continue
        item = PATHS_ITEM.fullmatch(line)
        if in_paths and item:
            quoted = QUOTED.fullmatch(item[1])
            globs.append(quoted[2] if quoted else item[1])
        elif not re.fullmatch(f"{S}*", line):
            in_paths = False
    return globs


def glob_match(glob, path):
    """A glob as the rule files write one, by hand so as to add no dependency: `**/` is zero or
    more directories, a trailing `**` is anything, `*` stays inside one directory, `?` is one
    character that is not `/`, and every other character is itself."""
    pattern = ""
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*" and glob[i + 1 : i + 2] == "*":
            if glob[i + 2 : i + 3] == "/":
                pattern += f"(?:{DOT}*/)?"
                i += 2
            else:
                pattern += f"{DOT}*"
                i += 1
        elif c == "*":
            pattern += "[^/]*"
        elif c == "?":
            pattern += "[^/]"
        else:
            pattern += re.escape(c)
        i += 1
    return re.fullmatch(pattern, path) is not None


def ui_files(paths, globs):
    """The paths among `paths` that some glob matches, in their order."""
    return [p for p in paths if any(glob_match(g, p) for g in globs)]


def _number(text):
    """`JSON.parse` of a number with a fraction or an exponent, as `JSON.stringify` prints it."""
    x = float(text)
    if x in (float("inf"), float("-inf")):
        return None
    return int(x) if x.is_integer() and abs(x) < 1e21 else x


def _refuse(name):
    raise ValueError(name)


def parse_json(text):
    """`JSON.parse(text)`, or `None` where it throws: what a `try`/`catch` around it gives."""
    try:
        return json.loads(text, parse_float=_number, parse_constant=_refuse)
    except ValueError, RecursionError:
        return None


def _spawned(cmd, args, repo_dir):
    """`spawnSync(cmd, args, { cwd: repoDir, encoding: 'utf8' })` as `{ code, out, err }`."""
    try:
        r = subprocess.run(
            [cmd, *args],
            cwd=repo_dir,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as e:
        name = errno.errorcode.get(e.errno or 0, str(e))
        return {"code": -1, "out": "", "err": f"spawnSync {cmd} {name}"}
    return {
        "code": None if r.returncode < 0 else r.returncode,
        "out": r.stdout.decode("utf-8", "replace"),
        "err": r.stderr.decode("utf-8", "replace"),
    }


class Probe:
    """`git`, `gh`, `ui`, `manifest` and `workflows` of one repository (`makeProbe`)."""

    def __init__(self, repo_dir):
        self.repo_dir = repo_dir

    def git(self, *args):
        return _spawned("git", args, self.repo_dir)

    def gh(self, *args):
        return _spawned("gh", args, self.repo_dir)

    def ui(self):
        """the UI standard of that repository, read from its files rather than `git`, or
        `None` without one."""
        path = os.path.join(self.repo_dir, UI_STANDARD)
        if not os.path.exists(path):
            return None
        return {"path": UI_STANDARD, "globs": parse_standard(read_text(path))}

    def manifest(self) -> Any:
        """what `scripts/capture_screens.py` last wrote in that repository, or `None`
        with no file there or one that is not JSON."""
        try:
            text = read_text(os.path.join(self.repo_dir, SCREENS_MANIFEST))
        except OSError:
            return None
        return parse_json(text)

    def workflows(self):
        """every workflow of that repository as `{ path, text }`, `[]` with no
        `.github/workflows/` or one that cannot be read."""
        folder = os.path.join(self.repo_dir, ".github", "workflows")
        try:
            names = [f for f in os.listdir(folder) if re.search(r"\.ya?ml\Z", f)]
            names.sort(key=lambda f: f.encode("utf-16-be", "surrogatepass"))
            return [
                {"path": f".github/workflows/{f}", "text": read_text(os.path.join(folder, f))}
                for f in names
            ]
        except OSError:
            return []


def make_probe(repo_dir):
    return Probe(repo_dir)
