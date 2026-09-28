"""`0137` proof: does every answer in force say who decided it, and does no inference alone
decide a question Jera writes?

The intent's outcome (R12), on the app's own data, by 2026-10-19:
  (a) the answers in force in `intent.md` and `spec.md` of every workspace, by code;
  (b) how many have no code;
  (c) how many carry an agent's name in `Answered by:` and read as `originator`;
  (d) how many `precedent` rows with `written: true` cite no `originator`, `delegated` or
      `practice` entry — only rows that carry `cite_who`, which a release with `0137` writes;
  (e) how many verdicts R8 took to `needs-person`.
Met when (b), (c) and (d) are all 0.

No session, no quota, no network, and nothing written. It opens `<COS_DATA_DIR>/cos.db`
(default `~/.cos`) with `mode=ro`, never through `Data`, which would raise the schema on a
database an older build still reads. Since `0135` a board is read on the app's snapshot, so
the database is copied into a temporary directory first and every store is imported there. The workspaces are the app's: `COS_WORKSPACES`, and the
`workspaces` rows under `COS_WORKING_DIR`. Each board is read by `cos.mjs`, so `node` is
needed; the codes are `coscc.agent.precedent.decided_by`'s. Run it at a terminal on the
machine the app runs on — inside a step `COS_DATA_DIR` is that step's scratch root (`0076`).

    uv run python scripts/verify_0137.py --measure

Exit codes: 0 met, 1 (b), (c) or (d) is not 0, 2 no `node`, no `cos.db` or no `--measure`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc import units  # noqa: E402
from coscc.agent import agents, precedent  # noqa: E402
from coscc.config import from_env  # noqa: E402
from coscc.data import Data, Incompatible  # noqa: E402
from coscc.units import board as board_reader  # noqa: E402
from coscc.units.board import Unavailable  # noqa: E402
from coscc.units.meta import MetaError, UnitMeta  # noqa: E402
from coscc.units.history import BadTransition  # noqa: E402

FROM = ("intent.md", "spec.md")
MINE = "answer_names_mine"


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _read(db: Path, working_dir: str) -> dict:
    """Everything the measure needs from `cos.db`, read once and read-only."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        prefs = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM prefs")} if _table(conn, "prefs") else {}
        names = []
        if working_dir and _table(conn, "workspaces"):
            root = str(Path(working_dir).expanduser().resolve())
            names = [r["name"] for r in conn.execute("SELECT name FROM workspaces WHERE root = ? ORDER BY rowid", (root,))]
        decisions = ([{k: r[k] for k in r.keys()} for r in conn.execute("SELECT * FROM decisions ORDER BY id")]
                     if _table(conn, "decisions") else [])
        rows = []
        if _table(conn, "runs"):
            for (text,) in conn.execute("SELECT record FROM runs WHERE kind = 'precedent' ORDER BY id"):
                try:
                    record = json.loads(text)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    rows.append(record)
    finally:
        conn.close()
    return {"prefs": prefs, "names": names, "decisions": decisions, "precedent": rows}


def _agent_names(prefs: dict[str, str]) -> list[str]:
    """`Service.agent_names`, from the rows read here rather than through `Data`."""
    defaults, _ = agents.load_defaults()
    overrides, _ = agents.overrides_from({k: v for k, v in prefs.items() if k.startswith(agents.PREFIX)})
    found = [str(r.get("name") or "") for r in (*defaults.values(), *overrides.values())]
    return [n for n in dict.fromkeys([*found, *precedent.AGENTS_ALWAYS]) if n]


def _mine(prefs: dict[str, str]) -> list[str]:
    try:
        value = json.loads(prefs.get(MINE) or "[]")
    except ValueError:
        return []
    return [str(n) for n in value] if isinstance(value, list) else []


def _copy(db: Path, into: Path, working_dir: str) -> UnitMeta:
    """`0135`: a board is read on the app's snapshot, and a store the app has not imported yet
    has its answers only in its files. So `cos.db` is copied with SQLite's backup API, from a
    read-only connection, and every import runs on the copy: the real one keeps every byte."""
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(into / "cos.db")
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return UnitMeta(working_dir or str(db.parent), Data(into))


async def _board(meta: UnitMeta, data_dir: Path, path: str, names: dict[str, str]) -> dict:
    """`path`'s board, read on the copy's snapshot, each store it may name imported first."""
    for key in {units.key(path), *names.values()}:
        store = units.root(key, str(data_dir))
        if (store / units.COS_DIR).is_dir():
            meta.import_store(key, store)
    return await board_reader.read(units.root(path, str(data_dir)), state=meta.snapshot(units.key(path), names))


async def measure(data_dir: Path, config) -> tuple[dict, list[str]]:
    db = _read(data_dir / "cos.db", config.working_dir or "")
    agent_names, mine = _agent_names(db["prefs"]), _mine(db["prefs"])
    paths = [str(p) for p in config.workspaces]
    names: dict[str, str] = {}
    if config.working_dir:
        paths += [str(Path(config.working_dir) / n) for n in db["names"]]
        names = {n: units.key(str(Path(config.working_dir) / n)) for n in db["names"]}
    codes: Counter = Counter()
    missing = agent_originator = 0
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        meta = _copy(data_dir / "cos.db", Path(tmp), config.working_dir or "")
        boards = {}
        for path in dict.fromkeys(paths):
            if not (units.root(path, str(data_dir)) / units.COS_DIR).is_dir():
                lines.append(f"{Path(path).name}: no units")
                continue
            try:
                boards[path] = await _board(meta, data_dir, path, names)
            except (Unavailable, MetaError, BadTransition, Incompatible, sqlite3.Error) as e:
                lines.append(f"{Path(path).name}: could not be read: {e}")
    for path, data in boards.items():
        for u in data["units"]:
            for a in u.get("answers") or []:
                if a.get("artifact") not in FROM:
                    continue
                code = precedent.decided_by(a, units.slot(path), db["decisions"], mine, agent_names)
                if code not in precedent.WHO:
                    missing += 1
                    continue
                codes[code] += 1
                if code == precedent.ORIGINATOR and precedent.is_agent_name(a.get("by"), agent_names):
                    agent_originator += 1
                    lines.append(f"{Path(path).name} {u.get('name')}/{a.get('artifact')} câu {a.get('n')}: "
                                 f"{a.get('by')} reads as originator")
    labelled = [r for r in db["precedent"] if isinstance(r.get("cite_who"), dict)]
    bare = [r for r in labelled if r.get("written") and not any(
        w in (precedent.ORIGINATOR, precedent.DELEGATED, precedent.PRACTICE) for w in r["cite_who"].values())]
    lowered = sum(1 for r in db["precedent"] if r.get("reason") == precedent.ONLY_INFERRED)
    return {"total": sum(codes.values()) + missing, "codes": dict(codes), "missing": missing,
            "agent_originator": agent_originator, "bare": len(bare), "labelled": len(labelled),
            "lowered": lowered}, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measure", action="store_true", help="read the app's data and count (a)-(e)")
    args = parser.parse_args(argv)
    if not args.measure:
        parser.print_help()
        return 2
    if shutil.which("node") is None:
        print("node is not on PATH, so no board can be read", file=sys.stderr)
        return 2
    config = from_env()
    data_dir = Path(config.data_dir or "~/.cos").expanduser()
    if not (data_dir / "cos.db").is_file():
        print(f"no cos.db under {data_dir}", file=sys.stderr)
        return 2
    found, lines = asyncio.run(measure(data_dir, config))
    for line in lines:
        print(line)
    codes = found["codes"]
    print(f"(a) {found['total']} answers in force in intent.md and spec.md: "
          + ", ".join(f"{c} {codes.get(c, 0)}" for c in precedent.WHO))
    print(f"(b) {found['missing']} without a code")
    print(f"(c) {found['agent_originator']} under an agent's name read as originator")
    print(f"(d) {found['bare']} written precedent rows cite no originator, delegated or practice "
          f"(of {found['labelled']} rows with cite_who)")
    print(f"(e) {found['lowered']} verdicts taken to needs-person because they rest only on inferences")
    return 0 if found["missing"] == found["agent_originator"] == found["bare"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
