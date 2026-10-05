"""Which model and effort each stage runs on."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from coscc.units import autopilot
from coscc.units import board as board_reader
from coscc.store.db import Data
from coscc.store.journal import Journal
from coscc.agent import labels, models, modeltrial

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
    # the loop, the overrides from `prefs`, `COS_MODEL` from `Config`.

    def model_overrides(self) -> tuple[dict[str, models.Value], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def effort_overrides(self) -> tuple[dict[str, models.Value], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat. Never raises on bad data.

        A board step goes through `stage_config` instead, which also reads the label.
        """
        return self.config_for(name)[:2]

    def config_for(self, name: str) -> tuple[str | None, str, str | None, str]:
        """`(model, model_source, effort, effort_source)` of a row run with no label: Gebo's
        `integrate`, which falls back to `impl`'s base row (`models.FALLS_BACK`)."""
        defaults, _ = models.load_defaults()
        return models.resolve(
            name,
            None,
            self.model_overrides()[0],
            self.effort_overrides()[0],
            defaults,
            self.config.model,
        )

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
        """Whether `coscc.loop next` sends `unit` back to `impl` because CI is red, read with
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
