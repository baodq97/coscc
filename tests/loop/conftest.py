"""Run `cos.mjs` and `python -m coscc.loop` on the same argv, snapshot, repository and env.

`same(argv, ...)` runs both and asserts stdout, stderr and the exit code are equal, byte for
byte; it returns the one result. `UnitStore` builds a `--root` with units on disk and the snapshot
the app would hand `--state`. `fake_gh` puts a `gh` first on `PATH` that answers from a table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COS_MJS = REPO / ".claude" / "scripts" / "cos.mjs"
# A fixed zone, so `rerun`'s local date is the same date for both.
TZ = "Asia/Ho_Chi_Minh"


@dataclass(frozen=True)
class Ran:
    code: int
    out: str
    err: str


def env(**extra: str) -> dict[str, str]:
    """A clean env: no `COS_*` of the person running the tests, a fixed `TZ`, then `extra`."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("COS_")}
    base["TZ"] = TZ
    base.update(extra)
    return base


def _run(cmd: list[str], argv, environ, stdin, cwd) -> Ran:
    r = subprocess.run(
        [*cmd, *argv],
        input=stdin.encode() if isinstance(stdin, str) else stdin,
        capture_output=True,
        env=env() if environ is None else environ,
        cwd=cwd,
        timeout=60,
        check=False,
    )
    return Ran(r.returncode, r.stdout.decode(), r.stderr.decode())


def node(argv, *, environ=None, stdin=None, cwd=REPO) -> Ran:
    return _run(["node", str(COS_MJS)], argv, environ, stdin, cwd)


def python(argv, *, environ=None, stdin=None, cwd=REPO) -> Ran:
    return _run([sys.executable, "-m", "coscc.loop"], argv, environ, stdin, cwd)


def same(argv, *, environ=None, stdin=None, cwd=REPO) -> Ran:
    """Both, asserted equal; the one result."""
    argv = [str(a) for a in argv]
    a = node(argv, environ=environ, stdin=stdin, cwd=cwd)
    b = python(argv, environ=environ, stdin=stdin, cwd=cwd)
    assert (b.code, b.out, b.err) == (a.code, a.out, a.err), f"argv: {argv}"
    return a


def entry(
    artifacts: dict[str, str] | None = None,
    *,
    type: str | None = "feat",
    links: dict | None = None,
    holds: list | None = None,
    answers: list | None = None,
    unknowns: list | None = None,
    merged: bool = False,
    **artifact_fields: dict,
) -> dict:
    """A snapshot entry as `UnitMeta.snapshot` builds it: `{file: status}` and the rest.

    `artifact_fields` adds keys to one artifact's record, `review_md={"rounds": [...]}` for
    `review.md`.
    """
    arts: dict[str, dict] = {f: {"status": s} for f, s in (artifacts or {}).items()}
    for key, extra in artifact_fields.items():
        arts.setdefault(key.replace("_md", ".md"), {}).update(extra)
    return {
        "artifacts": arts,
        "type": type,
        "links": links or {"idea": None, "repo": None, "dependsOn": None},
        "holds": holds or [],
        "answers": answers or [],
        "unknowns": unknowns or [],
        "merged": merged,
    }


def header(title: str, status: str, kind: str = "Intent", extra: str = "") -> str:
    """A first line and a header line as the skills write them."""
    return f"# {kind}: {title}\n{extra}Author: test. Status: {status}.\n"


class UnitStore:
    """A `--root`: `<root>/.cos/<unit>/<file>`, and the snapshot of it, written on `state()`."""

    def __init__(self, root: Path, workspace: str = "ws"):
        self.root = root
        self.cos = root / ".cos"
        self.cos.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.units: dict[str, dict] = {}
        self.workspaces: list[str] = [workspace]
        self.ideas: dict[str, list] = {}

    def unit(
        self, name: str, files: dict[str, str], known: dict | None = None, ws: str | None = None
    ) -> Path:
        """Writes `files` under `.cos/<name>/` (only for the own workspace) and records `known`."""
        ws = ws or self.workspace
        d = self.cos / name
        if ws == self.workspace:
            d.mkdir(parents=True, exist_ok=True)
            for f, text in files.items():
                (d / f).write_text(text)
        if known is not None:
            self.units[f"{ws}/{name}"] = known
        return d

    def state(self, **override) -> Path:
        snap = {
            "workspace": self.workspace,
            "workspaces": self.workspaces,
            "units": self.units,
            "ideas": self.ideas,
            **override,
        }
        path = self.root / "state.json"
        path.write_text(json.dumps(snap, ensure_ascii=False))
        return path

    def argv(self, *words: str, state: bool = True) -> list[str]:
        """`<words> --root <root> [--state <file>]`."""
        tail = ["--root", str(self.root)]
        if state:
            tail += ["--state", str(self.state())]
        return [*words, *tail]


@pytest.fixture
def store(tmp_path: Path) -> UnitStore:
    return UnitStore(tmp_path / "store")


def fake_gh(bin_dir: Path, answers: dict[str, tuple[int, str, str]]) -> dict[str, str]:
    """A `gh` that prints `answers[" ".join(argv)]`, `(code, out, err)`; the env that finds it.

    An argv not in the table exits 1 and says so on stderr, as an unreachable `gh` would fail.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    table = bin_dir / "gh.json"
    table.write_text(json.dumps({k: list(v) for k, v in answers.items()}))
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"t = json.load(open({str(table)!r}))\n"
        "a = t.get(' '.join(sys.argv[1:]))\n"
        "if a is None:\n"
        "    sys.stderr.write('fake gh: no answer for ' + ' '.join(sys.argv[1:]) + '\\n'); sys.exit(1)\n"
        "sys.stdout.write(a[1]); sys.stderr.write(a[2]); sys.exit(a[0])\n"
    )
    gh.chmod(0o755)
    return env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def git(repo: Path, *args: str) -> str:
    """`git -C repo args`, its stdout stripped; a fixed author and date so shas repeat."""
    stamp = "2026-10-01T00:00:00+00:00"
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            **env(),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t.invalid",
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        },
    )
    return r.stdout.strip()


def git_repo(path: Path) -> Path:
    """An empty repository on `main` with one commit."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("x\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path
