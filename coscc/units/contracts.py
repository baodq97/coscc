"""What each agent hands back through `submit`: its output declaration, checked, and the schema
generated from it.

A declaration is `{kind, version, fields}` (and `purpose`, what `submit` says it is for): an agent
row's `output` (`coscc/agent/pack.py`) of a kind `submit` takes. Field
types: `text`, `number` (an integer), `json` (any object, its shape another check's),
`{"enum": [...]}`, `{"list": <type>}`, an object `{name: type}`, and any other string as a
pattern, anchored. A name ending in `?` may be left
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

# What a stage's `judgement` says of its artifact. `rejected` is a machine's, and no agent
# chooses it.
Judgement = Literal["ready", "not-ready"]
# What a review round's `verdict` says.
Verdict = Literal["pass", "changes-requested", "needs-person"]
# What a graded criterion comes to: met, not met, or not enough evidence to say.
Met = Literal["yes", "no", "unclear"]
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
# its own rows; `session`: the object handed to the code that opened the session; `proposal`:
# work proposed for the Backlog (`coscc/units/proposals.py`); `verdict`: criteria graded on a
# unit, one `outputs` row; `draft`: a row or a process a person may save, kept on the run's `end`.
Kind = Literal["artifact", "review", "session", "proposal", "verdict", "draft"]
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

    variant: Label
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
# A review's criterion: spec `R<n>`, plan proof `P<n>`, intent outcome `O<n>`, UI rule `S<n>`.
REVIEW_CRITERION = "[RPOS][0-9]+"
# A verdict's criterion: a letter for where it was read, then its number (`O1`, `W2`).
VERDICT_CRITERION = "[A-Z][0-9]+"
# What a verdict comes to, its worst criterion: any `no` is `not-met`, else any `unclear`.
Graded = Literal["met", "not-met", "unclear"]
# Evidence a `yes` or a `no` must cite: a repository path and its lines.
CITED = re.compile(r"[^\s:`]+:[0-9]+(-[0-9]+)?")


def graded(criteria: Iterable[Mapping[str, Any]]) -> Graded:
    """The worst criterion of a verdict."""
    met = {c.get("met") for c in criteria}
    return "not-met" if "no" in met else "unclear" if "unclear" in met else "met"


def criterion_list(pattern: str) -> FieldType:
    """The shape of a graded list, shared by every output that grades: each entry names its
    criterion, what it was read from, whether it is met and the evidence."""
    return {
        "list": {
            "criterion": pattern,
            "source": "text",
            "met": _enum(Met),
            "evidence": "text",
        }
    }


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
        "criteria": ("review-round", criterion_list(REVIEW_CRITERION)),
        "findings": (
            "review-round",
            {
                "list": {
                    "id": _F,
                    "state": _enum(FindingState),
                    "fixed_in": "([0-9a-f]{7,40})?",
                    "severity": _enum(Severity),
                    "criterion": REVIEW_CRITERION,
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
    "proposal": {
        "proposals": (
            "proposals.kept",
            {
                "list": {
                    "type": "text",
                    "slug": "text",
                    "title": "text",
                    "problem": "text",
                    "sources": {"list": "text"},
                }
            },
        )
    },
    "verdict": {"criteria": ("verdict_problem", criterion_list(VERDICT_CRITERION))},
    # A draft holds an agent, a process or both (`draft_problem`): each as the save routes take it.
    "draft": {
        "why": ("draft_problem", "text"),
        "agent?": ("draft_problem", {"key": "text", "fields": "json", "body": "text"}),
        "process?": ("draft_problem", {"name": "text", "process": "json"}),
    },
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

# The fields the engine reads of any artifact that declares them, keyed by the field and not by
# the state or agent that has it: a row that declares one declares it as written here.
FIELD_READS: dict[str, tuple[str, FieldType]] = {
    "unmeasured": ("spike-holds", {"list": _U}),
    "verdicts": ("spike-holds", {"list": {"id": _U, "verdict": _enum(SpikeVerdict)}}),
    "type": ("branch_for", _enum(BranchType)),
    "fix?": (
        "fast-lane",
        {
            "reproduction": "text",
            "expected": {"source": SOURCE, "text": "text"},
            "actual": "text",
        },
    ),
    "variant": ("label_of", _enum(Label)),
    "files": ("label_of", {"list": "text"}),
    "steps": (
        "render",
        {"list": {"title": "text", "paths": {"list": "text"}, "report": "text"}},
    ),
    "rests_on": ("evaluate", {"list": _U}),
    "needs_person": ("impl-claim", {"list": _F}),
    "left_lane?": ("fast-lane", "text"),
}

# Fields declared together: a row that declares one declares the rest of its group.
GROUPS = (
    ("variant", "files", "steps", "rests_on"),
    ("type", "fix?"),
    ("needs_person", "left_lane?"),
)

_WORDS = ("text", "number", "json")
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
    # An artifact's field read by name: declared as the reader expects, optional where it reads
    # it when present, and with the rest of its group once one is declared. A session (Gebo, an
    # estimate) names its own fields above.
    if kind == "artifact":
        declared = {n.rstrip("?") for n in fields}
        wanted = {f for g in GROUPS if declared & {x.rstrip("?") for x in g} for f in g}
        for field, (reader, want) in FIELD_READS.items():
            bare = field.rstrip("?")
            if bare not in declared and field not in wanted:
                continue
            if field not in (fields if field.endswith("?") else have):
                raise ContractError("contract-field-missing", f"{agent}.{field} (read by {reader})")
            _covers(f"{agent}.{bare}", reader, fields[field], want)
    return Output(
        kind=kind, version=version, fields=fields, purpose=str(output.get("purpose") or "")
    )


def load(rows: Mapping[str, Mapping[str, object]]) -> dict[str, Output]:
    """Every declaration of the rows: each `output` of a kind `submit` takes, checked."""
    out: dict[str, Output] = {}
    for agent, row in rows.items():
        output = row.get("output")
        # A row with a problem never runs (`agent-invalid`): its declaration is not read.
        if row.get("problems"):
            continue
        if isinstance(output, dict) and output.get("kind") in KINDS:
            out[agent] = check(agent, output)
    _check_process_reads(out)
    return out


def _branch_fields(process: Mapping[str, Any]) -> list[tuple[str, str]]:
    """`(agent, field)` of every `{field, is}` a state of `process` branches on."""
    out = []
    for state in process["states"].values():
        for way in state.get("next") or ():
            when = way.get("when") or []
            out += [
                (str(state.get("agent")), c["field"])
                for c in (when if isinstance(when, list) else [when])
                if c.get("field")
            ]
    return out


def _check_process_reads(declared: Mapping[str, Output]) -> None:
    """A field a process branches on is one its state's agent declares, as the reader of that
    field expects."""
    for process in pack.processes().values():
        for agent, field in _branch_fields(process):
            reader = (FIELD_READS.get(field) or FIELD_READS.get(f"{field}?") or ("",))[0]
            out = declared.get(agent)
            if reader and out and field not in {n.rstrip("?") for n in out["fields"]}:
                raise ContractError("contract-field-missing", f"{agent}.{field} (read by {reader})")


class Input(TypedDict):
    """What a stage is handed, and nothing else: earlier artifacts whole (`name?` when it may be
    absent), earlier stages' records, the unit's answers and open findings, the app's data. A
    triggered row may say `skip_when_empty` (no interventions, no session: the run is `skipped`
    at $0). Every triggered row takes a person's words, from a press or Leif, handed whole."""

    artifacts: list[str]
    outputs: list[str]
    answers: bool
    findings: bool
    data: list[str]
    skip_when_empty: NotRequired[bool]


# The app's data a stage may declare: the shared idea, the sibling checkouts, the units this one
# names, the plan's files as they stand, the files `main` changed since the plan, the last
# integration and the screenshots taken again; and, for a triggered row
# (`coscc/runner/triggers.py`), what people stepped in for since its last run, the proposals
# already made, and the catalog a row or a process is composed from (`Agents.catalog_block`).
DATA = (
    "idea",
    "siblings",
    "mentions",
    "plan-map",
    "drift",
    "integration",
    "screens",
    "interventions",
    "proposals",
    "catalog",
)
_OPTIONAL_INPUT = ("skip_when_empty",)


def check_input(
    agent: str, raw: object, agents: Iterable[str], states: Iterable[str] | None = None
) -> Input:
    """`raw` as `agent`'s input declaration, or a `ContractError` naming what is wrong. An
    artifact names a state of the pack's processes (`states`, else every process's), an output an
    agent of `agents`, either with `?` when it may be missing."""
    keys = set(Input.__annotations__) - set(_OPTIONAL_INPUT)
    if not isinstance(raw, dict) or not keys <= set(raw) <= keys | set(_OPTIONAL_INPUT):
        raise _bad(f"{agent}.input", f"an input is {{{', '.join(sorted(keys))}}}")
    named = set(pack.state_names() if states is None else states)
    for part, known in (("artifacts", named), ("outputs", set(agents))):
        names = raw[part]
        if not isinstance(names, list) or not all(
            isinstance(n, str) and n.rstrip("?") in known for n in names
        ):
            raise _bad(f"{agent}.input.{part}", f"{names!r} names none of {sorted(known)}")
    for part in ("answers", "findings", *(k for k in _OPTIONAL_INPUT if k in raw)):
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
        **{k: raw[k] for k in _OPTIONAL_INPUT if k in raw},
    )


def load_inputs(rows: Mapping[str, Mapping[str, object]]) -> dict[str, Input]:
    """Every row's `input`, checked; a row with none declares none."""
    return {
        a: check_input(a, row["input"], rows)
        for a, row in rows.items()
        if "input" in row and not row.get("problems")
    }


def row_reasons(
    agent: str, row: Mapping[str, Any], rows: Iterable[str], states: Iterable[str]
) -> list[str]:
    """Why a row's input or output is no declaration the engine can read: `pack.check`'s step for
    a row not as built (`pack.ROW_CHECKS`), so a bad one is its problem and never an error that
    stops every read."""
    out: list[str] = []
    try:
        if "input" in row:
            check_input(agent, row["input"], rows, states)
        output = row.get("output")
        if isinstance(output, dict) and output.get("kind") in KINDS:
            check(agent, output)
    except ContractError as e:
        out.append(str(e))
    return out


pack.ROW_CHECKS.append(row_reasons)


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


def declarations() -> dict[str, Output]:
    """Every row's declaration."""
    return _read()[0]


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
    out = output(agent)
    read = {
        **READS.get(out["kind"], {}),
        **READS.get(agent, {}),
        **(
            {
                f: v
                for f, v in FIELD_READS.items()
                if f.rstrip("?") in out["fields"] or f in out["fields"]
            }
            if out["kind"] == "artifact"
            else {}
        ),
    }
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
    if t == "json":
        return {"type": "object"}
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
