"""`uv run python -m coscc.loop.compare`: R4 of `0151`, the two loops measured on real data.

For every workspace of the app's config it writes the workspace's snapshot to a file once, then
runs `node cos.mjs` and `python -m coscc.loop` on the same argv for `status --json`, `gate <unit>
<stage> --json` (every stage) and `next <unit>` of every unit in its store. A pair differs when the
exit code, stdout or stderr differ. Both see one `gh` shim first on `PATH`: the first call with an
argv asks the real `gh` and records the answer, every later call replays it, so CI moving between
the two runs makes no false difference.

    uv run python -m coscc.loop.compare
    uv run python -m coscc.loop.compare --root <store> --state <file> [--repo <dir>]

Prints the differing pairs to stderr and `pairs: N, different: M` to stdout; exits 1 when M > 0.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from coscc.loop import ROOT, STAGE_NAMES

TZ = "Asia/Ho_Chi_Minh"
TIMEOUT = 120
WORKERS = 8
DIFF_LINES = 20

# The `gh` placed first on `PATH`. It is a script of its own: it runs under the same interpreter
# and imports nothing of coscc. One record file per (cwd, argv), a lock file per record so two
# first calls with the same key ask the real `gh` once.
SHIM = """\
import base64, fcntl, hashlib, json, os, subprocess, sys

argv = sys.argv[1:]
d = os.environ["COSCC_COMPARE_GH_DIR"]
real = os.environ.get("COSCC_COMPARE_GH_REAL") or ""
key = hashlib.sha256(json.dumps([os.getcwd(), argv]).encode()).hexdigest()
path = os.path.join(d, key + ".json")


def b64(b):
    return base64.b64encode(b).decode()


def ask():
    if not real:
        return {"code": 127, "out": b64(b""), "err": b64(b"gh: command not found\\n")}
    try:
        r = subprocess.run([real, *argv], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    except subprocess.TimeoutExpired:
        return {"code": 124, "out": b64(b""), "err": b64(b"gh: timed out\\n")}
    except OSError as e:
        return {"code": 127, "out": b64(b""), "err": b64(str(e).encode() + b"\\n")}
    return {"code": r.returncode, "out": b64(r.stdout), "err": b64(r.stderr)}


with open(path + ".lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if os.path.exists(path):
        with open(path) as f:
            rec = json.load(f)
    else:
        rec = {"cwd": os.getcwd(), "argv": argv, **ask()}
        with open(path + ".tmp", "w") as f:
            json.dump(rec, f)
        os.replace(path + ".tmp", path)

sys.stdout.buffer.write(base64.b64decode(rec["out"]))
sys.stdout.buffer.flush()
sys.stderr.buffer.write(base64.b64decode(rec["err"]))
sys.stderr.buffer.flush()
sys.exit(rec["code"])
"""


def install_shim(work: Path, real: str | None) -> dict[str, str]:
    """Writes the shim under `work`; the env entries that put it first on `PATH` and point it home."""
    bin_dir = work / "bin"
    records = work / "records"
    bin_dir.mkdir(parents=True, exist_ok=True)
    records.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{SHIM}")
    gh.chmod(0o755)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "COSCC_COMPARE_GH_DIR": str(records),
        "COSCC_COMPARE_GH_REAL": real or "",
    }


@dataclass(frozen=True)
class Ran:
    code: int
    out: str
    err: str


@dataclass(frozen=True)
class Program:
    name: str
    cmd: tuple[str, ...]


NODE = Program("node", ("node", str(ROOT / ".claude" / "scripts" / "cos.mjs")))
PYTHON = Program("python", (sys.executable, "-m", "coscc.loop"))


@dataclass(frozen=True)
class Verdict:
    argv: tuple[str, ...]
    results: tuple[Ran, Ran]

    @property
    def differs(self) -> bool:
        return self.results[0] != self.results[1]


def base_env(extra: dict[str, str]) -> dict[str, str]:
    """The person's env without any `COS_*`, a fixed `TZ`, then `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COS_")}
    env["TZ"] = TZ
    env.update(extra)
    return env


def run_one(program: Program, argv: Sequence[str], env: dict[str, str]) -> Ran:
    try:
        r = subprocess.run(
            [*program.cmd, *argv],
            capture_output=True,
            env=env,
            cwd=ROOT,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Ran(-1, "", f"{program.name}: timed out after {TIMEOUT}s\n")
    return Ran(
        r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    )


def run_pair(
    argv: Sequence[str], programs: tuple[Program, Program], env: dict[str, str]
) -> Verdict:
    return Verdict(
        tuple(argv),
        (run_one(programs[0], argv, env), run_one(programs[1], argv, env)),
    )


def run_pairs(
    pairs: Sequence[Sequence[str]],
    env: dict[str, str],
    programs: tuple[Program, Program] = (NODE, PYTHON),
    workers: int = WORKERS,
) -> list[Verdict]:
    """Every argv through both programs, `workers` at a time; the verdicts in the order given."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda a: run_pair(a, programs, env), pairs))


def _diff(label: str, a: str, b: str, names: tuple[str, str]) -> list[str]:
    lines = list(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=f"{names[0]} {label}",
            tofile=f"{names[1]} {label}",
        )
    )
    shown = [ln if ln.endswith("\n") else ln + "\n" for ln in lines[:DIFF_LINES]]
    if len(lines) > DIFF_LINES:
        shown.append(f"... {len(lines) - DIFF_LINES} more diff lines\n")
    return shown


def report(v: Verdict, names: tuple[str, str] = ("node", "python")) -> str:
    """One differing pair: its argv, the codes, and a short diff of stdout and stderr."""
    a, b = v.results
    out = [f"DIFFERENT: {' '.join(v.argv)}\n"]
    if a.code != b.code:
        out.append(f"  exit code: {names[0]} {a.code}, {names[1]} {b.code}\n")
    if a.out != b.out:
        out += ["  " + ln for ln in _diff("stdout", a.out, b.out, names)]
    if a.err != b.err:
        out += ["  " + ln for ln in _diff("stderr", a.err, b.err, names)]
    return "".join(out)


# --- what to compare -------------------------------------------------------------------


def units_of(store: Path) -> list[str]:
    """The unit directories of `<store>/.cos`, `ideas/` and hidden ones left out."""
    cos = store / ".cos"
    if not cos.is_dir():
        return []
    return sorted(
        p.name
        for p in cos.iterdir()
        if p.is_dir() and p.name != "ideas" and not p.name.startswith(".")
    )


def pairs_for(store: Path, state: Path, repo: Path | None) -> list[list[str]]:
    """`status --json` once, then `gate` for every stage and `next` for every unit of `store`."""
    tail = ["--root", str(store), "--state", str(state)]
    where = ["--repo", str(repo)] if repo else []
    pairs = [["status", "--json", *tail]]
    for unit in units_of(store):
        pairs += [["gate", unit, s, "--json", *where, *tail] for s in STAGE_NAMES]
        pairs.append(["next", unit, *where, *tail])
    return pairs


@dataclass(frozen=True)
class Workspace:
    path: str
    store: Path
    snapshot: str


def workspaces() -> list[Workspace]:
    """Every workspace of the app's config that has a store, with its snapshot as JSON text.

    Found as `snapshot.from_db` finds them: the app's own store, then `config.workspaces`; a
    shared name, or one `valid_name` refuses, gets none.
    """
    from coscc import units
    from coscc.config import from_env
    from coscc.data import Data
    from coscc.service.store import Store, valid_name
    from coscc.units.meta import UnitMeta

    config = from_env()
    if not config.working_dir:
        return []
    data = Data(config.data_dir)
    store = Store(config.working_dir, data)
    rows = [(e.name, str(store.path_of(e.name))) for e in store.entries()]
    rows += [(Path(p).name, p) for p in config.workspaces]
    count = Counter(name for name, _ in rows)
    names = {n: units.key(p) for n, p in rows if count[n] == 1 and valid_name(n)}
    meta = UnitMeta(config.working_dir, data)
    found: list[Workspace] = []
    seen: set[str] = set()
    for _, p in rows:
        key = units.key(p)
        root = Path(units.root(key, config.data_dir))
        if key in seen or not (root / ".cos").is_dir():
            continue
        seen.add(key)
        text = json.dumps(meta.snapshot(key, names), ensure_ascii=False)
        found.append(Workspace(p, root, text))
    return found


def collect(work: Path) -> list[list[str]]:
    """The pairs of every workspace, each snapshot written to `work` once."""
    pairs: list[list[str]] = []
    for i, ws in enumerate(workspaces()):
        state = work / f"state-{i}.json"
        state.write_text(ws.snapshot, encoding="utf-8")
        repo = Path(ws.path)
        pairs += pairs_for(ws.store, state, repo if repo.is_dir() else None)
    return pairs


# --- command line ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m coscc.loop.compare",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", help="compare one store (its .cos/ parent); needs --state")
    ap.add_argument("--state", help="the snapshot file to give both programs, with --root")
    ap.add_argument("--repo", help="the --repo of gate and next, with --root")
    ap.add_argument("--gh-real", help="the real gh (default: the one found on PATH)")
    args = ap.parse_args(argv)
    if bool(args.root) != bool(args.state):
        ap.error("--root and --state come together")
    if args.repo and not args.root:
        ap.error("--repo goes with --root")
    real = args.gh_real or shutil.which("gh")
    with tempfile.TemporaryDirectory(prefix="coscc-compare-") as tmp:
        work = Path(tmp)
        if args.root:
            repo = Path(args.repo) if args.repo else None
            pairs = pairs_for(Path(args.root), Path(args.state), repo)
        else:
            pairs = collect(work)
        verdicts = run_pairs(pairs, base_env(install_shim(work / "gh", real)))
    bad = [v for v in verdicts if v.differs]
    for v in bad:
        sys.stderr.write(report(v))
    print(f"pairs: {len(verdicts)}, different: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
