"""The knowledge store, as the app reaches it: a gather after a ship, and the Knowledge page.

`0131_the-knowledge-store-goes-stale-after-one-gather`. The one service module that may import
what gathers or measures (`coscc/knowledge/cli_test.py` holds that), and `_gather_soon` is
called from one place, `_drive`, once a `ship` step ends `done` with `COS_KNOWLEDGE` on (R1).
Before `0131` only a terminal gathered (`0090` R9); the grant is still `knowledge`, still in
`TERMINAL_ONLY`, still with no tool (spec Design 1): what changed is who calls it.

`knowledge_page` reads and writes nothing else: the store, `knowledge.HEALTH` and `cos.db` at
`mode=ro`. It fetches nothing and gathers nothing (R24, Design 3).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from coscc import knowledge, units
from coscc.agent.sessions import Sessions
from coscc.knowledge import gather, measure

# R25 (3): how many steps the page lists.
RECENT = 20


def _sources(entry: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for s in entry["sources"]:
        found = knowledge.parts_of(s)
        out.append(found or {"slot": "", "unit": "", "file": "", "anchor": s})
    return out


class KnowledgeMixin:

    def _gather_soon(self, cwd: str, unit: str, key: str) -> None:
        """R1. Start `unit`'s gather in the background and return at once; the task is kept,
        so nothing collects it while it runs."""
        task = asyncio.create_task(self._gather_unit(cwd, unit, key))
        self._gathers.add(task)
        task.add_done_callback(self._gathers.discard)

    async def _gather_unit(self, cwd: str, unit: str, key: str) -> dict[str, Any] | None:
        """One gather of `unit`, its record, or `None` when it did not run. Never raises.

        With no run log it does not run: a gather that spent and could not be recorded is
        money nobody can count (`coscc/knowledge/cli.py`). Its `Sessions` is its own: `gather`
        narrows `membership` to the store's directory, which on the app's would refuse every
        step after it (plan Risk 2). It is listed while it runs, so an update waits for it."""
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
        """R24-R26. What the Knowledge page shows of this workspace: the entries it receives
        with what the last check said of each, the last gather, the steps that were handed the
        store, and the measure as the run log stands, unfetched."""
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
                # `None`: no check has read it (R26).
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
