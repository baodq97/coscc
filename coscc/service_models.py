"""Which model and effort each stage runs on, and the autopilot's settings.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from coscc import autopilot
from coscc import board as board_reader
from coscc.board import Unavailable
from coscc.data import Data
from coscc.journal import BadRecord, Busy, Journal
from coscc import labels, models
from coscc.run import LOOPBACK
from coscc.runner import SESSIONS_PER_STEP
from coscc.service_common import Invalid


def _whole_at_least_one(value: Any) -> bool:
    """`max_parallel`: an int, not a bool, 1 or more (`0043` R2)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_number(value: Any) -> bool:
    """`daily_cap_usd`: a finite number above 0, not a bool (`0043` R2)."""
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value > 0
    )


class ModelsMixin:

    # -- which model each stage runs on --------------------------------------
    #
    # `0004_no-setting-says-which-model-runs-a-stage`. The resolving is `coscc/models.py`;
    # this is where its three inputs are gathered: the stage list from `cos.mjs`, the
    # overrides from `prefs`, `COS_MODEL` from `Config`.

    def _model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def _effort_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def _model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat, and for Gebo on `impl`'s base row. Never raises on bad data.

        Takes no stage list: the caller has already checked `name` against `cos.mjs`
        (`run_step` found the row), and resolving one row does not need the others.
        A board step goes through `_stage_config` instead, which also reads the label.
        """
        overrides, _ = self._model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, None, overrides, {}, defaults, self.config.model)[:2]

    def _stage_config(
        self, stage: str, stages: list[str], directory: Path, journal: Journal, key: str, unit: str
    ) -> dict[str, Any]:
        """`0033`. The label a step runs under, the model and effort it resolves to, and
        for `impl` which run of the unit's this is. Called after the gate, before any money
        is spent. The label chooses a configuration and nothing else (spec R11).

        `Busy` from the run log is left to the caller, as `failed_attempts` is.
        """
        try:
            plan_text: str | None = (Path(directory) / "plan.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            plan_text = None
        history = [r for r in journal.records(key, unit) if r.get("stage") == "impl"]
        label_declared, label, label_source = labels.label_for(stage, stages, plan_text, history)
        model, model_source, effort, effort_source = models.resolve(
            stage, label, self._model_overrides()[0], self._effort_overrides()[0],
            models.load_defaults()[0], self.config.model,
        )
        return {
            "model": model, "model_source": model_source,
            "effort": effort, "effort_source": effort_source,
            "label_declared": label_declared, "label": label, "label_source": label_source,
            # R10: every `start` of `impl` counts, the review-driven fixes included; the
            # reading "before the first `pr`" is done from the log (spec Answers, câu 2).
            "impl_run": (
                sum(1 for r in history if r.get("kind") == "start") + 1 if stage == "impl" else None
            ),
        }

    async def _findings_added(self, cwd: str, unit: str, before: set[Any]) -> dict[str, Any]:
        """`0033` R10. The findings in the rounds a `review` step added, off the board —
        `parseReview`'s count, read the way `_post_new_rounds` reads it. `0093` R9: and
        those rounds' verdicts, each a string, for *Changes-requested rounds*."""
        data = await board_reader.read(self._units_root(cwd))
        found = next((u for u in data["units"] if u["name"] == unit), None) or {}
        added = [r for r in found.get("rounds") or [] if r.get("n") not in before]
        return {
            "findings": sum(int(r.get("findings") or 0) for r in added),
            "findings_open": sum(int(r.get("findings_open") or 0) for r in added),
            "verdicts": [str(r.get("verdict") or "") for r in added],
        }

    async def stage_models(self) -> dict[str, Any]:
        """Every row Settings shows: stage, agents, model, effort, where each came from.

        When `node` cannot run there is no stage list, and inventing one here would be the
        second copy of the loop. So the table is empty and `problems` says why.
        """
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            return {"rows": [], "problems": [str(e)], "cos_model": self.config.model}
        overrides, bad_rows = self._model_overrides()
        efforts, bad_efforts = self._effort_overrides()
        defaults, bad_defaults = models.load_defaults()
        table = models.table(stages, overrides, efforts, defaults, self.config.model, SESSIONS_PER_STEP)
        for r in table["rows"]:
            r["overridden"] = r["name"] in overrides
            r["effort_overridden"] = r["name"] in efforts
        table["problems"] = bad_defaults + bad_rows + bad_efforts + table["problems"]
        table["cos_model"] = self.config.model
        return table

    async def _setting_row(self, name: Any, allow_chat: bool) -> str:
        """Check a Settings row name against `cos.mjs`: a stage, `<stage>:novel` for a
        stage after `plan`, or `chat` when the setting has one."""
        if not isinstance(name, str) or not name:
            raise Invalid("name is required")
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            raise Invalid(str(e)) from e
        allowed = [r for r in models.rows_for(stages) if allow_chat or r != models.CHAT]
        if name not in allowed:
            raise Invalid(f"no such stage: {name} (use one of {', '.join(allowed)})")
        return name

    def _log_setting(self, key: str, old: Any, new: Any) -> None:
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({
                    "kind": "setting", "workspace": "", "unit": "", "stage": "",
                    "name": key, "old": old, "new": new,
                })
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e

    async def set_stage_model(self, name: Any, model: Any = None) -> dict[str, Any]:
        """Set one row's model, or remove the override when `model` is None.

        **Behind the password like every route here** (`0070`): whoever holds it or a live
        session can move `review` to a weaker model, or every stage to a dearer one. The one trace is the `setting`
        record appended below, with the old and new value. It chooses a model and nothing
        else: no gate reads it, and no stage starts because of it.

        The model name is not checked against the API — an unknown one fails at the next
        step of that stage, with the CLI's own error (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=True)
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise Invalid("model is required")
            model = model.strip()

        data = Data(self.config.data_dir)
        key = models.PREFIX + name
        old = self._model_overrides()[0].get(name)
        if model is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, model)
        self._log_setting(key, old, model)
        return await self.stage_models()

    async def set_stage_effort(self, name: Any, effort: Any = None) -> dict[str, Any]:
        """`0033` R9. Set one row's effort, or remove the override when `effort` is None.

        The same exposure as `set_stage_model`, and the `setting` record is the
        trace. `max` is accepted here and only here — `models.json` may not ship it, so
        every `max` run traces back to one of these records (spec R7, C8). `chat` has no
        effort (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=False)
        if effort is not None and effort not in models.EFFORTS:
            raise Invalid(f"effort must be one of {', '.join(models.EFFORTS)}")

        data = Data(self.config.data_dir)
        key = models.EFFORT_PREFIX + name
        old = self._effort_overrides()[0].get(name)
        if effort is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, effort)
        self._log_setting(key, old, effort)
        return await self.stage_models()

    # -- the autopilot's settings (`0043` R1, R2) ----------------------------
    #
    # In the data root's `prefs`, not the workspace's repository. Three per workspace, keyed
    # by the journal key; the cap is one for the whole app, since the quota is the machine's
    # account (`intent.md ## Answers`, câu 9). Not in `PREFERENCES`: those are the page's.

    AUTOPILOT_SETTINGS = ("autopilot", "autopilot_may_ship", "max_parallel", "daily_cap_usd")
    CAP_PREF = "autopilot_daily_cap_usd"

    def _autopilot_pref(self, name: str, key: str) -> str:
        return self.CAP_PREF if name == "daily_cap_usd" else f"{name}:{key}"

    def _autopilot_values(self, key: str) -> dict[str, Any]:
        """The four values in effect. A hand-edited value of the wrong type reads as its
        default, and the default of both switches is off."""
        data = Data(self.config.data_dir)

        def read(name: str, ok: Any, default: Any) -> Any:
            value = data.pref(self._autopilot_pref(name, key), default)
            return value if ok(value) else default

        return {
            "autopilot": read("autopilot", lambda v: v is True or v is False, False),
            "autopilot_may_ship": read("autopilot_may_ship", lambda v: v is True or v is False, False),
            "max_parallel": read("max_parallel", _whole_at_least_one, autopilot.DEFAULT_MAX_PARALLEL),
            "daily_cap_usd": float(read("daily_cap_usd", _positive_number, autopilot.DEFAULT_DAILY_CAP_USD)),
        }

    def _off_loopback(self) -> str:
        """Why the autopilot may not run on this bind, or `""` (`spec.md ## Answers`, câu 3)."""
        if self.config.host in LOOPBACK:
            return ""
        # S3: no variable name here, the page shows it verbatim; `coscc-settings.md` names it.
        return f"The app listens on {self.config.host}, beyond this machine; restart it on 127.0.0.1 to use the autopilot."

    def autopilot_settings(self, cwd: str) -> dict[str, Any]:
        """The four settings of one workspace, and whether the bind lets the autopilot run."""
        self._workspace_or_refuse(cwd)
        return {
            "cwd": cwd,
            **self._autopilot_values(self._journal_key(cwd)),
            "refused_because": self._off_loopback(),
        }

    def set_autopilot(self, cwd: str, name: Any, value: Any) -> dict[str, Any]:
        """Set one of the four. A wrong value is refused and nothing is written (R2).

        Behind the password like every route: whoever holds it or a live session can turn
        the autopilot on, raise the cap, or let it ship. The trace is the `setting` record.
        Turning it on is refused while the app listens beyond loopback.
        """
        self._workspace_or_refuse(cwd)
        if name not in self.AUTOPILOT_SETTINGS:
            raise Invalid(f"no such setting: {name} (use one of {', '.join(self.AUTOPILOT_SETTINGS)})")
        if name in ("autopilot", "autopilot_may_ship"):
            if value is not True and value is not False:
                raise Invalid(f"{name} must be true or false")
        elif name == "max_parallel":
            if not _whole_at_least_one(value):
                raise Invalid("max_parallel must be a whole number, 1 or more")
        elif not _positive_number(value):
            raise Invalid("daily_cap_usd must be a number above 0")
        if name == "autopilot" and value and self._off_loopback():
            raise Invalid(f"the autopilot was not turned on: {self._off_loopback()}")
        key = self._journal_key(cwd)
        old = self._autopilot_values(key)[name]
        stored = float(value) if name == "daily_cap_usd" else value
        Data(self.config.data_dir).set_pref(self._autopilot_pref(name, key), stored)
        self._log_setting(self._autopilot_pref(name, key), old, stored)
        if name == "autopilot":
            if value:
                self.autopilot_start(cwd)
            else:
                self.autopilot_stop(key)
        return self.autopilot_settings(cwd)
