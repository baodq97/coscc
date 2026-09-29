"""The knowledge store, as the app reaches it: a gather after a ship, and the Knowledge page.

The one service module that may import what gathers or measures. `_gather_soon` is called
from `_drive` once a `ship` step ends `done` with `COS_KNOWLEDGE` on. `knowledge_page` reads
the store, `knowledge.HEALTH` and `cos.db` at `mode=ro`; it fetches and gathers nothing.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from coscc import knowledge, units
from coscc.agent.sessions import Sessions
from coscc.knowledge import gather, measure
from coscc.runlog.journal import BadRecord, Busy
from coscc.update import updater as updater_mod

# # How many steps the page lists.
RECENT = 20


def _sources(entry: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for s in entry["sources"]:
        found = knowledge.parts_of(s)
        out.append(found or {"slot": "", "unit": "", "file": "", "anchor": s})
    return out


class KnowledgeMixin:

    def _gather_soon(self, cwd: str, unit: str, key: str) -> None:
        """Start `unit`'s gather in the background and return at once; the task is kept, so nothing
        collects it while it runs.

        Not once Apply is pressed, which waits for a gather already running and must not wait for
        one begun after. The refusal is the gather's own `knowledge` record, `refused`.
        """
        try:
            self.updater.refuse_mechanical_while_updating()
        except updater_mod.Refused as e:
            journal = self._journal()
            if journal is not None:
                try:
                    journal.append({
                        "kind": gather.KIND, "workspace": units.slot(cwd), "mode": "unit", "unit": unit,
                        "cost_usd": 0.0, "outcome": "refused", "reason": f"no session opened: {e}",
                        "dropped": [], "sessions": [], "origin_main": {},
                    })
                except (BadRecord, Busy):
                    pass
            return
        task = asyncio.create_task(self._gather_unit(cwd, unit, key))
        self._gathers.add(task)
        task.add_done_callback(self._gathers.discard)

    async def _gather_unit(self, cwd: str, unit: str, key: str) -> dict[str, Any] | None:
        """One gather of `unit`, its record, or `None` when it did not run. Never raises.

        With no run log it does not run: spend that cannot be recorded cannot be counted. Its
        `Sessions` is its own, since `gather` narrows `membership` to the store's directory, which
        would refuse every later step on the app's. It is listed while it runs, so an update waits.
        """
        journal = self._journal()
        if journal is None:
            return None
        rid = self._mark_running(key, unit, "knowledge", "knowledge")
        try:
            return await gather.gather_unit(
                self.config.data_dir, journal, Sessions(self.config), self.config.model,
                units.slot(cwd), unit, cwd, lambda _said: None,
            )
        except Exception:  # noqa: BLE001 — `gather_unit` records its own failures; nothing else may stop the app
            return None
        finally:
            self._running.pop(rid, None)
            self.updater.job_ended()

    def knowledge_page(self, cwd: str) -> dict[str, Any]:
        """What the Knowledge page shows of this workspace: the entries it receives with what the last
        check said of each, the last gather, the steps that were handed the store, and the measure
        as the run log stands, unfetched.
        """
        self._workspace_or_refuse(cwd)
        slot = units.slot(cwd)
        out: dict[str, Any] = {"slot": slot, "note": "", "entries": [], "checked": None,
                               "last_gather": None, "recent": [], "measure": None, "log_note": ""}
        try:
            text = knowledge.load(knowledge.path_of(self.config.data_dir) / knowledge.STORE)
            entries = knowledge.for_workspace(knowledge.parse(text)["entries"], slot)
        except FileNotFoundError:
            entries, out["note"] = [], "No knowledge has been gathered yet."
        except (OSError, UnicodeDecodeError) as e:
            entries, out["note"] = [], f"The knowledge store cannot be read: {type(e).__name__}."
        if not entries and not out["note"]:
            out["note"] = "The knowledge store holds no entry for this workspace."
        health: dict[str, Any] = {}
        try:
            raw = json.loads((knowledge.path_of(self.config.data_dir) / knowledge.HEALTH).read_text(encoding="utf-8"))
            health = raw if isinstance(raw, dict) and isinstance(raw.get("entries"), dict) else {}
        except (OSError, ValueError):
            health = {}
        if health:
            out["checked"] = {"sha": str((health.get("sha") or {}).get(slot) or ""), "at": str(health.get("at") or "")}
        for e in sorted(entries, key=lambda e: e["id"]):
            k = f"K{e['id']}"
            verdict = health.get("entries", {}).get(k) if health else None
            out["entries"].append({
                "id": k, "scope": e["scope"], "statement": e["statement"], "measured": e.get("measured") or "",
                "sources": _sources(e), "refs": list(e.get("refs") or []),
                # `None`: no check has read it.
                "broken": verdict if isinstance(verdict, str) else None,
            })

        try:
            db = measure._db(self.config.data_dir)
            rows = measure.read_rows(db) if db.is_file() else []
        except (OSError, sqlite3.Error) as e:
            rows, out["log_note"] = [], f"The run log cannot be read: {type(e).__name__}."
        gathers = [r for r in rows if r.get("kind") == gather.KIND and r.get("workspace") == slot]
        if gathers:
            last = gathers[-1]
            out["last_gather"] = {
                "at": str(last.get("at") or ""), "unit": str(last.get("unit") or ""),
                "mode": str(last.get("mode") or ""), "outcome": str(last.get("outcome") or ""),
                "reason": str(last.get("reason") or ""), "cost_usd": last.get("cost_usd"),
            }
        of: dict[str, str] = {}
        recent = []
        for r in reversed(rows):
            if r.get("kind") != "start" or knowledge.TRIAL_FIELD not in r:
                continue
            w = str(r.get("workspace") or "")
            if w not in of:
                of[w] = units.slot(w) if w else ""
            if of[w] != slot:
                continue
            record = r.get("knowledge") if isinstance(r.get("knowledge"), dict) else {}
            recent.append({
                "unit": str(r.get("unit") or ""), "stage": str(r.get("stage") or ""), "at": str(r.get("at") or ""),
                "arm": str(measure.arm_of(r) or ""), "ids": list(record.get("ids") or []),
                "withheld": [w for w in record.get("withheld") or [] if isinstance(w, dict)],
            })
            if len(recent) == RECENT:
                break
        out["recent"] = recent
        out["measure"] = measure.measure(rows, slot)
        return out
