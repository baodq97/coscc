"""Which agent each stage's session is, with the overrides Settings holds.

`0036_stage-agents-do-not-know-who-they-are` R2. The resolving is `coscc/agent/agents.py`;
this is where the overrides are read from `prefs` and written back. A mixin with no fields,
like `ModelsMixin`, which `Service` inherits.
"""

from __future__ import annotations

from typing import Any

from coscc.agent import agents
from coscc.data import Data
from coscc.service.common import Invalid


class AgentsMixin:

    def _agent_overrides(self) -> tuple[dict[str, dict[str, str]], list[str]]:
        """The stored overrides, or none and why when `cos.db` cannot be read: a name is
        never a reason to refuse a step or a board read (spec R2)."""
        try:
            rows = Data(self.config.data_dir).pref_rows(agents.PREFIX)
        except Exception as e:  # noqa: BLE001 — `Busy`, `Protected`, `Incompatible` alike
            return {}, [f"the agent overrides could not be read, so the defaults apply: {e}"]
        return agents.overrides_from(rows)

    def _agent(self, key: str) -> dict[str, Any] | None:
        """The resolved row for `key`, overrides included, or `None`. Every place in the
        service that shows or writes an agent's name asks this. Never raises on bad data."""
        return agents.agent_for(key, self._agent_overrides()[0])

    def agent_table(self) -> dict[str, Any]:
        """Every row Settings shows, each with `overridden`, and what was wrong."""
        overrides, bad = self._agent_overrides()
        found = agents.table(overrides)
        for row in found["rows"]:
            row["overridden"] = row["key"] in overrides
        found["problems"] = bad + found["problems"]
        return found

    def set_agent(self, key: Any, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        """Set some fields of one row's override; `""` removes that field's, and no fields
        at all removes the row's. A wrong field is refused and nothing is written (R2).

        **Behind the password like every route here**: whoever holds it or a live session can
        rename any agent, and the name goes into prompts, commits and review comments. The
        trace is the `setting` record appended below, with the old and new override. A name
        opens and closes nothing (spec C6).
        """
        if not isinstance(key, str) or not key:
            raise Invalid("key is required")
        defaults, _ = agents.load_defaults()
        if key not in defaults:
            raise Invalid(f"no such agent: {key} (use one of {', '.join(defaults)})")
        fields = dict(fields or {})
        for field, value in fields.items():
            if field not in agents.FIELDS:
                raise Invalid(f"no such field: {field} (use one of {', '.join(agents.FIELDS)})")
            if value == "":
                continue
            reason = agents.check_field(field, value)
            if reason:
                raise Invalid(reason)

        overrides, _ = self._agent_overrides()
        old = overrides.get(key)
        if fields:
            new = {**(old or {}), **fields}
            new = {f: v for f, v in new.items() if v != ""} or None
        else:
            new = None
        if new and "name" in new:
            taken = {
                str(agents.resolve(k, defaults, overrides)["name"]).lower(): k
                for k in defaults if k != key
            }
            if new["name"].lower() in taken:
                raise Invalid(f"the name {new['name']} is already {taken[new['name'].lower()]}'s")

        data = Data(self.config.data_dir)
        if new is None:
            data.delete_pref(agents.PREFIX + key)
        else:
            data.set_pref(agents.PREFIX + key, new)
        self._log_setting(agents.PREFIX + key, old, new)
        return self.agent_table()
