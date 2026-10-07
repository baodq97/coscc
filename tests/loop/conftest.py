"""Run `python -m coscc.loop` and hold what it says to a golden.

`expect(argv, ...)` runs it and asserts stdout, stderr and the exit code equal, byte for byte, what
`tests/loop/golden/<file>.json` holds under `<nodeid>#<n>`, the n-th call of the test, with a
directory under pytest's tmp, the checkout and today's date read as `<tmp>`, `<repo>` and `<today>`; it
returns the result. The goldens were written by the JavaScript loop the Python one replaced, so a
golden that changes is a decision a reviewer sees in the diff. `UnitStore` builds a `--root` with
units on disk and the snapshot the app would hand `--state`. `fake_gh` puts a `gh` first on `PATH`
that answers from a table.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.inprocess import in_process

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"
# A fixed zone, so `rerun`'s local date is one date for every run of a day.
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


def python(argv, *, environ=None, stdin=None, cwd=REPO, child=False) -> Ran:
    """What `python -m coscc.loop argv` prints and exits with.

    Run in this process: a child's start costs more than most answers, and the goldens hold
    thousands of them. `child=True` runs the real command, for the tests of the command itself.
    """
    data = stdin.encode() if isinstance(stdin, str) else stdin
    environ = env() if environ is None else environ
    if child:
        r = subprocess.run(
            [sys.executable, "-m", "coscc.loop", *argv],
            input=data,
            capture_output=True,
            env=environ,
            cwd=cwd,
            timeout=60,
            check=False,
        )
        return Ran(r.returncode, r.stdout.decode(), r.stderr.decode())
    return Ran(*in_process(argv, data, cwd, environ))


class _Test:
    """The test running in this process: its id, its tmp path and how many calls it made."""

    nodeid = ""
    base: Path | None = None
    calls = 0


@pytest.fixture(autouse=True)
def _golden_key(request, tmp_path_factory):
    _Test.nodeid, _Test.base, _Test.calls = request.node.nodeid, tmp_path_factory.getbasetemp(), 0
    yield


def _plain(text: str) -> str:
    """`text` with what differs between runs read as a placeholder."""
    if _Test.base is not None:
        # Each test's or module's own directory under pytest's, whichever process ran it.
        text = re.sub(re.escape(str(_Test.base)) + r"/[^/\s\"']+", "<tmp>", text)
    text = text.replace(str(REPO), "<repo>")
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    return text.replace(today, "<today>")


def _golden_file(nodeid: str) -> Path:
    return GOLDEN / f"{Path(nodeid.split('::')[0]).stem}.json"


def expect(argv, *, environ=None, stdin=None, cwd=REPO) -> Ran:
    """`python -m coscc.loop argv`, asserted equal to its golden; its result."""
    argv = [str(a) for a in argv]
    _Test.calls += 1
    key = f"{_Test.nodeid}#{_Test.calls}"
    got = python(argv, environ=environ, stdin=stdin, cwd=cwd)
    table = json.loads(_golden_file(_Test.nodeid).read_text())
    # A new case has no golden to be held to: it asserts fixed values instead.
    assert key in table, f"no golden for {key}: assert what the command says in the test"
    said, want = [got.code, _plain(got.out), _plain(got.err)], table[key]
    assert said == want, f"argv: {argv}"
    return got


def entry(
    artifacts: dict[str, str] | None = None,
    *,
    type: str | None = "feat",
    links: dict | None = None,
    holds: list | None = None,
    answers: list | None = None,
    unknowns: list | None = None,
    merged: bool = False,
    shipped: bool = False,
    reruns: list | None = None,
    rounds_granted: int = 0,
    **artifact_fields: dict,
) -> dict:
    """A snapshot entry as `UnitMeta.snapshot` builds it: `{file: status}` and the rest.

    `artifact_fields` adds keys to one artifact's entry: `review_md={"rounds": [round_row(...)]}`,
    `pr_md=pr_row(7)`, `ship_md={"merge": {"round": 1, "refused": None}}`, `spec_md={"record": 3}`.
    `reruns` are `rerun_row(...)`s; `rounds_granted` the more rounds a person allowed.
    """
    arts: dict[str, dict] = {f: {"status": s} for f, s in (artifacts or {}).items()}
    for key, extra in artifact_fields.items():
        arts.setdefault(key.replace("_md", ".md"), {}).update(extra)
    return {
        "artifacts": arts,
        "type": type,
        "links": links or {"idea": None, "dependsOn": None},
        "holds": holds or [],
        "answers": answers or [],
        "unknowns": unknowns or [],
        "merged": merged,
        "shipped": shipped,
        "reruns": reruns or [],
        "roundsGranted": rounds_granted,
    }


def pr_row(number: int = 7, url: str | None = None, record: int = 1) -> dict:
    """`pr.md`'s entry fields: the pull request the PR machine's last `open` recorded."""
    return {
        "pr": {"number": number, "url": url or f"https://github.com/o/r/pull/{number}"},
        "record": record,
    }


def finding_row(
    id: str,
    label: str,
    severity: str = "high",
    text: str = "the function is wrong",
    path: str = "src/a.py",
    lines: str = "",
    rule: str = "",
    fixed_in: str | None = None,
) -> dict:
    """One `review_findings` row as the snapshot carries it."""
    return {
        "id": id,
        "label": label,
        "fixedIn": fixed_in,
        "severity": severity,
        "rule": rule,
        "path": path,
        "lines": lines,
        "text": text,
    }


def round_row(
    n: int, verdict: str, reviewed: str, *findings: dict, screens: dict | None = None
) -> dict:
    """One `review_rounds` row: `screens` is `{taken, standard, by, shots: [{path, size,
    address, result}]}` or none."""
    return {
        "n": n,
        "reviewed": reviewed,
        "verdict": verdict,
        "screens": screens or {},
        "findings": list(findings),
    }


def rerun_row(stage: str, date: str = "2026-10-01", **stale: int) -> dict:
    """A person's rerun of `stage`: `stale` maps a file (`spec_md=3`) to the record it held."""
    return {
        "stage": stage,
        "stale": {k.replace("_md", ".md"): v for k, v in stale.items()},
        "date": date,
    }


# Set by `test_reads_no_artifact`: every header then says what no row says, so a decision read
# from a file's text instead of the snapshot shows as a changed golden.
CONTRADICT = "COS_LOOP_CONTRADICT"


def header(title: str, status: str, kind: str = "Intent", extra: str = "") -> str:
    """A first line and a header line as the skills write them. Under `CONTRADICT`, a status
    and a type no row holds, a skip reason and a spike citation, and an open question; `pr.md`
    keeps its lines, which become the pull request's body."""
    if os.environ.get(CONTRADICT):
        status = "rejected"
        if kind != "PR":
            extra = f"Type: docs. Spec: skipped (contradiction). Cites spike.md ## U1.\n{extra}"
            tail = "\n## Open questions\n1. Is this read?\n\n"
            return f"# {kind}: {title}\n{extra}Author: test. Status: {status}.\n{tail}"
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


def at_version(version: str) -> dict[str, str]:
    """The four files `check-version` reads, each declaring `version`."""
    return {
        "pyproject.toml": f'[project]\nname = "elsewhere"\nversion = "{version}"\n',
        "package.json": json.dumps({"name": "elsewhere", "version": version}),
        "uv.lock": f'[[package]]\nname = "elsewhere"\nversion = "{version}"\n',
        "package-lock.json": json.dumps(
            {"version": version, "packages": {"": {"version": version}}}
        ),
    }
