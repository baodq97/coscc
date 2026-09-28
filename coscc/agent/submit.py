"""The one tool a session hands its object back through: `submit`.

`.cos/0136_transitions-are-decided-by-parsing-prose` R2, R3. Every output of an agent that
drives a transition reaches the app as an object, not as prose the app parses. The channel is
an in-process tool registered through an SDK MCP server (`spike.md ## U1`): the SDK checks the
arguments against the tool's JSON Schema before the handler runs, the handler gets a dict in
this process before the session's `ResultMessage`, and `can_use_tool` is still asked first.

A `Channel` is bound to one run. Its handler checks what the schema cannot — that the object
came from the run the app has open, and that the artifacts it judged are still the revision the
app read (R3 a, b) — keeps the object it accepted, and says why it refused one otherwise. The
transition itself is applied by the runner through `coscc/units/transitions.py` once the step
has written its artifact, so a step that fails after submitting leaves no status behind.

The tool writes nothing to disk and runs nothing (spec C4): it is the one thing beyond reading
a prose stage's grant carries.
"""

from __future__ import annotations

import hashlib
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from coscc.units import guards

SERVER = "cos"
TOOL = "submit"
# What `can_use_tool` is asked with, and so what `policy.decide` lets through.
NAME = f"mcp__{SERVER}__{TOOL}"

# `spike.md ## U2`: with this sentence in the error 5/5 sessions submitted again after one more
# turn; with neither it nor the prompt saying so, 2/5 did.
AGAIN = "Correct the object and call submit again."

# R4. What a stage's `judgement` puts on its artifact. `rejected` and `done` are a person's, or
# a later machine's, and no agent chooses them.
JUDGEMENTS = {"ready": "accepted", "not-ready": "draft"}

# The stages whose run hands back a stage result (R4). `review` hands back a round and the
# integrate, precedent and estimate sessions their own objects; a stage not here opens no
# channel and ends as it did before.
# A set, not the loop's order, as `policy.SUBMITTING` is.
STAGE_RESULT = ("idea", "impl", "intent", "plan", "spec", "spike")

_U = {"type": "string", "pattern": "^U[0-9]+$"}
_F = {"type": "string", "pattern": "^F[0-9]+$"}
_QUESTIONS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 1}, "text": {"type": "string", "minLength": 1}},
        "required": ["n", "text"],
        "additionalProperties": False,
    },
}


def stage_result_schema(stage: str) -> dict[str, Any]:
    """R2's first kind. `unmeasured` belongs to `spec` alone and `verdicts` to `spike`."""
    properties: dict[str, Any] = {
        "stage": {"type": "string", "enum": [stage]},
        "judgement": {"type": "string", "enum": list(JUDGEMENTS)},
        "questions": _QUESTIONS,
    }
    required = ["stage", "judgement", "questions"]
    if stage == "spec":
        properties["unmeasured"] = {"type": "array", "items": _U}
        required.append("unmeasured")
    if stage == "spike":
        properties["verdicts"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": _U, "verdict": {"type": "string", "enum": ["holds", "fails"]}},
                "required": ["id", "verdict"],
                "additionalProperties": False,
            },
        }
        required.append("verdicts")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


# Three more kinds of R2, whose fields the spec's Design names, each wired by the step of
# `plan.md` that moves its place. Jera's and the estimate's come with theirs (step 12): their
# fields are what `precedent.py` and `backlog.py` check today, and are copied from there then.
SCHEMAS: dict[str, dict[str, Any]] = {
    "review-round": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "changes-requested", "needs-person"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": _F,
                        "state": {"type": "string", "enum": ["open", "fixed", "withdrawn"]},
                        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                        "rule": {"type": "string", "pattern": "^(S[0-9]+)?$"},
                        "path": {"type": "string"},
                        "lines": {"type": "string"},
                        "text": {"type": "string", "minLength": 1},
                    },
                    "required": ["id", "state", "severity", "rule", "path", "lines", "text"],
                    "additionalProperties": False,
                },
            },
            "screens": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["verdict", "findings", "screens"],
        "additionalProperties": False,
    },
    "impl-claim": {
        "type": "object",
        "properties": {"needs_person": {"type": "array", "items": _F}},
        "required": ["needs_person"],
        "additionalProperties": False,
    },
    "integrate-result": {
        "type": "object",
        "properties": {
            "needs_person": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"commit": {"type": "string"}, "why": {"type": "string", "minLength": 1}},
                    "required": ["commit", "why"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["needs_person"],
        "additionalProperties": False,
    },
}


def schema_for(stage: str) -> dict[str, Any] | None:
    """The schema a run of `stage` submits against, or `None` for a stage with no channel yet."""
    return stage_result_schema(stage) if stage in STAGE_RESULT else None


def revision(directory: str | Path, artifact: str, *, own: bool) -> str:
    """R3 b. One hash of the unit's artifacts as they are on disk now.

    Every `*.md` of the unit but `artifact`, and `artifact` too when `own` — when the session
    writes it itself (`impl`). A prose stage's artifact is written from its reply after the
    session, so what its object judged is the reply and the artifacts it was given, and the
    last are what this can check. Names and bytes both count, so a file added is a change.
    """
    h = hashlib.sha256()
    base = Path(directory)
    for path in sorted(base.glob("*.md")):
        if path.name == artifact and not own:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        h.update(path.name.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def refusal(what: str) -> dict[str, Any]:
    """An `is_error` result: the facts, then `AGAIN` (`spike.md ## U2`)."""
    return {"content": [{"type": "text", "text": f"{what} {AGAIN}"}], "is_error": True}


# Each channel by the server it made, so `Channel.of` finds it again: a stand-in session in a
# test calls the handler through it as the SDK would. Weak, so a finished run keeps nothing.
_CHANNELS: weakref.WeakKeyDictionary[Any, Channel] = weakref.WeakKeyDictionary()


class Channel:
    """The `submit` of one run. `received` is the last object it accepted, with the inputs its
    guard read; a later accepted object replaces it, so the model's last word counts.
    """

    def __init__(
        self,
        *,
        run: str,
        stage: str,
        directory: str | Path,
        artifact: str,
        own: bool,
        open_run: Callable[[], str] | None = None,
    ):
        self.run = run
        self.stage = stage
        self.directory = Path(directory)
        self.artifact = artifact
        self.own = own
        # Who the app has open for this unit and stage now. By default this very run.
        self._open_run = open_run or (lambda: run)
        self.schema = schema_for(stage) or {"type": "object"}
        self.received: dict[str, Any] | None = None
        self.refused = 0

    def inputs(self, obj: Mapping[str, Any], revision_then: str) -> dict[str, Any]:
        """What guard `stage-result` reads, the app's own hash taken again now beside it."""
        return {
            "run": self.run,
            "open_run": self._open_run(),
            "revision": revision_then,
            "computed_revision": revision(self.directory, self.artifact, own=self.own),
            "stage": self.stage,
            "object": dict(obj),
        }

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        """The handler. The schema has passed by the time this runs (`spike.md ## U1`)."""
        obj = dict(args or {})
        if obj.get("stage") != self.stage:
            self.refused += 1
            return refusal(f"this run is {self.stage}; the object names {obj.get('stage')!r}.")
        taken = revision(self.directory, self.artifact, own=self.own)
        if self.own and not (self.directory / self.artifact).exists():
            self.refused += 1
            return refusal(f"{self.artifact} is not written yet; write it first, then submit what it says.")
        verdict = guards.stage_result(self.inputs(obj, taken))
        if not verdict.open:
            self.refused += 1
            return refusal(
                f"guard stage-result refused: {', '.join(verdict.reasons)} "
                "(this run is not the one open for the unit, or its artifacts changed while it ran)."
            )
        self.received = {"object": obj, "revision": taken}
        return {"content": [{"type": "text", "text": f"received: {self.artifact} {obj['judgement']}"}]}

    def description(self) -> str:
        return (
            f"Hand the app your judgement of {self.artifact}: whether it is ready, its open "
            "questions" + (", the U<n> ids under ## Concerns" if self.stage == "spec" else "")
            + (", and a verdict per U<n>" if self.stage == "spike" else "")
            + ". Call it once the artifact is final. If it returns an error, the app has checked "
            f"your object against the unit: {AGAIN}"
        )

    def server(self) -> Any:
        """The SDK MCP server carrying this channel's one tool, for `mcp_servers`."""
        from claude_agent_sdk import create_sdk_mcp_server, tool

        config = create_sdk_mcp_server(SERVER, "1.0.0", [tool(TOOL, self.description(), self.schema)(self.handle)])
        _CHANNELS[config["instance"]] = self
        return config

    @staticmethod
    def of(config: Mapping[str, Any]) -> Channel | None:
        """The channel whose `server()` gave `config`, if it is still alive."""
        instance = config.get("instance") if isinstance(config, Mapping) else None
        return _CHANNELS.get(instance) if instance is not None else None
