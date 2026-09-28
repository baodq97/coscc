"""`0135` R5, measured once: every real store, read before the import and after it.

Never on `~/.cos` itself (plan Risk 1). `cos.db` is copied with SQLite's backup API and the
stores under `units/` are copied beside it, into a temporary directory; the import and every
read run there, and the directory is removed at the end.

- **Before** is `cos.mjs` at `git merge-base HEAD origin/main`, run with `--root` and the
  `--peer`s `Service._peer_table` passes.
- **After** is this checkout's `cos.mjs` with `--state`, the snapshot `UnitMeta` builds from
  the imported copy.

It compares the fields R5 lists, never `problems` (spec C6), and ends with
`units: <n>`, `without unit_meta: <n>` and `mismatches: <n>`. Exit 0 only when the first is
above 0 and the other two are 0.

    uv run python scripts/verify_0135.py [--data ~/.cos]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from coscc import units  # noqa: E402
from coscc.agent import harness  # noqa: E402
from coscc.data import Data  # noqa: E402
from coscc.service.store import valid_name  # noqa: E402
from coscc.units.meta import UnitMeta  # noqa: E402


def r5(unit: dict) -> dict:
    return {
        "artifacts": {f: a.get("status") for f, a in unit["artifacts"].items()},
        "phase": unit.get("phase"), "type": unit.get("type"), "hold": unit.get("hold"),
        "holdMoves": unit.get("holdMoves"), "questions": unit.get("questions"), "open": unit.get("open"),
        "dependsOn": [(d["ref"], d["merged"]) for d in unit.get("dependsOn") or []],
        "next": (unit["next"]["stage"], unit["next"]["why"]),
    }


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def status(script: Path, store: Path, *extra: str, stdin: str | None = None) -> dict:
    done = subprocess.run(
        ["node", str(script), "--root", str(store), *extra, "status", "--json"],
        input=stdin, capture_output=True, text=True, env=harness.child_env(),
    )
    if done.returncode != 0:
        raise SystemExit(f"{script} status on {store} exited {done.returncode}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(Path("~/.cos").expanduser()))
    args = parser.parse_args()
    source = Path(args.data).expanduser()

    tmp = Path(tempfile.mkdtemp(prefix="verify_0135-"))
    try:
        data_dir = tmp / "data"
        (data_dir / "units").mkdir(parents=True)
        with sqlite3.connect(f"file:{source / 'cos.db'}?mode=ro", uri=True) as src, \
                sqlite3.connect(data_dir / "cos.db") as dst:
            src.backup(dst)
        for store in sorted((source / "units").iterdir()):
            if store.is_dir():
                shutil.copytree(store, data_dir / "units" / store.name, symlinks=True)

        # The harness of the base commit, whole, so the old script reads its own rules.
        base = git("merge-base", "HEAD", "origin/main").strip()
        before_repo = tmp / "before"
        shutil.copytree(REPO / ".claude", before_repo / ".claude")
        old = before_repo / ".claude" / "scripts" / "cos.mjs"
        old.write_text(git("show", f"{base}:.claude/scripts/cos.mjs"), encoding="utf-8")

        data = Data(data_dir)
        with data.connect() as conn:
            rows = [(r["root"], r["name"]) for r in conn.execute("SELECT root, name FROM workspaces")]
        paths = {name: str(Path(root) / name) for root, name in rows}
        counts: dict[str, int] = {}
        for _, name in rows:
            counts[name] = counts.get(name, 0) + 1
        peers = {n: p for n, p in paths.items() if counts[n] == 1 and valid_name(n)}

        units_seen = without_meta = mismatches = 0
        by_root: dict[str, UnitMeta] = {}
        for name, path in sorted(paths.items()):
            store = units.root(path, data_dir)
            if not (store / ".cos").is_dir():
                continue
            root = next(r for r, n in rows if n == name)
            meta = by_root.setdefault(root, UnitMeta(root, data))
            for other, p in peers.items():
                other_store = units.root(p, data_dir)
                if (other_store / ".cos").is_dir():
                    meta.import_store(units.key(p), other_store)
            meta.import_store(units.key(path), store)

            peer_args = [a for n, p in peers.items() for a in ("--peer", f"{n}={units.root(p, data_dir)}")]
            before = status(old, store, *peer_args)
            snapshot = meta.snapshot(units.key(path), {n: units.key(p) for n, p in peers.items()})
            after = status(harness.script(), store, "--state", "-", stdin=json.dumps(snapshot))

            with data.connect() as conn:
                known = {r[0] for r in conn.execute(
                    "SELECT unit FROM unit_meta WHERE workspace = ?", (units.key(path),))}
            dirs = {d.name for d in (store / ".cos").iterdir() if d.is_dir() and d.name != "ideas"}
            for d in sorted(dirs - known):
                without_meta += 1
                print(f"{name}/{d}: no unit_meta row")

            after_by = {u["name"]: u for u in after["units"]}
            for old_unit in before["units"]:
                units_seen += 1
                new_unit = after_by.get(old_unit["name"])
                if new_unit is None:
                    mismatches += 1
                    print(f"{name}/{old_unit['name']}: missing after the import")
                    continue
                a, b = r5(old_unit), r5(new_unit)
                for field in a:
                    if a[field] != b[field]:
                        mismatches += 1
                        print(f"{name}/{old_unit['name']}: {field}: before {a[field]!r}, after {b[field]!r}")
            print(f"{name}: {len(before['units'])} units, {len(meta.unknowns([units.key(path)]))} unknown fields")

        print(f"units: {units_seen}")
        print(f"without unit_meta: {without_meta}")
        print(f"mismatches: {mismatches}")
        return 0 if units_seen > 0 and without_meta == 0 and mismatches == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
