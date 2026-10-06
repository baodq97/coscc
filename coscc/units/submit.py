"""The one tool a session hands its object back through: `submit`.

An in-process tool registered through an SDK MCP server: the SDK checks the arguments against
the tool's JSON Schema before the handler runs, the handler gets a dict before the session's
`ResultMessage`, and the session's gate is still asked first.

A `Channel` is bound to one run. Its handler checks what the schema cannot (the object came
from the run the app has open; the artifacts it judged are still the revision the app read),
keeps the object it accepted, and says why it refused one otherwise. The runner applies the
transition through `coscc/units/transitions.py` once the artifact is written. The tool writes
nothing and runs nothing.
"""

import hashlib
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, get_args

from coscc.units import contracts, guards

SERVER = "cos"
TOOL = "submit"
# The name the session's gate is asked with, and so what `policy.allowed_mcp` holds.
NAME = f"mcp__{SERVER}__{TOOL}"

# With this sentence in the error sessions submit again after one more turn; without it, often not.
AGAIN = "Correct the object and call submit again."

# What a stage's `judgement` (`contracts.Judgement`) puts on its artifact.
JUDGEMENTS = {"ready": "accepted", "not-ready": "draft"}

# The stages whose run hands back a stage result. `review` hands back a round and the
# integrate and estimate sessions their own objects. A set, as `policy.SUBMITTING` is.
ResultStage = Literal["idea", "impl", "intent", "plan", "spec", "spike"]
STAGE_RESULT: tuple[ResultStage, ...] = get_args(ResultStage)
ROUND = "review"

# What a round's `verdict` (`contracts.Verdict`) puts on `review.md`. `needs-person` keeps it
# `changes-requested`: the unit is not finished, and the loop reads the round's verdict for the wait.
ROUND_STATES = {
    "pass": "accepted",
    "changes-requested": "changes-requested",
    "needs-person": "changes-requested",
}

# The sessions that are no stage and hand back an object, each by its grant's name, with what
# its tool says it is for; the schema is generated from its declaration (`contracts`). A
# feature adds its own (`add_session`). The estimate's fields are checked by
# `backlog.parse_proposal`: the declaration holds types, the app its rules.
SESSIONS: dict[str, str] = {
    "estimate": "Hand the app your estimate of every backlog unit, with the relations you propose.",
    "integrate": "Hand the app the commits only a person can settle, each with why; `[]` when there is none.",
}


def add_session(kind: str, output: object, purpose: str) -> None:
    """A feature's session (`kernel.Session`), added when the app is built: its declaration is
    checked (`ContractError`), and taking a name another holds is a `ValueError`."""
    contracts.add(kind, output)
    SESSIONS[kind] = purpose


def round_problem(obj: Mapping[str, Any]) -> str:
    """What a review round says that its schema cannot rule out, `""` when nothing: an id
    given twice, or a `fixed` with no commit, which the loop would read as neither."""
    ids = [f["id"] for f in obj.get("findings") or ()]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        return f"{', '.join(twice)} is listed more than once."
    unfixed = [
        f["id"] for f in obj.get("findings") or () if (f["state"] == "fixed") != bool(f["fixed_in"])
    ]
    if unfixed:
        return f"{', '.join(unfixed)}: `fixed_in` names the commit of a `fixed` finding, and is empty for any other state."
    return ""


def plan_problem(obj: Mapping[str, Any]) -> str:
    """What a plan's steps say that its schema cannot rule out, `""` when nothing: a step naming
    a path its `files` do not, or one path in two steps."""
    files = set(obj.get("files") or ())
    seen: dict[str, str] = {}
    for step in obj.get("steps") or ():
        for path in step["paths"]:
            if path not in files:
                return f"step {step['title']!r} names {path}, which `files` does not list."
            if path in seen:
                return f"{path} is in two steps, {seen[path]!r} and {step['title']!r}."
            seen[path] = step["title"]
    return ""


def revision(directory: str | Path, artifact: str, *, own: bool) -> str:
    """One hash of the unit's artifacts as they are on disk now.

    Every `*.md` of the unit but `artifact`, and `artifact` too when `own` (the session writes
    it itself, `impl`). A prose stage's artifact is written from its reply after the session,
    so only the artifacts it was given can be checked. Names and bytes both count.
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
    """An `is_error` result: the facts, then `AGAIN`."""
    return {"content": [{"type": "text", "text": f"{what} {AGAIN}"}], "is_error": True}


# Each channel by the server it made, so `Channel.of` finds it again (a test stand-in calls
# the handler through it). Weak, so a finished run keeps nothing.
_CHANNELS: weakref.WeakKeyDictionary[Any, Channel | Collector] = weakref.WeakKeyDictionary()


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
        head: str = "",
        open_findings: tuple[str, ...] = (),
        claims_round: int | None = None,
    ):
        self.run = run
        self.stage = stage
        self.directory = Path(directory)
        self.artifact = artifact
        self.own = own
        # Who the app has open for this unit and stage now. By default this very run.
        self._open_run = open_run or (lambda: run)
        # The head the app read when the run opened, the one a review round is of.
        self.head = head
        # The `F<k>` the last round left `open`, as the board read them before the step, and
        # that round's number: what an impl may claim only a person can close.
        self.open_findings = tuple(open_findings)
        self.claims_round = claims_round
        # What the runner adds once the artifact is written, all of it the app's: a round's
        # number and where its screenshots were taken.
        self.extra: dict[str, Any] = {}
        self.schema = contracts.schema(stage)
        self.received: dict[str, Any] | None = None
        self.refused = 0

    @property
    def guard_id(self) -> str:
        return "review-round" if self.stage == ROUND else "stage-result"

    def inputs(self, obj: Mapping[str, Any], revision_then: str) -> dict[str, Any]:
        """What this channel's guard reads, the app's own hash taken again now beside it."""
        out: dict[str, Any] = {
            "run": self.run,
            "open_run": self._open_run(),
            "revision": revision_then,
            "computed_revision": revision(self.directory, self.artifact, own=self.own),
            "stage": self.stage,
            "object": dict(obj),
        }
        if self.stage == ROUND:
            out["head"] = self.head
        if self.stage == "impl":
            out.update(
                claims=list(obj.get("needs_person") or ()),
                open_findings=list(self.open_findings),
                claims_round=self.claims_round,
            )
        return {**out, **self.extra}

    def verdict(self, obj: Mapping[str, Any], revision_then: str) -> guards.Verdict:
        """This channel's guard, and for impl guard `impl-claim` once it opens."""
        inputs = self.inputs(obj, revision_then)
        verdict = guards.guard(self.guard_id).check(inputs)
        if verdict.open and self.stage == "impl":
            return guards.guard("impl-claim").check(inputs)
        return verdict

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        """The handler. The schema has passed by the time this runs."""
        obj = dict(args or {})
        if self.stage == ROUND:
            problem = round_problem(obj)
            if problem:
                self.refused += 1
                return refusal(problem)
        elif obj.get("stage") != self.stage:
            self.refused += 1
            return refusal(f"this run is {self.stage}; the object names {obj.get('stage')!r}.")
        elif self.stage == "plan" and (problem := plan_problem(obj)):
            self.refused += 1
            return refusal(problem)
        taken = revision(self.directory, self.artifact, own=self.own)
        if self.own and not (self.directory / self.artifact).exists():
            self.refused += 1
            return refusal(
                f"{self.artifact} is not written yet; write it first, then submit what it says."
            )
        verdict = self.verdict(obj, taken)
        if not verdict.open:
            self.refused += 1
            if "not-open-finding" in verdict.reasons:
                return refusal(
                    "guard impl-claim refused: `needs_person` may name only a finding the last review "
                    f"round left open, and those are: {', '.join(self.open_findings) or 'none'}."
                )
            return refusal(
                f"guard {self.guard_id} refused: {', '.join(verdict.reasons)} "
                "(this run is not the one open for the unit, or its artifacts changed while it ran)."
            )
        self.received = {"object": obj, "revision": taken}
        said = obj["verdict"] if self.stage == ROUND else obj["judgement"]
        return {"content": [{"type": "text", "text": f"received: {self.artifact} {said}"}]}

    def description(self) -> str:
        if self.stage == ROUND:
            return (
                "Hand the app your review round: its verdict, every finding with its state, and "
                "every screenshot you opened. The app writes the round's verdict line, its "
                "### Findings and its ### Screens in review.md from this object. If it returns "
                f"an error, the app has checked your object against the unit: {AGAIN}"
            )
        return (
            f"Hand the app your judgement of {self.artifact}: whether it is ready, its open "
            "questions"
            + (", the U<n> ids under ## Concerns" if self.stage == "spec" else "")
            + (", and a verdict per U<n>" if self.stage == "spike" else "")
            + (", and the open findings only a person can close" if self.stage == "impl" else "")
            + ". Call it once the artifact is final. If it returns an error, the app has checked "
            f"your object against the unit: {AGAIN}"
        )

    def server(self, *extra: Any) -> Any:
        """The SDK MCP server carrying this channel's tool, for `mcp_servers`, and `extra`: the
        kernel's other `cos` tools (`coscc/agent/helpers.py`'s `peers`)."""
        return _serve(self, extra)

    @staticmethod
    def of(config: Mapping[str, Any]) -> Channel | Collector | None:
        """The channel whose `server()` gave `config`, if it is still alive."""
        instance = config.get("instance") if isinstance(config, Mapping) else None
        return _CHANNELS.get(instance) if instance is not None else None


class Collector:
    """The `submit` of a session that is no stage: Gebo, an estimate.

    No artifact to hash and no unit run to match: it checks the schema alone (the SDK does
    that) and keeps the last object handed in. What of it is written is the caller's to decide.
    """

    def __init__(self, kind: str):
        self.kind = self.stage = kind
        self._what = SESSIONS[kind]
        self.schema = contracts.schema(kind)
        self.received: dict[str, Any] | None = None

    def object(self) -> dict[str, Any] | None:
        return self.received["object"] if self.received is not None else None

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        self.received = {"object": dict(args or {})}
        return {"content": [{"type": "text", "text": f"received: {self.kind}"}]}

    def description(self) -> str:
        return (
            f"{self._what} Call it once, when you are done; a later call replaces the earlier "
            f"object. If it returns an error, the object did not fit its schema: {AGAIN}"
        )

    def server(self) -> Any:
        return _serve(self)


# The guard that decides whether a session that is no stage ends `done`: it handed back an
# object. Its id goes on that session's `end` row.
RUN_SUBMITTED = "run-submitted"


def submitted(collector: Collector) -> bool:
    """Whether guard `run-submitted` opens on what `collector` received."""
    return guards.guard(RUN_SUBMITTED).check({"submitted": collector.object() is not None}).open


def _serve(channel: Channel | Collector, extra: tuple[Any, ...] = ()) -> Any:
    from claude_agent_sdk import create_sdk_mcp_server, tool

    config = create_sdk_mcp_server(
        SERVER,
        "1.0.0",
        [tool(TOOL, channel.description(), channel.schema)(channel.handle), *extra],
    )
    _CHANNELS[config["instance"]] = channel
    return config
