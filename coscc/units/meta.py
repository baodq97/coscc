"""A unit's metadata, kept in `cos.db`, and the snapshot `coscc.loop --state` reads.

Writers and one reader:

- `add_unit`, `link`: a unit's row and its links, when the app opens it.
- `record_result` and `record_round`: what a finished step handed back, in the caller's transaction.
- `add_answer`, `add_hold` and `add_decision`: an answer, a hold, or a person's rerun, more
  rounds or outcome.
- `snapshot`: the JSON `--state` reads, from the tables and the fold over `transitions`.

No file is read here: a unit's state is its rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from coscc.agent import pack
from coscc.store.db import Data, now
from coscc.store.journal import Intervention, Journal
from coscc.units import UNIT_RE, backlog, contracts
from coscc.units.history import History
from coscc.units.states import Machine

SOURCE = "import:0135"
# The source every row of the PR machine carries (`prmachine.Machine._apply`), the guards that
# move where its pull request stands (`prmachine.state`), and the one that is `merged`.
PR_SOURCE = "prmachine:%"
PR_MOVES = ("branch-named", "ship-ready", "merge-read", "close-read")
MERGED = "merge-read"
# The roads a merge was recorded by outside the machine: the import, and a `ship` session. A unit
# the machine never touched is merged when its last `ship.md` transition is `accepted` from one.
SHIPPED_BEFORE_THE_MACHINE = (SOURCE, "run:ship")

_ONE = "root = ? AND workspace = ? AND unit = ?"

# Whose decision an answer is, as its writer sent it: a person's press, or decided for them.
By = Literal["person", "delegated"]
# A person's decision that is neither an answer nor a hold (`unit_decisions.kind`).
DecisionKind = Literal["rerun", "more-rounds", "outcome"]


class Decision(TypedDict):
    """One `unit_decisions` row: `fields` as its kind holds them."""

    kind: DecisionKind
    fields: dict[str, Any]
    by: str
    date: str


class OutputRecord(TypedDict):
    """What an agent handed back, as the unit page shows it: the latest record of one agent."""

    agent: str
    version: int
    at: str
    fields: dict[str, object]


def _spike_round(objects: Iterable[str]) -> int:
    """1 + the earlier spikes of a unit with a failing verdict (each a stored object, oldest first)."""
    failed = sum(
        any(
            isinstance(v, dict) and v.get("verdict") == "fails"
            for v in json.loads(o).get("verdicts") or []
        )
        for o in objects
    )
    return 1 + failed


class UnitMeta:
    """The metadata of every unit under one working folder, as `History` is its transitions.

    `workspace` is the key `transitions` uses, the resolved path; a workspace's name appears
    only in the snapshot, where `Depends on:` refers to another by it.
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
        self.history.process_of = self._process_of

    def _process_of(self, conn: sqlite3.Connection, workspace: str, unit: str) -> str | None:
        """The process the unit records, `None` while it has no row."""
        found = conn.execute(
            "SELECT process FROM unit_meta WHERE root = ? AND workspace = ? AND unit = ?",
            (self.root, workspace, unit),
        ).fetchone()
        return found[0] if found else None

    def ingest_failed(self, workspace: str, unit: str, reason: str) -> None:
        """An ingest that failed, as a row the snapshot turns into a problem on the card."""
        with self.data.write() as conn:
            conn.execute(
                "INSERT INTO unit_unknowns (root, workspace, unit, artifact, field, reason, at) "
                "VALUES (?, ?, ?, '', 'ingest', ?, ?)",
                (self.root, workspace, unit, reason, now()),
            )

    def add_unit(
        self,
        conn: sqlite3.Connection,
        workspace: str,
        unit: str,
        process: str = pack.DEFAULT_PROCESS,
    ) -> None:
        """The unit's `unit_meta` row, once, with the process it walks to the end."""
        match = UNIT_RE.fullmatch(unit)
        conn.execute(
            "INSERT OR IGNORE INTO unit_meta "
            "(root, workspace, unit, type, number, slug, imported_at, process) "
            "VALUES (?, ?, ?, 'unknown', ?, ?, ?, ?)",
            (
                self.root,
                workspace,
                unit,
                int(match.group(1)) if match else None,
                match.group(2) if match else None,
                now(),
                process,
            ),
        )

    def record_result(
        self,
        conn: sqlite3.Connection,
        workspace: str,
        unit: str,
        stage: str,
        artifact: str,
        submitted: Mapping[str, Any],
    ) -> None:
        """What a stage result carries beside its transition, in the caller's transaction: its
        row in `outputs`, and the artifact's open questions."""
        obj = dict(submitted.get("object") or {})
        scope = (self.root, workspace, unit)
        conn.execute(
            "INSERT INTO outputs (at, root, workspace, unit, agent, version, run, revision, judgement, object) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now(),
                *scope,
                stage,
                contracts.version(stage),
                str(submitted.get("run") or ""),
                str(submitted.get("revision") or ""),
                str(obj.get("judgement") or ""),
                json.dumps(obj, ensure_ascii=False),
            ),
        )
        conn.execute(
            f"DELETE FROM unit_questions WHERE {_ONE} AND artifact = ?", (*scope, artifact)
        )
        conn.executemany(
            "INSERT INTO unit_questions (root, workspace, unit, artifact, n, text, recommendation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (*scope, artifact, int(q["n"]), str(q["text"]), str(q.get("recommendation") or ""))
                for q in obj.get("questions") or []
            ],
        )
        if stage == "intent" and obj.get("type"):
            conn.execute(f"UPDATE unit_meta SET type = ? WHERE {_ONE}", (str(obj["type"]), *scope))
        # impl's claims, each against the round whose open findings guard `impl-claim` read.
        conn.executemany(
            "INSERT INTO impl_claims (at, root, workspace, unit, run, round, finding) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    now(),
                    *scope,
                    str(submitted.get("run") or ""),
                    int(submitted.get("claims_round") or 0),
                    str(f),
                )
                for f in obj.get("needs_person") or ()
            ],
        )

    def record_round(
        self, conn: sqlite3.Connection, workspace: str, unit: str, submitted: Mapping[str, Any]
    ) -> None:
        """A review round and its findings, in the caller's transaction. `n` and `head` are the
        app's: the round number and the head read when the run opened; `screens` is the object's
        list with where the app read they were taken."""
        obj = dict(submitted.get("object") or {})
        screens = {**dict(submitted.get("screens") or {}), "shots": list(obj.get("screens") or ())}
        cur = conn.execute(
            "INSERT INTO review_rounds (at, root, workspace, unit, n, run, head, verdict, screens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now(),
                self.root,
                workspace,
                unit,
                int(submitted["n"]),
                str(submitted.get("run") or ""),
                str(submitted.get("head") or ""),
                str(obj["verdict"]),
                json.dumps(screens, ensure_ascii=False),
            ),
        )
        conn.executemany(
            "INSERT INTO review_findings (round, finding, open, label, fixed_in, severity, rule, path, lines, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    cur.lastrowid,
                    f["id"],
                    1 if f["state"] == "open" else 0,
                    f["state"],
                    f["fixed_in"],
                    f["severity"],
                    f["rule"],
                    f["path"],
                    f["lines"],
                    f["text"],
                )
                for f in obj.get("findings") or ()
            ],
        )

    def interventions(self, workspace: str, after: str, limit: int) -> list[Intervention]:
        """What the CI and the reviews of `workspace` sent back to a person, past `after`: each
        transition the CI read `red` (`ci-red`; its `to_state` is no clue, it stays `accepted`),
        each `impl` moved to `draft` (`impl-draft`) and each round that asked for changes
        (`review-round`, its findings' words as the detail). Oldest first, at most `limit` of
        each."""
        out: list[Intervention] = []
        with self.data.connect() as conn:
            for row in conn.execute(
                "SELECT id, at, unit, stage, artifact, inputs FROM transitions "
                "WHERE root = ? AND workspace = ? AND at > ? AND actor = 'code:ci' "
                "AND CASE WHEN json_valid(inputs) THEN json_extract(inputs, '$.ci') END = 'red' "
                "ORDER BY at, id LIMIT ?",
                (self.root, workspace, after, int(limit)),
            ).fetchall():
                read = json.loads(row["inputs"])
                head = str(read.get("head") or "")[:7] if isinstance(read, dict) else ""
                said = f"CI went red on {row['artifact']}" + (f" at {head}" if head else "")
                out.append(
                    Intervention(
                        f"ci-red:transitions:{row['id']}",
                        "ci-red",
                        row["at"],
                        row["unit"],
                        row["stage"],
                        said,
                    )
                )
            for row in conn.execute(
                "SELECT id, at, unit, stage, from_state, guard FROM transitions "
                "WHERE root = ? AND workspace = ? AND at > ? AND stage = 'impl' "
                "AND to_state = 'draft' ORDER BY at, id LIMIT ?",
                (self.root, workspace, after, int(limit)),
            ).fetchall():
                said = f"impl went from {row['from_state'] or 'nothing'} to draft ({row['guard']})"
                out.append(
                    Intervention(
                        f"impl-draft:transitions:{row['id']}",
                        "impl-draft",
                        row["at"],
                        row["unit"],
                        "impl",
                        said,
                    )
                )
            for row in conn.execute(
                "SELECT id, at, unit, n FROM review_rounds WHERE root = ? AND workspace = ? "
                "AND at > ? AND verdict = 'changes-requested' ORDER BY at, id LIMIT ?",
                (self.root, workspace, after, int(limit)),
            ).fetchall():
                findings = conn.execute(
                    "SELECT finding, severity, text FROM review_findings WHERE round = ? "
                    "ORDER BY finding",
                    (row["id"],),
                ).fetchall()
                said = "; ".join(
                    f"{f['finding']} ({f['severity']}): {' '.join(f['text'].split())[:120]}"
                    for f in findings
                )
                out.append(
                    Intervention(
                        f"review-round:review_rounds:{row['id']}",
                        "review-round",
                        row["at"],
                        row["unit"],
                        "review",
                        f"round {row['n']}: {said}" if said else f"round {row['n']}",
                    )
                )
        return out

    def link(
        self,
        conn: sqlite3.Connection,
        workspace: str,
        unit: str,
        idea: str | None,
        depends_on: Iterable[str],
    ) -> None:
        """The unit's idea and the units it depends on, replacing what it had. The one writer of
        `unit_links`: the press that created the unit calls it, in the transaction of its row."""
        rows = [("idea", idea)] if idea else []
        rows += [("depends", d) for d in depends_on]
        scope = (self.root, workspace, unit)
        conn.execute(f"DELETE FROM unit_links WHERE {_ONE}", scope)
        conn.executemany(
            "INSERT INTO unit_links (root, workspace, unit, kind, ref, pos) VALUES (?, ?, ?, ?, ?, ?)",
            [(*scope, k, str(ref), i) for i, (k, ref) in enumerate(rows)],
        )

    def idea_units(self, idea: str) -> list[tuple[str, str, list[str]]]:
        """`(workspace, unit, depends_on)` of every unit whose `idea` row names `idea`, in every
        workspace of the root."""
        with self.data.connect() as conn:
            found = conn.execute(
                "SELECT workspace, unit FROM unit_links WHERE root = ? AND kind = 'idea' AND ref = ? "
                "ORDER BY workspace, unit",
                (self.root, idea),
            ).fetchall()
            return [
                (
                    r["workspace"],
                    r["unit"],
                    [
                        d[0]
                        for d in conn.execute(
                            f"SELECT ref FROM unit_links WHERE {_ONE} AND kind = 'depends' ORDER BY pos",
                            (self.root, r["workspace"], r["unit"]),
                        )
                    ],
                )
                for r in found
            ]

    def _answer(self, conn, workspace, unit, artifact, ref, text, by, name, date, via) -> None:
        conn.execute(
            "INSERT INTO unit_answers "
            '(root, workspace, unit, artifact, ref, text, "by", name, date, via) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.root, workspace, unit, artifact, str(ref), text, by, name, date, via),
        )

    def _hold(self, conn, workspace, unit, state, reason, by, date, via) -> None:
        conn.execute(
            "INSERT INTO unit_holds (root, workspace, unit, move, reason, decided_by, date, via) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.root, workspace, unit, state, reason, by, date, via),
        )

    def add_answer(
        self,
        workspace: str,
        unit: str,
        artifact: str,
        ref: str | int,
        text: str,
        by: By,
        name: str,
        date: str,
        via: str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """`ref` is a question's number or a finding's `F<k>`; the last row for it wins. `by` is
        whose decision it is, as sent; `name` the name the caller gave."""
        if conn is not None:
            return self._answer(conn, workspace, unit, artifact, ref, text, by, name, date, via)
        with self.data.write() as c:
            self._answer(c, workspace, unit, artifact, ref, text, by, name, date, via)

    def add_hold(
        self,
        workspace: str,
        unit: str,
        state: str,
        reason: str,
        by: str,
        date: str,
        via: str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """`state` is `paused`, `dropped` or `active`; the loop folds the rows."""
        if conn is not None:
            return self._hold(conn, workspace, unit, state, reason, by, date, via)
        with self.data.write() as c:
            self._hold(c, workspace, unit, state, reason, by, date, via)

    def add_decision(
        self,
        workspace: str,
        unit: str,
        kind: DecisionKind,
        fields: Mapping[str, Any],
        by: str,
        date: str,
        via: str,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """A person's rerun, more rounds or outcome: one row, appended."""
        row = (
            self.root,
            workspace,
            unit,
            kind,
            json.dumps(dict(fields), ensure_ascii=False),
            by,
            date,
            via,
        )
        sql = (
            "INSERT INTO unit_decisions (root, workspace, unit, kind, fields, decided_by, date, via) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        if conn is not None:
            conn.execute(sql, row)
            return
        with self.data.write() as c:
            c.execute(sql, row)

    def decisions(self, workspace: str, unit: str) -> list[Decision]:
        """A unit's decisions, oldest first: the unit page's history."""
        with self.data.connect() as conn:
            found = conn.execute(
                f"SELECT kind, fields, decided_by, date FROM unit_decisions WHERE {_ONE} ORDER BY id",
                (self.root, workspace, unit),
            ).fetchall()
        return [
            {
                "kind": r["kind"],
                "fields": json.loads(r["fields"]),
                "by": r["decided_by"],
                "date": r["date"],
            }
            for r in found
        ]

    def outputs(self, workspace: str, unit: str) -> list[OutputRecord]:
        """The latest output of each agent for a unit, oldest first: the unit page's second tab."""
        with self.data.connect() as conn:
            found = conn.execute(
                "SELECT agent, version, at, object FROM outputs WHERE id IN "
                f"(SELECT MAX(id) FROM outputs WHERE {_ONE} GROUP BY agent) ORDER BY id",
                (self.root, workspace, unit),
            ).fetchall()
        return [
            {
                "agent": r["agent"],
                "version": r["version"],
                "at": r["at"],
                "fields": {
                    k: v for k, v in json.loads(r["object"]).items() if k != contracts.SENDER
                },
            }
            for r in found
        ]

    def plan(self, workspace: str, unit: str) -> contracts.Plan | None:
        """The unit's latest plan record, `None` when the plan has handed none back."""
        with self.data.connect() as conn:
            r = conn.execute(
                f"SELECT version, object FROM outputs WHERE {_ONE} AND agent = 'plan' "
                "ORDER BY id DESC LIMIT 1",
                (self.root, workspace, unit),
            ).fetchone()
        if r is None:
            return None
        contracts.check_stored("plan", r["version"])
        o = json.loads(r["object"])
        return {
            "variant": o["variant"],
            "files": list(o["files"]),
            "steps": list(o["steps"]),
            "rests_on": list(o["rests_on"]),
        }

    def _backlog_depends(
        self, keys: list[str], wanted: list[str] | None
    ) -> dict[tuple[str, str], list[str]]:
        """`{(workspace, unit): [other]}` for each `phụ thuộc` relation of the backlog in force.

        The relations are `relation` records of the run log, folded by `backlog.relations_of`;
        The loop holds `impl` for the unit they name like a `Depends on:`.
        """
        journal = Journal(self.root, self.data)
        out: dict[tuple[str, str], list[str]] = {}
        for workspace in keys:
            for r in backlog.relations_of(journal.records(workspace, kind="relation")):
                if r["type"] == "phụ thuộc" and (wanted is None or r["unit"] in wanted):
                    out.setdefault((workspace, r["unit"]), []).append(r["other"])
        return out

    def snapshot(  # noqa: C901, PLR0915 - still to split
        self, own: str, names: Mapping[str, str], units_: Iterable[str] | None = None
    ) -> dict[str, Any]:
        """What `coscc.loop --state` reads, for the store `own` and every workspace `names` maps a name to.

        One query per table. Units are keyed `<name>/<unit>`; `own`'s name is the one `names`
        gives it, or `""`. `not started` is no status at all here. `units_` narrows it to those
        units of `own` and every unit their `Depends on:` or backlog relation may name, which is
        all `gate`, `next` and `unit-branch` read. A unit with a `phụ thuộc` relation in force
        carries `links.backlog`, `[{ref, source: "backlog"}]`; one with none has no such key.
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
        wanted = sorted(set(units_)) if units_ is not None else None
        waited = self._backlog_depends(keys, wanted)
        with self.data.connect() as conn:
            if wanted is not None:
                pairs = {(own, u) for u in wanted}
                pairs.update(
                    (own, o) for (ws, _), others in waited.items() if ws == own for o in others
                )
                links = conn.execute(
                    f"SELECT ref FROM unit_links WHERE root = ? AND workspace = ? AND kind = 'depends' "
                    f"AND unit IN ({', '.join('?' for _ in wanted)})",
                    (self.root, own, *wanted),
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

            for r in rows("SELECT workspace, unit, type, process FROM unit_meta WHERE {where}"):
                if pairs is not None and (r["workspace"], r["unit"]) not in pairs:
                    continue
                units[f"{name_of[r['workspace']]}/{r['unit']}"] = {
                    "artifacts": {},
                    "type": None if r["type"] == "unknown" else r["type"],
                    "links": {"idea": None, "dependsOn": None},
                    "holds": [],
                    "answers": [],
                    "unknowns": [],
                    "merged": False,
                    "shipped": False,
                    "reruns": [],
                    "roundsGranted": 0,
                    "process": r["process"],
                }

            def entry(r) -> dict[str, Any] | None:
                return units.get(f"{name_of[r['workspace']]}/{r['unit']}")

            def artifact(r) -> dict[str, Any] | None:
                e = entry(r)
                return (
                    None
                    if e is None
                    else e["artifacts"].setdefault(
                        r["artifact"], {"status": None, "questions": None}
                    )
                )

            for r in rows(
                "SELECT workspace, unit, artifact, to_state, authority, inputs FROM transitions WHERE id IN "
                "(SELECT MAX(id) FROM transitions WHERE {where} GROUP BY workspace, unit, artifact)"
            ):
                a = artifact(r)
                if a is not None and r["to_state"] != self.machine.absent:
                    a["status"] = r["to_state"]
                    # The rows are its questions: an artifact the app holds a state for, with none, asks none.
                    a["questions"] = []
                    # Whose skip it was: the loop stops the unit unless a person's.
                    if r["to_state"] == "skipped":
                        a["authority"] = r["authority"]
                        a["reason"] = json.loads(r["inputs"] or "{}").get("reason") or None
            # Whether the unit is merged: the machine's own fold where it moved the unit, else a ship recorded outside it.
            moved: set[tuple[str, str]] = set()
            for r in rows(
                f"SELECT workspace, unit, guard FROM transitions WHERE id IN (SELECT MAX(id) FROM transitions "
                f"WHERE {{where}} AND source LIKE '{PR_SOURCE}' AND guard IN ({', '.join(repr(g) for g in PR_MOVES)}) "
                f"GROUP BY workspace, unit)"
            ):
                moved.add((r["workspace"], r["unit"]))
                e = entry(r)
                if e is not None:
                    e["merged"] = r["guard"] == MERGED
            for r in rows(
                "SELECT workspace, unit, to_state, source FROM transitions WHERE id IN "
                "(SELECT MAX(id) FROM transitions WHERE {where} AND artifact = 'ship.md' GROUP BY workspace, unit)"
            ):
                e = entry(r)
                if e is not None and (r["workspace"], r["unit"]) not in moved:
                    e["merged"] = (
                        r["to_state"] == "accepted" and r["source"] in SHIPPED_BEFORE_THE_MACHINE
                    )
            # The pull request the machine opened, the last `open` it recorded: its row is `pr.md`'s record.
            for r in rows(
                "SELECT id, workspace, unit, artifact, inputs FROM transitions WHERE id IN (SELECT MAX(id) "
                "FROM transitions WHERE {where} AND artifact = 'pr.md' AND guard = 'branch-named' "
                "GROUP BY workspace, unit)"
            ):
                a = artifact(r)
                if a is not None:
                    read = json.loads(r["inputs"] or "{}")
                    a["pr"] = {"number": read.get("number"), "url": read.get("url")}
                    a["record"] = r["id"]
            # The round a merge was asked at, and what GitHub said when it made none.
            for r in rows(
                "SELECT workspace, unit, artifact, guard, inputs FROM transitions WHERE id IN (SELECT MAX(id) "
                "FROM transitions WHERE {where} AND artifact = 'ship.md' "
                "AND guard IN ('ship-ready', 'merge-refused') GROUP BY workspace, unit)"
            ):
                a = artifact(r)
                if a is not None:
                    read = json.loads(r["inputs"] or "{}")
                    a["ship"] = {
                        "round": read.get("round"),
                        "refused": (read.get("refused") or None)
                        if r["guard"] == "merge-refused"
                        else None,
                    }
            # Whether the unit ever shipped, on either road: a later move of its pull request does not undo it.
            for r in rows(
                f"SELECT DISTINCT workspace, unit FROM transitions WHERE {{where}} AND ("
                f"(source LIKE '{PR_SOURCE}' AND guard = '{MERGED}') OR (artifact = 'ship.md' "
                f"AND to_state = 'accepted' AND source IN ({', '.join(repr(s) for s in SHIPPED_BEFORE_THE_MACHINE)})))"
            ):
                e = entry(r)
                if e is not None:
                    e["shipped"] = True
            # The last stage result of each stage, which the loop reads a spec's `U<n>` and a spike's verdicts from.
            for r in rows(
                "SELECT id, workspace, unit, agent, version, object FROM outputs WHERE id IN "
                "(SELECT MAX(id) FROM outputs WHERE {where} GROUP BY workspace, unit, agent)"
            ):
                a = artifact(
                    {"workspace": r["workspace"], "unit": r["unit"], "artifact": f"{r['agent']}.md"}
                )
                if a is not None:
                    contracts.check_stored(r["agent"], r["version"])
                    a["result"] = contracts.reads(r["agent"], json.loads(r["object"]))
                    a["record"] = r["id"]
                    # A record has no questions of its own to ask until its rows say so.
                    if a["questions"] is None:
                        a["questions"] = []
                    if r["agent"] == "spike":
                        earlier = conn.execute(
                            "SELECT object FROM outputs WHERE root = ? AND workspace = ? AND unit = ? "
                            "AND agent = 'spike' AND id < ? ORDER BY id",
                            (self.root, r["workspace"], r["unit"], r["id"]),
                        ).fetchall()
                        a["round"] = _spike_round(o[0] for o in earlier)
            # Every round a review handed back, read in place of the round of the same number in `review.md`.
            by_id: dict[int, dict[str, Any]] = {}
            for r in rows(
                "SELECT id, workspace, unit, n, head, verdict, screens FROM review_rounds WHERE {where} ORDER BY n"
            ):
                a = artifact(
                    {"workspace": r["workspace"], "unit": r["unit"], "artifact": "review.md"}
                )
                if a is not None:
                    by_id[r["id"]] = {
                        "n": r["n"],
                        "reviewed": r["head"],
                        "verdict": r["verdict"],
                        "screens": json.loads(r["screens"]),
                        "findings": [],
                    }
                    a.setdefault("rounds", []).append(by_id[r["id"]])
                    a["record"] = max(a.get("record") or 0, r["id"])
                    if a["questions"] is None:
                        a["questions"] = []
            for r in rows(
                "SELECT f.round, f.finding, f.label, f.fixed_in, f.severity, f.rule, f.path, f.lines, f.text "
                "FROM review_findings f JOIN review_rounds ON f.round = review_rounds.id WHERE {where} ORDER BY f.rowid"
            ):
                if r["round"] in by_id:
                    by_id[r["round"]]["findings"].append(
                        {
                            "id": r["finding"],
                            "label": r["label"],
                            "fixedIn": r["fixed_in"] or None,
                            "severity": r["severity"],
                            "rule": r["rule"],
                            "path": r["path"],
                            "lines": r["lines"],
                            "text": r["text"],
                        }
                    )
            for r in rows(
                "SELECT workspace, unit, artifact, n, text, recommendation FROM unit_questions "
                "WHERE {where} ORDER BY rowid"
            ):
                a = artifact(r)
                if a is not None:
                    a["questions"] = [
                        *(a["questions"] or []),
                        {"n": r["n"], "text": r["text"], "recommendation": r["recommendation"]},
                    ]
            for r in rows(
                "SELECT workspace, unit, artifact, field, reason FROM unit_unknowns WHERE {where}"
            ):
                e = entry(r)
                if e is None:
                    continue
                e["unknowns"].append(
                    {"artifact": r["artifact"], "field": r["field"], "reason": r["reason"]}
                )
            for r in rows(
                "SELECT workspace, unit, kind, ref FROM unit_links WHERE {where} ORDER BY pos"
            ):
                e = entry(r)
                if e is None:
                    continue
                if r["kind"] == "depends":
                    e["links"]["dependsOn"] = [*(e["links"]["dependsOn"] or []), r["ref"]]
                else:
                    e["links"][r["kind"]] = r["ref"]
            for (ws, name), others in waited.items():
                e = units.get(f"{name_of[ws]}/{name}")
                if e is not None:
                    e["links"]["backlog"] = [{"ref": o, "source": "backlog"} for o in others]
            for r in rows(
                'SELECT workspace, unit, artifact, ref, text, "by", name, date, via '
                "FROM unit_answers WHERE {where} ORDER BY id"
            ):
                e = entry(r)
                if e is not None:
                    number = r["ref"].isdigit()
                    e["answers"].append(
                        {
                            "artifact": r["artifact"],
                            "n": int(r["ref"]) if number else None,
                            "id": None if number else r["ref"],
                            "by": r["by"],
                            "name": r["name"],
                            "date": r["date"],
                            "via": r["via"],
                            "text": r["text"],
                        }
                    )
            for r in rows(
                "SELECT workspace, unit, move, reason, decided_by, date, via FROM unit_holds WHERE {where} ORDER BY id"
            ):
                e = entry(r)
                if e is not None:
                    e["holds"].append(
                        {
                            "state": r["move"],
                            "reason": r["reason"],
                            "by": r["decided_by"],
                            "date": r["date"],
                            "via": r["via"],
                        }
                    )
            # A rerun names the record each artifact it made stale held; more rounds add up.
            for r in rows(
                "SELECT workspace, unit, kind, fields, date FROM unit_decisions "
                "WHERE {where} AND kind IN ('rerun', 'more-rounds') ORDER BY id"
            ):
                e = entry(r)
                if e is None:
                    continue
                fields = json.loads(r["fields"])
                if r["kind"] == "rerun":
                    e["reruns"].append(
                        {"stage": fields["stage"], "stale": fields["stale"], "date": r["date"]}
                    )
                else:
                    e["roundsGranted"] += int(fields["rounds"])
        return {
            "workspace": own_name,
            "workspaces": sorted(n for n in named if n),
            "units": units,
        }
