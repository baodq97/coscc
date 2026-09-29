"""Which model and effort each stage runs on, and the autopilot's settings. A mixin with no fields."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from coscc.units import autopilot
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.data import Data
from coscc.runlog.journal import BadRecord, Journal
from coscc.data import Busy
from coscc.agent import labels, models, modeltrial
from coscc.github import prmachine
from coscc.config import LOOPBACK
from coscc.runner import SESSIONS_PER_STEP
from coscc.service.common import Invalid

log = logging.getLogger(__name__)


def _whole_at_least_one(value: Any) -> bool:
    """`max_parallel`: an int, not a bool, 1 or more."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_number(value: Any) -> bool:
    """`daily_cap_usd`: a finite number above 0, not a bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


class ModelsMixin:
    # -- which model each stage runs on --------------------------------------
    #
    # The resolving is `coscc/agent/models.py`; this gathers its inputs: the stage list from
    # `cos.mjs`, the overrides from `prefs`, `COS_MODEL` from `Config`.

    def _model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def _effort_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def _model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat, and for Gebo on `impl`'s base row. Never raises on bad data.

        A board step goes through `_stage_config` instead, which also reads the label.
        """
        overrides, _ = self._model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, None, overrides, {}, defaults, self.config.model)[:2]

    def _stage_config(
        self, stage: str, stages: list[str], directory: Path, journal: Journal, key: str, unit: str
    ) -> dict[str, Any]:
        """The label a step runs under, the model and effort it resolves to, and for `impl`
        which run of the unit's this is. Called after the gate, before any money is spent.
        The label chooses a configuration and nothing else.

        `Busy` from the run log is left to the caller, as `failed_attempts` is.

        On a routine `impl`, with no flag, the unit's arm names the model handed to `resolve`,
        and `trial_record` says which arm and what was asked for; the model the session really
        ran is filled in once its `init` names it. Any other step has no `trial_record` key.
        """
        try:
            plan_text: str | None = (Path(directory) / "plan.md").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            plan_text = None
        history = [r for r in journal.records(key, unit) if r.get("stage") == "impl"]
        label_declared, label, label_source = labels.label_for(stage, stages, plan_text, history)
        arm = modeltrial.arm(unit) if modeltrial.applies(stage, label) else None
        trial_kw = {"trial_model": modeltrial.model_for(stage, label, arm)} if arm else {}
        model, model_source, effort, effort_source = models.resolve(
            stage,
            label,
            self._model_overrides()[0],
            self._effort_overrides()[0],
            models.load_defaults()[0],
            self.config.model,
            **trial_kw,
        )
        trial_record = (
            {"trial_record": {modeltrial.FIELD: {"arm": arm, "requested": model}}} if arm else {}
        )
        return {
            **trial_record,
            "model": model,
            "model_source": model_source,
            "effort": effort,
            "effort_source": effort_source,
            "label_declared": label_declared,
            "label": label,
            "label_source": label_source,
            # Every `start` of `impl` counts, the review-driven fixes included.
            "impl_run": (
                sum(1 for r in history if r.get("kind") == "start") + 1 if stage == "impl" else None
            ),
        }

    async def _ci_red(self, cwd: str, unit: str, repo: str) -> bool | None:
        """Whether `cos.mjs next` sends `unit` back to `impl` because CI is red, read with
        `autopilot.is_ci_red`; `None` when it could not be asked. Never raises."""
        try:
            found = await board_reader.next_step(
                self._units_root(cwd), unit, repo=repo, state=self._snapshot(cwd, [unit])
            )
            return autopilot.is_ci_red(found)
        except Exception:
            # Recorded as null.
            log.exception("whether the CI of %s is red could not be read", unit)
            return None

    async def _findings_added(self, cwd: str, unit: str, before: set[Any]) -> dict[str, Any]:
        """The findings in the rounds a `review` step added, off the board (`parseReview`'s
        count, read the way `_post_new_rounds` reads it), and those rounds' verdicts."""
        data = await board_reader.read(self._units_root(cwd), state=self._snapshot(cwd))
        found = next((u for u in data["units"] if u["name"] == unit), None) or {}
        added = [r for r in found.get("rounds") or [] if r.get("n") not in before]
        return {
            "findings": sum(int(r.get("findings") or 0) for r in added),
            "findings_open": sum(int(r.get("findings_open") or 0) for r in added),
            "verdicts": [str(r.get("verdict") or "") for r in added],
        }

    async def stage_models(self) -> dict[str, Any]:
        """Every row Settings shows: stage, agents, model, effort, where each came from.

        When `node` cannot run there is no stage list (a second copy of the loop would be
        wrong), so the table is empty and `problems` says why. `pr` and `ship` run no session.
        """
        try:
            stages = [s for s in await board_reader.stages() if s not in prmachine.STAGES]
        except Unavailable as e:
            return {"rows": [], "problems": [str(e)], "cos_model": self.config.model}
        overrides, bad_rows = self._model_overrides()
        efforts, bad_efforts = self._effort_overrides()
        defaults, bad_defaults = models.load_defaults()
        table = models.table(
            stages, overrides, efforts, defaults, self.config.model, SESSIONS_PER_STEP
        )
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
                journal.append(
                    {
                        "kind": "setting",
                        "workspace": "",
                        "unit": "",
                        "stage": "",
                        "name": key,
                        "old": old,
                        "new": new,
                    }
                )
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e

    async def set_stage_model(self, name: Any, model: Any = None) -> dict[str, Any]:
        """Set one row's model, or remove the override when `model` is None.

        Behind the password like every route: whoever holds it can move `review` to a weaker
        model. The one trace is the `setting` record, with the old and new value. No gate reads it.
        An unknown model fails at the next step of that stage, with the CLI's own error.
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
        """Set one row's effort, or remove the override when `effort` is None.

        Same exposure and trace as `set_stage_model`. `max` is accepted here and only here
        (`models.json` may not ship it), so every `max` run traces back to a `setting` record.
        `chat` has no effort.
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

    # -- the autopilot's settings ---------------------------------------------
    #
    # In the data root's `prefs`, not the workspace's repository. Three per workspace, keyed by
    # the journal key; the cap is one for the whole app, since the quota is the machine's account.
    # Not in `PREFERENCES`: those are the page's.

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
            "autopilot_may_ship": read(
                "autopilot_may_ship", lambda v: v is True or v is False, False
            ),
            "max_parallel": read(
                "max_parallel", _whole_at_least_one, autopilot.DEFAULT_MAX_PARALLEL
            ),
            "daily_cap_usd": float(
                read("daily_cap_usd", _positive_number, autopilot.DEFAULT_DAILY_CAP_USD)
            ),
        }

    def _off_loopback(self) -> str:
        """Why the autopilot may not run on this bind, or `""`."""
        if self.config.host in LOOPBACK:
            return ""
        # No variable name here: the page shows it verbatim.
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
        """Set one of the four. A wrong value is refused and nothing is written.

        Behind the password like every route: whoever holds it can turn the autopilot on,
        raise the cap, or let it ship. The trace is the `setting` record. Turning it on is
        refused while the app listens beyond loopback.
        """
        self._workspace_or_refuse(cwd)
        if name not in self.AUTOPILOT_SETTINGS:
            raise Invalid(
                f"no such setting: {name} (use one of {', '.join(self.AUTOPILOT_SETTINGS)})"
            )
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
