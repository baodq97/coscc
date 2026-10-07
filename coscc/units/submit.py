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
from typing import Any

from coscc.agent import pack
from coscc.units import contracts, guards

SERVER = "cos"
TOOL = "submit"
# The name the session's gate is asked with, and so what `Grant.mcp` holds.
NAME = f"mcp__{SERVER}__{TOOL}"

# With this sentence in the error sessions submit again after one more turn; without it, often not.
AGAIN = "Correct the object and call submit again."

# What a stage's `judgement` (`contracts.Judgement`) puts on its artifact.
JUDGEMENTS = {"ready": "accepted", "not-ready": "draft"}

# What a round's `verdict` (`contracts.Verdict`) puts on `review.md`. `needs-person` keeps it
# `changes-requested`: the unit is not finished, and the loop reads the round's verdict for the wait.
ROUND_STATES = {
    "pass": "accepted",
    "changes-requested": "changes-requested",
    "needs-person": "changes-requested",
}


def round_problem(obj: Mapping[str, Any]) -> str:
    """What a review round says that its schema cannot rule out, `""` when nothing: a criterion
    or an id given twice, a finding naming a criterion the round does not grade, `pass` while a
    criterion is `no`, or a `fixed` with no commit, which the loop would read as neither."""
    named = [c["criterion"] for c in obj.get("criteria") or ()]
    again = sorted({c for c in named if named.count(c) > 1})
    if again:
        return f"criterion {', '.join(again)} is graded more than once."
    unknown = sorted({f["id"] for f in obj.get("findings") or () if f["criterion"] not in named})
    if unknown:
        return (
            f"{', '.join(unknown)}: `criterion` must be one of the `criteria` you graded "
            f"({', '.join(named) or 'none'})."
        )
    failed = [c["criterion"] for c in obj.get("criteria") or () if c["met"] == "no"]
    if obj.get("verdict") == "pass" and failed:
        return f"`pass` while {', '.join(failed)} is `no`: a criterion not met blocks the merge."
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


def verdict_problem(obj: Mapping[str, Any]) -> str:
    """What a verdict says that its schema cannot rule out, `""` when nothing: no criterion, one
    named twice, or a `yes` or `no` whose evidence cites no `path:lines`."""
    criteria = list(obj.get("criteria") or ())
    if not criteria:
        return "grade at least one criterion."
    named = [c["criterion"] for c in criteria]
    again = sorted({c for c in named if named.count(c) > 1})
    if again:
        return f"criterion {', '.join(again)} is graded more than once."
    uncited = [
        c["criterion"]
        for c in criteria
        if c["met"] != "unclear" and not contracts.CITED.search(c["evidence"])
    ]
    if uncited:
        return (
            f"{', '.join(uncited)}: a `yes` or a `no` cites its evidence as `path:lines` "
            "(as `coscc/bus.py:12-30`); with none, say `unclear`."
        )
    return ""


# The most questions a draft asks before it drafts: the few whose answer changes the draft.
QUESTIONS_MAX = 3


def draft_problem(obj: Mapping[str, Any], catalog: Mapping[str, str] | None) -> str:
    """What a draft says that its schema cannot rule out, `""` when nothing: the load checks a
    person's save runs again. An `agent` is a whole new row (`pack.new_row_problems` with
    `catalog`: its key, every part, its input and output); a `process` passes `check_process`
    on every row and that agent, under a name the owner's pack does not hold yet. A draft that
    asks holds at most `QUESTIONS_MAX` questions, each with a recommendation, and nothing else
    yet: the person's answers come before the draft."""
    agent, process = obj.get("agent"), obj.get("process")
    questions = obj.get("questions") or []
    if questions:
        if len(questions) > QUESTIONS_MAX:
            return (
                f"ask at most {QUESTIONS_MAX} questions: the ones whose answer changes the draft."
            )
        if any(not str(q.get("recommendation") or "").strip() for q in questions):
            return "each question carries the answer you recommend."
        if agent is not None or process is not None:
            return "a draft that asks holds no `agent` or `process` yet: ask, or draft."
        return ""
    if agent is None and process is None:
        return "a draft holds an `agent`, a `process` or both, or the questions to ask first."
    rows = pack.plain_rows()
    out: list[str] = []
    if agent is not None:
        key, fields, body = agent.get("key"), agent.get("fields"), agent.get("body")
        if not isinstance(key, str) or not isinstance(fields, dict) or not isinstance(body, str):
            return "agent is {key, fields, body}: key and body text, fields an object."
        made = {**fields, "key": key, pack.BODY: body}
        try:
            out += [f"agent: {r}" for r in pack.new_row_problems(made, catalog)]
        except (TypeError, AttributeError, ValueError, KeyError) as e:
            out.append(f"agent: {type(e).__name__}: {e}")
        rows[key] = made
    if process is not None:
        name = process["name"]
        if why := pack.key_problem(name, "a process name"):
            out.append(f"process: {why}")
        elif f"{pack.LOCAL_NAME}/{name}" in pack.processes():
            out.append(f"process: {name} is taken: name it anew")
        try:
            out += [f"process: {r}" for r in pack.check_process(name, process["process"], rows)]
        except (TypeError, AttributeError, ValueError, KeyError) as e:
            out.append(f"process: {type(e).__name__}: {e}")
    return "; ".join(out) + ("." if out else "")


def plan_problem(obj: Mapping[str, Any]) -> str:
    """What a plan's steps say that its schema cannot rule out, `""` when nothing: a step naming
    a path its `files` do not, one path in two steps, or a path not as the repository names it."""
    files = set(obj.get("files") or ())
    for path in sorted(files):
        if path != path.strip() or path.startswith(("/", "./", "`")) or "`" in path or ":" in path:
            return f"`files` names {path!r}: give each path as the repository names it, as `coscc/bus.py`."
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
        open_ids: tuple[str, ...] = (),
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
        self.open_ids = tuple(open_ids)
        self.claims_round = claims_round
        # What the runner adds once the artifact is written, all of it the app's: a round's
        # number and where its screenshots were taken.
        self.extra: dict[str, Any] = {}
        self.schema = contracts.schema(stage)
        out = contracts.output(stage)
        # What the declaration is: a review's round, or an artifact's result, and the fields the
        # engine reads of it beyond the judgement.
        self.is_round = out["kind"] == "review"
        self.fields = {n.rstrip("?") for n in out["fields"]}
        self.received: dict[str, Any] | None = None
        self.refused = 0

    @property
    def guard_id(self) -> str:
        return "review-round" if self.is_round else "stage-result"

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
        if self.is_round:
            out["head"] = self.head
        if "needs_person" in self.fields:
            out.update(
                claims=list(obj.get("needs_person") or ()),
                open_ids=list(self.open_ids),
                claims_round=self.claims_round,
            )
        return {**out, **self.extra}

    def verdict(self, obj: Mapping[str, Any], revision_then: str) -> guards.Verdict:
        """This channel's guard, and for impl guard `impl-claim` once it opens."""
        inputs = self.inputs(obj, revision_then)
        verdict = guards.guard(self.guard_id).check(inputs)
        if verdict.open and "needs_person" in self.fields:
            return guards.guard("impl-claim").check(inputs)
        return verdict

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        """The handler. The schema has passed by the time this runs."""
        obj = dict(args or {})
        if self.is_round:
            problem = round_problem(obj)
            if problem:
                self.refused += 1
                return refusal(problem)
        elif obj.get("stage") != self.stage:
            self.refused += 1
            return refusal(f"this run is {self.stage}; the object names {obj.get('stage')!r}.")
        elif "steps" in self.fields and (problem := plan_problem(obj)):
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
                    f"round left open, and those are: {', '.join(self.open_ids) or 'none'}."
                )
            return refusal(
                f"guard {self.guard_id} refused: {', '.join(verdict.reasons)} "
                "(this run is not the one open for the unit, or its artifacts changed while it ran)."
            )
        self.received = {"object": obj, "revision": taken}
        said = obj["verdict"] if self.is_round else obj["judgement"]
        return {"content": [{"type": "text", "text": f"received: {self.artifact} {said}"}]}

    def description(self) -> str:
        if self.is_round:
            return (
                "Hand the app your review round: its verdict, every finding with its state, and "
                "every screenshot you opened. The app writes the round's verdict line, its "
                "### Findings and its ### Screens in review.md from this object. If it returns "
                f"an error, the app has checked your object against the unit: {AGAIN}"
            )
        return (
            f"Hand the app your judgement of {self.artifact}: whether it is ready, its open "
            "questions"
            + (", the U<n> ids under ## Concerns" if "unmeasured" in self.fields else "")
            + (", and a verdict per U<n>" if "verdicts" in self.fields else "")
            + (
                ", and the open findings only a person can close"
                if "needs_person" in self.fields
                else ""
            )
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
    """The `submit` of a session that is no stage: Gebo, an estimate, a triggered row.

    No artifact to hash and no unit run to match: it checks the schema (the SDK does that), a
    verdict's citations (`verdict_problem`) and a draft against the load checks with `catalog`
    (`draft_problem`), and keeps the last object handed in. What of it is written is the
    caller's to decide.
    """

    def __init__(self, kind: str, catalog: Mapping[str, str] | None = None):
        self.kind = self.stage = kind
        out = contracts.output(kind)
        self._what = out.get("purpose", "")
        self.is_verdict = out["kind"] == "verdict"
        self.is_draft = out["kind"] == "draft"
        self.catalog = catalog
        self.schema = contracts.schema(kind)
        self.received: dict[str, Any] | None = None

    def object(self) -> dict[str, Any] | None:
        return self.received["object"] if self.received is not None else None

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        obj = dict(args or {})
        if self.is_verdict and (problem := verdict_problem(obj)):
            return refusal(problem)
        if self.is_draft and (problem := draft_problem(obj, self.catalog)):
            return refusal(f"The draft would be refused: {problem}")
        self.received = {"object": obj}
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
