"""Activity, usage and cost, an artifact's text, settings and preferences.

A mixin with no fields, inherited by `Service`.
"""

from __future__ import annotations

import sys
from typing import Any

from coscc.runlog import events
from coscc.runlog import spend
from coscc.data import Data
from coscc.runlog.journal import COST_FIELDS, COST_USD, Busy, add_cost, zero_cost
from coscc.agent.policy import GRANTS, NOVEL_CEILINGS, PROSE_STAGES, grant_for_step
from coscc.agent import labels
from coscc.service.common import Invalid, STAGE_FILES, consequence


class ActivityMixin:

    # -- activity, usage and settings ---------------------------------------

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded.

        Shared by `activity` and `usage`; see `activity_and_usage` for why it is read once.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return None
        try:
            return journal.records(self._journal_key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def _events_of(self, cwd: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        events = [
            {
                "at": r.get("at") or "",
                "kind": r.get("kind") or "",
                "unit": r.get("unit") or "",
                "stage": r.get("stage") or "",
                "mode": r.get("mode") or "",
                "outcome": r.get("outcome") or "",
                "session_id": r.get("session_id") or "",
                "artifact": r.get("artifact") or "",
                "denials": int(r.get("denials") or 0),
                "cost": {f: r.get(f) for f in COST_FIELDS + (COST_USD,) if r.get(f)},
                # A `hold` row's move, reason, name and side effects; empty on every other kind.
                "from": r.get("from") or "",
                "to": r.get("to") or "",
                "reason": r.get("reason") or "",
                "by": r.get("by") or "",
                "effects": [e for e in r.get("effects") or [] if isinstance(e, dict)],
                # A `release` row's version and what happened; empty on every other kind.
                "version": str(r.get("version") or "") if r.get("kind") == "release" else "",
                "detail": str(r.get("detail") or "") if r.get("kind") == "release" else "",
            }
            for r in rows
            # A retake of the screenshots is recorded, and shown on no screen.
            if r.get("kind") != "screens"
        ]
        events.reverse()
        return {"cwd": cwd, "events": events[:limit], "recording": True}

    def _usage_of(self, cwd: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        # Only known costs are added; `unknown` counts the `end` rows that carried no `cost_usd`, so the sum is never shown as the whole of it.
        per_unit: dict[str, dict[str, Any]] = {}
        for record in rows:
            if record.get("kind") != "end":
                continue
            bucket = per_unit.setdefault(str(record.get("unit") or ""), {**zero_cost(), "unknown": 0})
            add_cost(bucket, record)
            bucket["unknown"] += int(COST_USD not in record)
        total = {**zero_cost(), "unknown": 0}
        for bucket in per_unit.values():
            add_cost(total, bucket)
            total["unknown"] += bucket["unknown"]
        return {"cwd": cwd, "total": total, "per_unit": per_unit, "recording": True}

    def activity(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """What has happened across the whole workspace, newest first.

        Built from one read, not by calling `timeline` once per unit, which would spawn one board read per unit.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "events": [], "recording": False}
        return self._events_of(cwd, rows, limit)

    def usage(self, cwd: str) -> dict[str, Any]:
        """What this workspace has cost, added up from its records.

        Added rather than stored: a stored total is a second number that can disagree with the first.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "total": {}, "per_unit": {}, "recording": False}
        return self._usage_of(cwd, rows)

    def activity_and_usage(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """Both of the above, from one read: the page calls this rather than the two public methods, which parse the same rows twice."""
        rows = self._records_or_none(cwd)
        if rows is None:
            return {
                "cwd": cwd, "events": [], "total": {}, "per_unit": {}, "recording": False,
            }
        return {**self._events_of(cwd, rows, limit), **self._usage_of(cwd, rows)}

    def cost(self, cwd: str, rounds: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """Where this workspace's money went, from one read of its run log.

        `rounds` is each unit's review verdicts, from the board read the page already has. Read only; no gate reads a figure here.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "recording": False}
        return {**spend.model(rows, rounds), "recording": True}

    def unit_cost(self, cwd: str, unit: str) -> dict[str, Any]:
        """One unit's cost by stage and its anomalies.

        The whole log is read, not the unit's rows: a token-per-turn median is the workspace's.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"by_stage": [], "anomalies": [], "recording": False}
        found = spend.model(rows)
        return {
            "by_stage": found["unit_stages"].get(unit, []),
            "anomalies": [a for a in found["anomalies"] if a["unit"] == unit],
            "recording": True,
        }

    def settings(self) -> dict[str, Any]:
        """The safety posture, as something a screen can render. Read only.

        The screen shows the four knobs and the grant table and can change neither; there is no setter here, as in `config.from_env`.

        The model per stage is the one thing Settings can change (`stage_models`, `set_stage_model`), so it is not here. The route decides what every step spends for whoever holds the password or a live session. `cos_model` below is only the fallback for a row nothing else answers.
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "cos_model": c.model,
            "knobs": [
                {
                    "name": "tools",
                    "value": ", ".join(c.effective_tools()) or "none",
                    "on": bool(c.effective_tools()),
                    "detail": "The tools a chat session gets; none means chat only.",
                },
                {
                    "name": "allow_write_and_exec",
                    "value": "on" if c.allow_write_and_exec else "off",
                    "on": c.allow_write_and_exec,
                    "detail": "While off, no chat session can write files or run commands.",
                },
                {
                    "name": "bypass_permissions",
                    "value": "on" if c.bypass_permissions else "off",
                    "on": c.bypass_permissions,
                    "detail": "Set only by the environment the app started with.",
                },
                {
                    "name": "resume_foreign_sessions",
                    "value": "on" if c.resume_foreign_sessions else "off",
                    "on": c.resume_foreign_sessions,
                    "detail": "The app resumes only the sessions it created.",
                },
            ],
            # The board's own grants, from `policy.py` rather than from the config; they are
            # separate on purpose, and the screen shows that. A stage with its own `novel`
            # ceilings shows them as `<stage>:novel`, right after its own row.
            "grants": [
                {
                    "stage": name,
                    "tools": ", ".join(grant.tools) or "none",
                    "commands": ", ".join(grant.commands) or "none",
                    # The same, one item each, for the page to list.
                    "tool_list": list(grant.tools),
                    "command_list": list(grant.commands),
                    "max_turns": grant.max_turns,
                    "max_budget_usd": grant.max_budget_usd,
                    "budget": f"${grant.max_budget_usd:.2f}",
                    "app_writes_artifact": grant.app_writes_artifact,
                    "warning": grant.warning,
                    "consequence": consequence(name),
                }
                for stage, own in sorted(GRANTS.items())
                for name, grant in (
                    [(stage, own)]
                    + ([(f"{stage}:{labels.NOVEL}", grant_for_step(stage, labels.NOVEL))]
                       if stage in NOVEL_CEILINGS else [])
                )
            ],
            "prose_stages": list(PROSE_STAGES),
            "import_report": self._import_report(),
        }

    def _import_report(self) -> dict[str, Any]:
        """Every field an import could not read, its workspace by name, not a failed ingest: the card shows that one. A database that cannot be read is `problem`, in place of a Settings screen that does not load. What went wrong goes to the log, not the screen: `Busy` and `Incompatible` name the database's path."""
        names = {self._journal_key(r["path"]): str(r["name"]) for r in self.workspaces()["workspaces"]}
        try:
            found = self._unit_meta().unknowns()
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            print(f"coscc: the import report could not be read: {e}", file=sys.stderr)
            return {"rows": [], "problem": "The import report could not be read."}
        return {
            "rows": [{**r, "workspace": names.get(r["workspace"], "a removed workspace")} for r in found],
            "problem": "",
        }

    # -- artifacts and preferences ------------------------------------------

    def artifact(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The text of one stage's artifact, or why there is none.

        The path is built by `runner.unit_dir`, which validates the unit name against the `NNNN_slug` shape, so a name this refuses is one no step could run against either.
        """
        self._workspace_or_refuse(cwd)
        if stage not in STAGE_FILES:
            raise Invalid(f"no such stage: {stage}")
        filename = f"{stage}.md"
        path = self._unit_dir(cwd, unit) / filename
        if not path.is_file():
            return {"unit": unit, "stage": stage, "file": filename, "text": "", "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Invalid(f"could not read {filename}: {e}") from e
        return {"unit": unit, "stage": stage, "file": filename, "text": text, "exists": True}

    # Which preferences the page may keep: an open key/value store reachable from a request
    # is a place to put anything, so only the keys the Settings screen remembers are writable.
    PREFERENCES = {"density": "comfortable", "screen": "overview", "board_view": "Board"}

    def preferences(self) -> dict[str, Any]:
        data = Data(self.config.data_dir)
        stored = data.prefs()
        return {k: stored.get(k, default) for k, default in self.PREFERENCES.items()}

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        if key not in self.PREFERENCES:
            raise Invalid(f"not a stored preference: {key}")
        if not isinstance(value, (str, int, float, bool)):
            raise Invalid("a preference must be a simple value")
        Data(self.config.data_dir).set_pref(key, value)
        return {"key": key, "value": value}
