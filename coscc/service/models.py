"""Which model and effort each stage runs on."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from coscc.units import autopilot
from coscc.units import board as board_reader
from coscc.units.board import Unavailable
from coscc.data import Data
from coscc.runlog.journal import Journal
from coscc.agent import labels, models, modeltrial
from coscc.github import prmachine
from coscc.runner import SESSIONS_PER_STEP
from coscc.service.common import Invalid, log_setting

from coscc.config import Config

from coscc.service.workspaces import Workspaces

log = logging.getLogger(__name__)


class Models:
    def __init__(self, config: Config, ws: Workspaces) -> None:
        self.config = config
        self.ws = ws

    # -- which model each stage runs on --------------------------------------
    #
    # The resolving is `coscc/agent/models.py`; this gathers its inputs: the stage list from
    # `cos.mjs`, the overrides from `prefs`, `COS_MODEL` from `Config`.

    def model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def effort_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat, and for Gebo on `impl`'s base row. Never raises on bad data.

        A board step goes through `_stage_config` instead, which also reads the label.
        """
        overrides, _ = self.model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, None, overrides, {}, defaults, self.config.model)[:2]

    def stage_config(
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
            self.model_overrides()[0],
            self.effort_overrides()[0],
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

    async def ci_red(self, cwd: str, unit: str, repo: str) -> bool | None:
        """Whether `cos.mjs next` sends `unit` back to `impl` because CI is red, read with
        `autopilot.is_ci_red`; `None` when it could not be asked. Never raises."""
        try:
            found = await board_reader.next_step(
                self.ws.units_root(cwd), unit, repo=repo, state=self.ws.snapshot(cwd, [unit])
            )
            return autopilot.is_ci_red(found)
        except Exception:
            # Recorded as null.
            log.exception("whether the CI of %s is red could not be read", unit)
            return None

    async def findings_added(self, cwd: str, unit: str, before: set[Any]) -> dict[str, Any]:
        """The findings in the rounds a `review` step added, off the board (`parseReview`'s
        count, read the way `post_new_rounds` reads it), and those rounds' verdicts."""
        data = await board_reader.read(self.ws.units_root(cwd), state=self.ws.snapshot(cwd))
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
        overrides, bad_rows = self.model_overrides()
        efforts, bad_efforts = self.effort_overrides()
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
        old = self.model_overrides()[0].get(name)
        if model is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, model)
        log_setting(self.ws.journal(), key, old, model)
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
        old = self.effort_overrides()[0].get(name)
        if effort is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, effort)
        log_setting(self.ws.journal(), key, old, effort)
        return await self.stage_models()
