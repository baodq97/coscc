"""The app's snapshot of every unit's metadata: from `--state`, or straight from `cos.db` (R2).

`--state <file|->` is read as `cos.mjs` reads it, with its words when it cannot be. Without it
the deciding commands build the same dict in this process from the app's database, for the
workspace whose store `--root` names: `UnitMeta.snapshot`, as `coscc state <workspace>` prints it.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path


class StateError(Exception):
    """`--state` named something that is not a snapshot; the message is `cos.mjs`'s."""


def _node_message(arg: str, e: OSError) -> str:
    """What node's `readFileSync` says for the errors a path can give."""
    if isinstance(e, FileNotFoundError):
        return f"ENOENT: no such file or directory, open '{arg}'"
    if isinstance(e, IsADirectoryError):
        return "EISDIR: illegal operation on a directory, read"
    if isinstance(e, PermissionError):
        return f"EACCES: permission denied, open '{arg}'"
    return str(e)


def _json_message(text: str, e: json.JSONDecodeError) -> str:
    """V8's `JSON.parse` words for the two failures said alike everywhere; Python's otherwise."""
    if text.strip(" \t\n\r") == "":
        return "Unexpected end of JSON input"
    return e.msg


def load(arg: str):
    """The snapshot `--state <arg>` names (`-` for stdin), or `StateError` with `cos.mjs`'s words."""
    try:
        raw = sys.stdin.buffer.read() if arg == "-" else Path(arg).read_bytes()
    except OSError as e:
        raise StateError(f"--state {arg} is not readable JSON: {_node_message(arg, e)}") from e
    text = raw.decode("utf-8", "replace")
    try:
        state = json.loads(text)
    except json.JSONDecodeError as e:
        raise StateError(f"--state {arg} is not readable JSON: {_json_message(text, e)}") from e
    units = state.get("units") if isinstance(state, dict) else None
    if not (
        isinstance(state, dict)
        and isinstance(state.get("workspace"), str)
        and isinstance(units, (dict, list))
    ):
        raise StateError(f'--state {arg} is not a snapshot: it needs "workspace" and "units"')
    return state


def from_db(cos_dir: str):
    """The snapshot of the workspace whose store holds `cos_dir`, from `cos.db`; `None` without one.

    Workspaces are named as `coscc state` names them: a shared name, or one `valid_name`
    refuses, gets none.
    """
    from coscc import units
    from coscc.config import from_env
    from coscc.data import Data
    from coscc.service.store import Store, valid_name
    from coscc.units.meta import UnitMeta

    config = from_env()
    if not config.working_dir:
        return None
    data = Data(config.data_dir)
    store = Store(config.working_dir, data)
    rows = [(e.name, str(store.path_of(e.name))) for e in store.entries()]
    rows += [(Path(p).name, p) for p in config.workspaces]
    count = Counter(name for name, _ in rows)
    names = {n: units.key(p) for n, p in rows if count[n] == 1 and valid_name(n)}
    want = os.path.realpath(os.path.dirname(cos_dir))
    own = next(
        (
            units.key(p)
            for _, p in rows
            if os.path.realpath(units.root(units.key(p), config.data_dir)) == want
        ),
        None,
    )
    if own is None:
        return None
    snap = UnitMeta(config.working_dir, data).snapshot(own, names)
    # What `--state` would carry: the same dict, through JSON.
    return json.loads(json.dumps(snap, ensure_ascii=False))
