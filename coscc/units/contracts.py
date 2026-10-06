"""What each agent hands back through `submit`: its output declaration, checked, and the schema
generated from it.

A declaration is `{kind, version, fields}` (and `purpose`, what `submit` says it is for): an agent
row's `output` (`coscc/agent/pack.py`) of a kind `submit` takes, a feature session's
`kernel.Session.output` (`add`). Field
types: `text`, `number` (an integer), `{"enum": [...]}`, `{"list": <type>}`, an object
`{name: type}`, and any other string as a pattern, anchored. A name ending in `?` may be left
out. `READS` names every field the engine decides on, with the reader and the type it expects:
a declaration that lacks one, or gives it another type, refuses the load with a named reason.
The declarations are read and checked when the rows change, and when the app is built.
A row's `input` is what its stage is handed (`check_input`); the prompt is built from it alone.
"""

import hashlib
import json
import re
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Any, Literal, NotRequired, TypedDict, get_args

from coscc.agent import pack
from coscc.agent.policy import Label
from coscc.units.states import STAGE_NAMES

# What a stage's `judgement` says of its artifact. `rejected` is a machine's, and no agent
# chooses it.
Judgement = Literal["ready", "not-ready"]
# What a review round's `verdict` says.
Verdict = Literal["pass", "changes-requested", "needs-person"]
# The labels a finding may carry, the loop's own: `open`, `fixed` in a commit, or what the
# review made of impl's claim or of a person's answer.
FindingState = Literal["open", "fixed", "needs-person", "claim-rejected", "answered"]
Severity = Literal["high", "medium", "low"]
SpikeVerdict = Literal["holds", "fails"]
# A unit's branch type: the intent's `type`; the loop's `BRANCH_TYPES` is this list.
BranchType = Literal[
    "feat", "fix", "docs", "refactor", "test", "chore", "perf", "build", "ci", "revert"
]
# `fix.expected.source`: a path, and the lines cited when there are some. What it may name (no
# absolute path, no `..`, not `.cos`) is the loop's rule, not the schema's.
SOURCE = "[^\\s:]+(:[0-9]+-[0-9]+)?"

# `artifact`: a stage's judgement of its file, one `outputs` row; `review`: a round, kept in
# its own rows; `session`: the object handed to the code that opened the session.
Kind = Literal["artifact", "review", "session"]
KINDS: tuple[Kind, ...] = get_args(Kind)

# The field the engine adds to an artifact's object: who sent it.
SENDER = "stage"

# A word or a pattern, `{"enum": [words]}`, `{"list": type}`, or an object `{name: type}`.
type FieldType = str | list[str] | dict[str, FieldType]


class Output(TypedDict):
    kind: Kind
    version: int
    fields: dict[str, FieldType]
    # What the `submit` tool tells a session that is no stage it is for; "" for a stage.
    purpose: NotRequired[str]


class PlanStep(TypedDict):
    """One parallel step of a plan: a helper's title, the paths it alone edits, what it reports."""

    title: str
    paths: list[str]
    report: str


class Plan(TypedDict):
    """What the app reads of a plan's record: its label, its files, its parallel steps and the
    spike items it rests on."""

    impl: Label
    files: list[str]
    steps: list[PlanStep]
    rests_on: list[str]


class ContractError(Exception):
    """A declaration or a stored record the engine cannot use: `<code>: <words>`."""

    def __init__(self, code: str, words: str):
        super().__init__(f"{code}: {words}")
        self.code = code
        self.words = words


_U = "U[0-9]+"
_F = "F[0-9]+"


def _enum(literal: object) -> FieldType:
    return {"enum": list(get_args(literal))}


# Every field the engine decides on, by kind or by agent: `{field: (reader, type)}`. A reader
# is a guard of `coscc/units/guards.py` or the function that reads the field. An object type
# names only the fields the reader reads; the declaration may hold more. A name ending in `?`
# is read when present: the declaration must still hold it, as an optional field.
READS: dict[str, dict[str, tuple[str, FieldType]]] = {
    "artifact": {
        "judgement": ("stage-result", _enum(Judgement)),
        "questions": (
            "awaits-person",
            {"list": {"n": "number", "text": "text", "recommendation": "text"}},
        ),
    },
    "review": {
        "verdict": ("review-round", _enum(Verdict)),
        "findings": (
            "review-round",
            {
                "list": {
                    "id": _F,
                    "state": _enum(FindingState),
                    "fixed_in": "([0-9a-f]{7,40})?",
                    "severity": _enum(Severity),
                    "rule": "(S[0-9]+)?",
                    "path": "text",
                    "lines": "text",
                    "text": "text",
                }
            },
        ),
        "screens": (
            "review-round",
            {
                "list": {
                    "path": ".*\\.png",
                    "size": "[0-9]+x[0-9]+",
                    "address": "text",
                    "result": "text",
                }
            },
        ),
    },
    "spec": {"unmeasured": ("spike-holds", {"list": _U})},
    "spike": {"verdicts": ("spike-holds", {"list": {"id": _U, "verdict": _enum(SpikeVerdict)}})},
    "intent": {
        "type": ("branch_for", _enum(BranchType)),
        "fix?": (
            "lane_of",
            {
                "reproduction": "text",
                "expected": {"source": SOURCE, "text": "text"},
                "actual": "text",
            },
        ),
    },
    "plan": {
        "impl": ("label_of", _enum(Label)),
        "files": ("label_of", {"list": "text"}),
        "steps": (
            "render",
            {"list": {"title": "text", "paths": {"list": "text"}, "report": "text"}},
        ),
        "rests_on": ("evaluate", {"list": _U}),
    },
    "impl": {"needs_person": ("impl-claim", {"list": _F}), "left_lane?": ("lane_of", "text")},
    "integrate": {
        "needs_person": ("outcome_of_session", {"list": {"commit": "text", "why": "text"}})
    },
    "estimate": {
        "units": (
            "parse_proposal",
            {
                "list": {
                    "unit": "text",
                    "value": "number",
                    "effort": "text",
                    "similar": {"list": "text"},
                    "basis": "text",
                    "relations": {"list": {"type": "text", "other": "text", "reason": "text"}},
                }
            },
        )
    },
}

_WORDS = ("text", "number")
_NAME = re.compile(r"[a-z][a-z0-9_]*\??")
_RESERVED = ("list", "enum")


def _bad(where: str, why: str) -> ContractError:
    return ContractError("contract-bad-type", f"{where}: {why}")


def _check_type(where: str, t: object) -> None:
    if isinstance(t, str):
        if t in _WORDS:
            return
        if re.fullmatch(r"[a-z]+", t):
            raise _bad(
                where, f"{t!r} is no type (text, number, enum, list, an object or a pattern)"
            )
        try:
            re.compile(t)
        except re.error as e:
            raise _bad(where, f"{t!r} is no pattern: {e}") from None
        return
    if not isinstance(t, dict) or not t:
        raise _bad(where, f"{json.dumps(t)} is no type")
    if set(t) == {"enum"}:
        words = t["enum"]
        if not isinstance(words, list) or not words or not all(isinstance(w, str) for w in words):
            raise _bad(where, "an enum is a list of one or more words")
        return
    if set(t) == {"list"}:
        _check_type(where, t["list"])
        return
    for name, sub in t.items():
        _check_name(f"{where}.{name}", name)
        _check_type(f"{where}.{name.rstrip('?')}", sub)


def _check_name(where: str, name: object) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise _bad(where, "a field's name is lowercase letters, digits and _, then `?` if optional")
    if name.rstrip("?") in _RESERVED:
        raise _bad(where, f"no field is named {name.rstrip('?')!r}")


def _required(fields: dict[str, FieldType]) -> dict[str, FieldType]:
    return {name: t for name, t in fields.items() if not name.endswith("?")}


def _shape(t: FieldType) -> str:
    """`word`, `enum`, `list` or `object`."""
    if not isinstance(t, dict):
        return "word"
    return next((s for s in ("enum", "list") if set(t) == {s}), "object")


def _covers(where: str, reader: str, got: FieldType, want: FieldType) -> None:
    """`got`, declared, gives what `want`, read, expects: an object at least the fields read,
    each required; anything else the very same type."""
    shape = _shape(want)
    if isinstance(got, dict) and isinstance(want, dict) and shape == _shape(got) == "object":
        have = _required(got)
        for name, sub in want.items():
            if name not in have:
                raise ContractError("contract-field-missing", f"{where}.{name} (read by {reader})")
            _covers(f"{where}.{name}", reader, have[name], sub)
        return
    if isinstance(got, dict) and isinstance(want, dict) and shape == _shape(got) == "list":
        _covers(where, reader, got["list"], want["list"])
        return
    if got != want:
        raise _bad(where, f"declared {json.dumps(got)}, {reader} reads {json.dumps(want)}")


def check(agent: str, output: object) -> Output:
    """`output` as `agent`'s declaration, or a `ContractError` naming what is wrong."""
    if not isinstance(output, dict):
        raise _bad(agent, "an output is {kind, version, fields}")
    kind, version, fields = output.get("kind"), output.get("version"), output.get("fields")
    if kind not in KINDS:
        raise _bad(f"{agent}.kind", f"{kind!r} is no kind ({', '.join(KINDS)})")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _bad(f"{agent}.version", f"{version!r} is not a whole number from 1")
    if not isinstance(fields, dict):
        raise _bad(f"{agent}.fields", "the fields are an object {name: type}")
    if kind == "artifact" and SENDER in {str(n).rstrip("?") for n in fields}:
        raise _bad(f"{agent}.{SENDER}", "the engine adds who sent an artifact's object")
    for name, t in fields.items():
        _check_name(f"{agent}.{name}", name)
        _check_type(f"{agent}.{name.rstrip('?')}", t)
    have = _required(fields)
    for scope in dict.fromkeys((kind, agent)):
        for field, (reader, want) in READS.get(scope, {}).items():
            if field not in (fields if field.endswith("?") else have):
                raise ContractError("contract-field-missing", f"{agent}.{field} (read by {reader})")
            _covers(f"{agent}.{field.rstrip('?')}", reader, fields[field], want)
    return Output(
        kind=kind, version=version, fields=fields, purpose=str(output.get("purpose") or "")
    )


def load(rows: Mapping[str, Mapping[str, object]]) -> dict[str, Output]:
    """Every declaration of the rows: each `output` of a kind `submit` takes, checked."""
    out: dict[str, Output] = {}
    for agent, row in rows.items():
        output = row.get("output")
        if isinstance(output, dict) and output.get("kind") in KINDS:
            out[agent] = check(agent, output)
    return out


class Input(TypedDict):
    """What a stage is handed, and nothing else: earlier artifacts whole (`name?` when it may be
    absent), earlier stages' records, the unit's answers and open findings, the app's data."""

    artifacts: list[str]
    outputs: list[str]
    answers: bool
    findings: bool
    data: list[str]


# The app's data a stage may declare: the shared idea, the sibling checkouts, the units this one
# names, the plan's files as they stand, the files `main` changed since the plan, the last
# integration and the screenshots taken again.
DATA = ("idea", "siblings", "mentions", "plan-map", "drift", "integration", "screens")


def check_input(agent: str, raw: object, agents: Iterable[str]) -> Input:
    """`raw` as `agent`'s input declaration, or a `ContractError` naming what is wrong. An
    artifact names a stage (`states.STAGE_NAMES`), an output an agent of `agents`, either with
    `?` when it may be missing."""
    keys = set(Input.__annotations__)
    if not isinstance(raw, dict) or set(raw) != keys:
        raise _bad(f"{agent}.input", f"an input is {{{', '.join(sorted(keys))}}}")
    for part, known in (("artifacts", set(STAGE_NAMES)), ("outputs", set(agents))):
        names = raw[part]
        if not isinstance(names, list) or not all(
            isinstance(n, str) and n.rstrip("?") in known for n in names
        ):
            raise _bad(f"{agent}.input.{part}", f"{names!r} names none of {sorted(known)}")
    for part in ("answers", "findings"):
        if not isinstance(raw[part], bool):
            raise _bad(f"{agent}.input.{part}", "true or false")
    if not isinstance(raw["data"], list) or not set(raw["data"]) <= set(DATA):
        raise _bad(f"{agent}.input.data", f"{raw['data']!r} is not among {', '.join(DATA)}")
    return Input(
        artifacts=raw["artifacts"],
        outputs=raw["outputs"],
        answers=raw["answers"],
        findings=raw["findings"],
        data=raw["data"],
    )


def load_inputs(rows: Mapping[str, Mapping[str, object]]) -> dict[str, Input]:
    """Every row's `input`, checked; a row with none declares none."""
    return {a: check_input(a, row["input"], rows) for a, row in rows.items() if "input" in row}


# The rows the declarations were last read from, and what was read: read again when they change.
_READ: list[tuple[object, dict[str, Output], dict[str, Input]]] = []


def _read() -> tuple[dict[str, Output], dict[str, Input]]:
    rows = pack.rows()
    if not _READ or _READ[0][0] is not rows:
        _READ[:] = [(rows, load(rows), load_inputs(rows))]
    return _READ[0][1], _READ[0][2]


def _inputs() -> dict[str, Input]:
    return _read()[1]


def input_of(agent: str) -> Input:
    """What `agent` declares it is handed; nothing of the unit for an agent that declares none."""
    return _inputs().get(agent) or Input(
        artifacts=[], outputs=[], answers=False, findings=False, data=[]
    )


def artifact_text(directory: Path, name: str) -> str:
    """A declared artifact (`name` or `name?`) as it stands, `""` when the unit has none."""
    try:
        return (directory / f"{name.rstrip('?')}.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def record(unit_meta: Mapping[str, Any] | None, agent: str) -> Mapping[str, Any] | None:
    """The last record `agent` handed back through `submit`, as the unit's snapshot entry
    carries it; `None` when it handed none."""
    arts = (unit_meta or {}).get("artifacts") or {}
    return (arts.get(f"{agent.rstrip('?')}.md") or {}).get("result") or None


def missing(agent: str, directory: Path, unit_meta: Mapping[str, Any] | None) -> list[str]:
    """What `agent` declares it needs and the unit lacks: an artifact with no text, an earlier
    stage's record never handed back. `[]` when its step may be composed; anything else refuses
    it before spend (`input-missing`)."""
    declared = input_of(agent)
    required = [n for n in declared["artifacts"] if not n.endswith("?")]
    out = [f"{n}.md" for n in required if not artifact_text(directory, n).strip()]
    outputs = [n for n in declared["outputs"] if not n.endswith("?")]
    return out + [f"{n}'s record" for n in outputs if record(unit_meta, n) is None]


# A feature's sessions, added when the app is built.
ADDED: dict[str, Output] = {}


def declarations() -> dict[str, Output]:
    """Every declaration this process knows: the rows' and the features'."""
    return {**_read()[0], **ADDED}


def add(kind: str, output: object) -> None:
    """A feature's session (`kernel.Session`); adding the same one again changes nothing, and
    taking a name another declaration holds is a `ValueError`."""
    checked = check(kind, output)
    if declarations().get(kind, checked) != checked:
        raise ValueError(f"the output {kind!r} is taken")
    ADDED[kind] = checked


def output(agent: str) -> Output:
    found = declarations().get(agent)
    if found is None:
        raise ContractError("contract-field-missing", f"{agent}.output (read by submit)")
    return found


def version(agent: str) -> int:
    return output(agent)["version"]


def check_stored(agent: str, stored: int) -> None:
    """A record written against another version than the declaration's is never read."""
    declared = version(agent)
    if stored != declared:
        raise ContractError("output-version", f"{agent} stored v{stored}, declared v{declared}")


def reads(agent: str, obj: dict[str, object]) -> dict[str, object]:
    """The fields of `obj` the engine decides on, and no other."""
    read = {**READS.get(output(agent)["kind"], {}), **READS.get(agent, {})}
    return {k: v for k, v in obj.items() if k in read or f"{k}?" in read}


def fingerprint(out: Output) -> str:
    """A short hash of a declaration's kind and fields, which its version is pinned beside."""
    said = json.dumps({"kind": out["kind"], "fields": out["fields"]}, sort_keys=True)
    return hashlib.sha256(said.encode()).hexdigest()[:12]


def _schema(t: FieldType) -> dict[str, object]:
    if t == "text":
        return {"type": "string"}
    if t == "number":
        return {"type": "integer"}
    if isinstance(t, str):
        return {"type": "string", "pattern": f"^{t}$"}
    if isinstance(t, list):
        return {"type": "string", "enum": list(t)}
    if _shape(t) == "enum":
        return _schema(t["enum"])
    if _shape(t) == "list":
        return {"type": "array", "items": _schema(t["list"])}
    return _object(t, {})


def _object(fields: dict[str, FieldType], first: dict[str, dict[str, object]]) -> dict[str, object]:
    properties = {**first, **{n.rstrip("?"): _schema(t) for n, t in fields.items()}}
    return {
        "type": "object",
        "properties": properties,
        "required": [*first, *_required(fields)],
        "additionalProperties": False,
    }


def schema_of(agent: str, out: Output) -> dict[str, object]:
    """The JSON Schema `submit` checks `agent`'s object against; an artifact's names its sender."""
    first = {SENDER: {"type": "string", "enum": [agent]}} if out["kind"] == "artifact" else {}
    return _object(out["fields"], first)


def schema(agent: str) -> dict[str, object]:
    return schema_of(agent, output(agent))
