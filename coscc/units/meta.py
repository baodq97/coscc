"""`0135`. A unit's metadata, kept in `cos.db`, and the snapshot `cos.mjs --state` reads.

Spec Design 1-3. Four writers and one reader:

- `import_store`, once per store: everything `cos.mjs meta` reads off its markdown, in one
  `Data.write()` keyed in `migrations`, so it can neither run twice nor stop halfway (R3).
- `ingest`, at the end of every step that finished (R7): what changed in one unit's files.
- `add_answer` and `add_hold` (R8): a person's answer or hold, which no file carries any more.
- `snapshot`: the JSON `--state` reads, from the tables above and the fold over `transitions`.

There is no parser here (spec Design 2). Every read of a file is `cos.mjs meta`, the parsers
`status` used before this unit, so Python holds no second copy of the grammar.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from coscc.agent import harness
from coscc.data import Data, now
from coscc.units.history import History
from coscc.units.states import Machine

# Seconds. `meta` over the 135 units of this repository's own store took 0.3s on 2026-09-28;
# like `board.TIMEOUT`, this turns a hung child into an error rather than bounding the work.
TIMEOUT = 30.0

SOURCE = "import:0135"

_UNIT_RE = re.compile(r"(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)")
_ONE = "root = ? AND workspace = ? AND unit = ?"


class MetaError(RuntimeError):
    """`cos.mjs meta` gave no answer, carrying what it said."""


def read(store: str | Path, *args: str) -> dict[str, Any]:
    """`cos.mjs --root <store> meta [args]`, parsed. This app's copy of the script, as
    `coscc/units/board.py` runs it, for the reason its docstring gives."""
    argv = ["node", str(harness.script()), "--root", str(store), "meta", *args]
    try:
        done = subprocess.run(
            argv, env=harness.child_env(), capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MetaError(f"cos.mjs meta did not run: {e}") from e
    if done.returncode != 0:
        raise MetaError((done.stderr or done.stdout).strip() or f"cos.mjs meta exited {done.returncode}")
    try:
        return json.loads(done.stdout)
    except ValueError as e:
        raise MetaError(f"cos.mjs meta printed no JSON: {e}") from e


def _no_status(artifact: str, raw: str | None, machine: Machine) -> str:
    if raw is None:
        return "carries no Status line"
    return machine.refuse(artifact, raw) or f'status "{raw}" is not one the app records'


class UnitMeta:
    """The metadata of every unit under one working folder, as `History` is its transitions.

    `workspace` is the key `transitions` already uses, the workspace's resolved path
    (`Service._journal_key`); a workspace's *name* appears only in the snapshot, where a
    unit's `Depends on:` refers to another by it.
    """

    def __init__(
        self,
        working_dir: str | Path,
        data: Data | str | Path | None = None,
        machine: Machine | None = None,
    ):
        self.history = History(working_dir, data, machine)
        self.data = self.history.data
        self.machine = self.history.machine
        self.root = str(self.history.working_dir)

    # -- import, once per store ----------------------------------------------

    def import_key(self, workspace: str) -> str:
        return f"unit-meta:0135:{self.root}/{workspace}"

    def imported(self, workspace: str) -> bool:
        return self.data.has_run(self.import_key(workspace))

    def import_store(self, workspace: str, store: str | Path) -> list[dict[str, Any]] | None:
        """Every directory under the store's `.cos/`, read once, in one transaction (R3).

        Returns the fields that could not be read, `(unit, artifact, field, reason, raw)`,
        or `None` when the store was imported already. A second call adds no row: the
        `migrations` key stops it, and every appended row carries a `once_key` besides.
        """
        key = self.import_key(workspace)
        if self.data.has_run(key):
            return None
        found = read(store)
        with self.data.write() as conn:
            if self.data.has_run(key, conn):
                return None
            unknowns: list[dict[str, Any]] = []
            for unit, meta in sorted((found.get("units") or {}).items()):
                unknowns += self._apply(conn, workspace, unit, meta, imported=True)
            self._write_ideas(conn, workspace, found.get("ideas"))
            Data.mark_run(conn, key)
        return unknowns

    # -- ingest, at the end of a step ----------------------------------------

    def ingest(
        self,
        workspace: str,
        store: str | Path,
        unit: str,
        *,
        actor: str,
        session: str,
        source: str,
        wrote: str | None = None,
        decided: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        """R7: read one unit's files through `cos.mjs meta`, and write what changed.

        Only an artifact whose text differs from the last one read (`unit_seen`) is written:
        a transition when the fold differs from its status, its questions, and from
        `intent.md` its `Type:` and links. `wrote`, the artifact the step itself writes, is
        the exception: it always gets its transition, as it had one per step since `0014`
        R6, because rewriting a settled artifact is the event `0013` counts. Answers and
        holds are not read: since `0135` the app writes them here and nowhere else. Raises
        `MetaError` or `sqlite3.Error`; the caller records the failure (spec C3).

        `0136` R4: an artifact in `decided` takes its status and its questions from the
        object its run submitted (`record_result`), so neither is read from its file here.
        """
        found = read(store, unit)
        meta = (found.get("units") or {}).get(unit) or {}
        with self.data.write() as conn:
            conn.execute(f"DELETE FROM unit_unknowns WHERE {_ONE} AND field = 'ingest'", (self.root, workspace, unit))
            return self._apply(
                conn, workspace, unit, meta, imported=False,
                provenance={"actor": actor, "session": session, "source": source}, wrote=wrote,
                decided=decided,
            )

    def ingest_failed(self, workspace: str, unit: str, reason: str) -> None:
        """An ingest that failed, as a row the snapshot turns into a problem on the card."""
        with self.data.write() as conn:
            conn.execute(
                "INSERT INTO unit_unknowns (root, workspace, unit, artifact, field, reason, raw, at) "
                "VALUES (?, ?, ?, '', 'ingest', ?, NULL, ?)",
                (self.root, workspace, unit, reason, now()),
            )

    def refresh_ideas(self, workspace: str, store: str | Path) -> None:
        """The store's ideas again, after the app wrote one (`coscc/service/ideas.py`)."""
        found = read(store)
        with self.data.write() as conn:
            self._write_ideas(conn, workspace, found.get("ideas"))

    def _apply(
        self,
        conn: sqlite3.Connection,
        workspace: str,
        unit: str,
        meta: Mapping[str, Any],
        *,
        imported: bool,
        provenance: Mapping[str, str] | None = None,
        wrote: str | None = None,
        decided: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        """Write one unit's `meta` output. Returns the fields it could not read."""
        scope = (self.root, workspace, unit)
        at = now()
        match = _UNIT_RE.fullmatch(unit)
        conn.execute(
            "INSERT OR IGNORE INTO unit_meta (root, workspace, unit, type, lane, number, slug, imported_at) "
            "VALUES (?, ?, ?, 'unknown', 'full', ?, ?, ?)",
            (*scope, int(match.group(1)) if match else None, match.group(2) if match else None, at),
        )
        seen = {
            r["artifact"]: r["sha256"]
            for r in conn.execute(f"SELECT artifact, sha256 FROM unit_seen WHERE {_ONE}", scope)
        }
        latest = self.history._latest(conn)
        unknowns: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        changed = [
            (artifact, a) for artifact, a in (meta.get("artifacts") or {}).items()
            if seen.get(artifact) != a.get("sha256") or artifact == wrote
        ]
        for artifact, a in changed:
            conn.execute(f"DELETE FROM unit_unknowns WHERE {_ONE} AND artifact = ?", (*scope, artifact))
            if artifact in decided:
                # `0136` R4: its `Status:` and `## Open questions` are prose for a reader now.
                conn.execute(
                    "INSERT INTO unit_seen (root, workspace, unit, artifact, sha256, questions) VALUES (?, ?, ?, ?, ?, 1) "
                    "ON CONFLICT (root, workspace, unit, artifact) DO UPDATE SET sha256 = excluded.sha256",
                    (*scope, artifact, str(a.get("sha256") or "")),
                )
                continue
            status, raw = a.get("status"), a.get("raw")
            if status is not None and self.machine.refuse(artifact, status) is None:
                if artifact == wrote or latest.get((workspace, unit, artifact), self.machine.absent) != status:
                    item = {"workspace": workspace, "unit": unit, "artifact": artifact, "to_state": status}
                    if imported:
                        item.update(actor=SOURCE, session=SOURCE, source=SOURCE,
                                    once_key=f"{SOURCE}:{workspace}/{unit}/{artifact}")
                    else:
                        item.update(provenance or {})
                    items.append(item)
            else:
                unknowns.append({"unit": unit, "artifact": artifact, "field": "status",
                                 "reason": _no_status(artifact, raw, self.machine), "raw": raw})
            conn.execute(f"DELETE FROM unit_questions WHERE {_ONE} AND artifact = ?", (*scope, artifact))
            questions = a.get("questions")
            conn.executemany(
                "INSERT INTO unit_questions (root, workspace, unit, artifact, n, text) VALUES (?, ?, ?, ?, ?, ?)",
                [(*scope, artifact, int(q["n"]), str(q["text"])) for q in questions or []],
            )
            conn.execute(
                "INSERT OR REPLACE INTO unit_seen (root, workspace, unit, artifact, sha256, questions) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (*scope, artifact, str(a.get("sha256") or ""), 0 if questions is None else 1),
            )
        if "intent.md" in dict(changed) and "links" in meta:
            kind = meta.get("type")
            conn.execute(f"UPDATE unit_meta SET type = ? WHERE {_ONE}", (kind or "unknown", *scope))
            if kind is None:
                unknowns.append({"unit": unit, "artifact": "intent.md", "field": "type",
                                 "reason": "intent.md declares no Type", "raw": None})
            links = meta.get("links") or {}
            rows = [("idea", links.get("idea")), ("repo", links.get("repo"))]
            rows += [("depends", d) for d in links.get("dependsOn") or []]
            conn.execute(f"DELETE FROM unit_links WHERE {_ONE}", scope)
            conn.executemany(
                "INSERT INTO unit_links (root, workspace, unit, kind, ref, pos) VALUES (?, ?, ?, ?, ?, ?)",
                [(*scope, k, str(ref), i) for i, (k, ref) in enumerate(rows) if ref is not None],
            )
        if imported:
            for i, a in enumerate(meta.get("answers") or []):
                self._answer(conn, workspace, unit, a["artifact"], a["id"] or str(a["n"]), a["text"],
                             a["by"], a["date"], a["via"], f"{SOURCE}:{workspace}/{unit}/{a['artifact']}/answer/{i}")
            for i, h in enumerate(meta.get("holds") or []):
                if h.get("by") is None:
                    unknowns.append({"unit": unit, "artifact": "intent.md", "field": "hold",
                                     "reason": f"hold block {i + 1} has no Decided by line", "raw": None})
                    continue
                self._hold(conn, workspace, unit, h["state"], h["reason"], h["by"], h["date"], h["via"],
                           f"{SOURCE}:{workspace}/{unit}/hold/{i}")
        self.history.record_in(conn, items)
        conn.executemany(
            "INSERT INTO unit_unknowns (root, workspace, unit, artifact, field, reason, raw, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(self.root, workspace, u["unit"], u["artifact"], u["field"], u["reason"], u["raw"], at)
             for u in unknowns],
        )
        return unknowns

    def record_result(
        self, conn: sqlite3.Connection, workspace: str, unit: str, stage: str, artifact: str,
        submitted: Mapping[str, Any],
    ) -> None:
        """`0136` R4. What a stage result carries beside its transition, in the caller's
        transaction (`transitions.apply(also=…)`): its row in `stage_results`, and the
        artifact's open questions, which the snapshot reads as it read them from the file."""
        obj = dict(submitted.get("object") or {})
        scope = (self.root, workspace, unit)
        conn.execute(
            "INSERT INTO stage_results (at, root, workspace, unit, stage, run, revision, judgement, object) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), *scope, stage, str(submitted.get("run") or ""), str(submitted.get("revision") or ""),
             str(obj.get("judgement") or ""), json.dumps(obj, ensure_ascii=False)),
        )
        conn.execute(f"DELETE FROM unit_questions WHERE {_ONE} AND artifact = ?", (*scope, artifact))
        conn.executemany(
            "INSERT INTO unit_questions (root, workspace, unit, artifact, n, text) VALUES (?, ?, ?, ?, ?, ?)",
            [(*scope, artifact, int(q["n"]), str(q["text"])) for q in obj.get("questions") or []],
        )
        conn.execute(
            "INSERT INTO unit_seen (root, workspace, unit, artifact, sha256, questions) VALUES (?, ?, ?, ?, '', 1) "
            "ON CONFLICT (root, workspace, unit, artifact) DO UPDATE SET questions = 1",
            (*scope, artifact),
        )

    def _write_ideas(self, conn: sqlite3.Connection, workspace: str, ideas: Iterable[Mapping[str, Any]] | None) -> None:
        conn.execute("DELETE FROM idea_meta WHERE root = ? AND workspace = ?", (self.root, workspace))
        conn.executemany(
            "INSERT INTO idea_meta (root, workspace, idea, read) VALUES (?, ?, ?, ?)",
            [
                (self.root, workspace, str(i["id"]), json.dumps(
                    {k: i.get(k) for k in ("title", "status", "units", "problems")}, ensure_ascii=False))
                for i in ideas or []
            ],
        )

    # -- answers and holds, which only the app writes -------------------------

    def _answer(self, conn, workspace, unit, artifact, ref, text, by, date, via, once_key="") -> None:
        conn.execute(
            "INSERT OR IGNORE INTO unit_answers (root, workspace, unit, artifact, ref, text, answered_by, date, via, once_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.root, workspace, unit, artifact, str(ref), text, by, date, via, once_key),
        )

    def _hold(self, conn, workspace, unit, state, reason, by, date, via, once_key="") -> None:
        conn.execute(
            "INSERT OR IGNORE INTO unit_holds (root, workspace, unit, move, reason, decided_by, date, via, once_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.root, workspace, unit, state, reason, by, date, via, once_key),
        )

    def add_answer(
        self, workspace: str, unit: str, artifact: str, ref: str | int, text: str,
        by: str, date: str, via: str, conn: sqlite3.Connection | None = None,
    ) -> None:
        """R8. `ref` is a question's number or a finding's `F<k>`; the last row for it wins."""
        if conn is not None:
            return self._answer(conn, workspace, unit, artifact, ref, text, by, date, via)
        with self.data.write() as c:
            self._answer(c, workspace, unit, artifact, ref, text, by, date, via)

    def add_hold(
        self, workspace: str, unit: str, state: str, reason: str,
        by: str, date: str, via: str, conn: sqlite3.Connection | None = None,
    ) -> None:
        """R8. `state` is `paused`, `dropped` or `active`; `cos.mjs` folds the rows."""
        if conn is not None:
            return self._hold(conn, workspace, unit, state, reason, by, date, via)
        with self.data.write() as c:
            self._hold(c, workspace, unit, state, reason, by, date, via)

    # -- reading --------------------------------------------------------------

    def unknowns(self, workspaces: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Every field an import could not read, for `/settings` (R4). Not failed ingests."""
        sql = "SELECT workspace, unit, artifact, field, reason, raw FROM unit_unknowns WHERE root = ? AND field <> 'ingest'"
        args: list[Any] = [self.root]
        wanted = list(workspaces) if workspaces is not None else None
        if wanted is not None:
            sql += f" AND workspace IN ({', '.join('?' for _ in wanted)})"
            args += wanted
        with self.data.connect() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY workspace, unit, artifact, field", args)]

    def snapshot(self, own: str, names: Mapping[str, str], units_: Iterable[str] | None = None) -> dict[str, Any]:
        """Spec Design 3: what `cos.mjs --state` reads, for the store `own` and every
        workspace `names` maps a name to. One query per table, never one per unit.

        Units are keyed `<name>/<unit>`. `own`'s name is the one `names` gives it, or `""`
        for a workspace with none. `not started`, the fold's word for no transition, is no
        status at all here, as a missing file is to `cos.mjs`.

        `units_` narrows it to those units of `own` and every unit their `Depends on:` may
        name, which is all `gate`, `next` and `unit-branch` read: R10 measured a `next` given
        every unit of this repository's store slower than one reading its files.
        """
        named = dict(names)
        own_name = next((n for n, k in named.items() if k == own), "")
        named.setdefault(own_name, own)
        name_of = {k: n for n, k in named.items()}
        keys = list(name_of)
        where = f"root = ? AND workspace IN ({', '.join('?' for _ in keys)})"
        args: list[Any] = [self.root, *keys]
        pairs: set[tuple[str, str]] | None = None
        units: dict[str, dict[str, Any]] = {}
        with self.data.connect() as conn:
            if units_ is not None:
                wanted = sorted(set(units_))
                pairs = {(own, u) for u in wanted}
                links = conn.execute(
                    f"SELECT ref FROM unit_links WHERE root = ? AND workspace = ? AND kind = 'depends' "
                    f"AND unit IN ({', '.join('?' for _ in wanted)})", (self.root, own, *wanted),
                ).fetchall()
                for (ref,) in links:
                    ws, _, name = ref.rpartition("/")
                    pairs.add((own, name))
                    if ws in named:
                        pairs.add((named[ws], name))
                names_in = sorted({u for _, u in pairs})
                where += f" AND unit IN ({', '.join('?' for _ in names_in)})"
                args += names_in

            def rows(sql: str):
                return conn.execute(sql.format(where=where), args).fetchall()

            for r in rows("SELECT workspace, unit, type, lane FROM unit_meta WHERE {where}"):
                if pairs is not None and (r["workspace"], r["unit"]) not in pairs:
                    continue
                units[f"{name_of[r['workspace']]}/{r['unit']}"] = {
                    "artifacts": {}, "type": None if r["type"] == "unknown" else r["type"], "lane": r["lane"],
                    "links": {"idea": None, "repo": None, "dependsOn": None},
                    "holds": [], "answers": [], "unknowns": [],
                }

            def entry(r) -> dict[str, Any] | None:
                return units.get(f"{name_of[r['workspace']]}/{r['unit']}")

            def artifact(r) -> dict[str, Any] | None:
                e = entry(r)
                return None if e is None else e["artifacts"].setdefault(r["artifact"], {"status": None, "raw": None, "questions": None})

            for r in rows(
                "SELECT workspace, unit, artifact, to_state FROM transitions WHERE id IN "
                "(SELECT MAX(id) FROM transitions WHERE {where} GROUP BY workspace, unit, artifact)"
            ):
                a = artifact(r)
                if a is not None and r["to_state"] != self.machine.absent:
                    a["status"] = r["to_state"]
            # `0136` R4: the last stage result of each stage, which `cos.mjs` reads a spec's
            # `U<n>` and a spike's verdicts from rather than from the file.
            for r in rows(
                "SELECT workspace, unit, stage, object FROM stage_results WHERE id IN "
                "(SELECT MAX(id) FROM stage_results WHERE {where} GROUP BY workspace, unit, stage)"
            ):
                a = artifact({"workspace": r["workspace"], "unit": r["unit"], "artifact": f"{r['stage']}.md"})
                if a is not None:
                    a["result"] = json.loads(r["object"])
            for r in rows("SELECT workspace, unit, artifact, questions FROM unit_seen WHERE {where}"):
                a = artifact(r)
                if a is not None and r["questions"]:
                    a["questions"] = []
            for r in rows("SELECT workspace, unit, artifact, n, text FROM unit_questions WHERE {where} ORDER BY rowid"):
                a = artifact(r)
                if a is not None:
                    a["questions"] = [*(a["questions"] or []), {"n": r["n"], "text": r["text"]}]
            for r in rows("SELECT workspace, unit, artifact, field, reason, raw FROM unit_unknowns WHERE {where}"):
                e = entry(r)
                if e is None:
                    continue
                e["unknowns"].append({"artifact": r["artifact"], "field": r["field"], "reason": r["reason"]})
                if r["field"] == "status" and r["raw"] is not None:
                    artifact(r)["raw"] = r["raw"]
            for r in rows("SELECT workspace, unit, kind, ref FROM unit_links WHERE {where} ORDER BY pos"):
                e = entry(r)
                if e is None:
                    continue
                if r["kind"] == "depends":
                    e["links"]["dependsOn"] = [*(e["links"]["dependsOn"] or []), r["ref"]]
                else:
                    e["links"][r["kind"]] = r["ref"]
            for r in rows("SELECT workspace, unit, artifact, ref, text, answered_by, date, via FROM unit_answers WHERE {where} ORDER BY id"):
                e = entry(r)
                if e is not None:
                    number = r["ref"].isdigit()
                    e["answers"].append({
                        "artifact": r["artifact"], "n": int(r["ref"]) if number else None,
                        "id": None if number else r["ref"], "by": r["answered_by"], "date": r["date"],
                        "via": r["via"], "text": r["text"],
                    })
            for r in rows("SELECT workspace, unit, move, reason, decided_by, date, via FROM unit_holds WHERE {where} ORDER BY id"):
                e = entry(r)
                if e is not None:
                    e["holds"].append({"state": r["move"], "reason": r["reason"], "by": r["decided_by"],
                                       "date": r["date"], "via": r["via"]})
            ideas: dict[str, list[dict[str, Any]]] = {}
            for r in conn.execute(
                f"SELECT workspace, idea, read FROM idea_meta WHERE root = ? AND workspace IN "
                f"({', '.join('?' for _ in keys)}) ORDER BY idea", (self.root, *keys),
            ):
                ideas.setdefault(name_of[r["workspace"]], []).append({"id": r["idea"], **json.loads(r["read"])})
        return {"workspace": own_name, "workspaces": sorted(n for n in named if n), "units": units, "ideas": ideas}
