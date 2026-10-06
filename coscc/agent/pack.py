"""Every agent is one row of a pack: `agents/<key>.md`, its frontmatter the agent's data and its
body the agent's system prompt, with the skills it names beside it in `skills/<name>/SKILL.md`.

The built-in pack (`coscc/packs/coscc-sdlc/`) ships with the package and is never written. The
owner's layer, `<data root>/packs/local/`, holds only what differs: a frontmatter key there
replaces the built-in's, a non-empty body replaces its body, a skill file replaces that skill.
Reset is deleting the key. Both are read again when a file under them changes (mtime), so the
next run sees an edit without a restart.

A frontmatter line is `key: <one line of JSON>` (JSON is YAML, so a plugin reader sees valid
YAML); `#` lines are comments. The keys are Managed Agents' (`name`, `description`, `model`,
`skills`, `tools`) and coscc's (`KEYS`). `check` holds every row to the same rules at load and at
save; a bad built-in row stops the load, a bad owner file leaves its agent with `problems` and
its runs refused (`agent-invalid`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict

BUILTIN = Path(__file__).resolve().parent.parent / "packs" / "coscc-sdlc"
LOCAL = Path("packs") / "local"
MANIFEST = Path(".claude-plugin") / "plugin.json"
PROCESS_FILE = "process.json"
SKILL_FILE = "SKILL.md"

# The data root the owner's layer lives under; `None` is `store.db.DEFAULT_DIR`. The app sets it.
ROOT: str | None = None

KEYS = (
    "name",
    "glyph",
    "description",
    "model",
    "variants",
    "skills",
    "tools",
    "helpers",
    "input",
    "output",
    "trigger",
    "ceilings",
    "warning",
    "consequence",
)
BODY = "body"
# A field `write` takes for the text of a skill the row names: `skill:<name>`.
SKILL = "skill:"

# What the CLI's `--effort` accepts.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The bounds a row keeps. Chosen, not measured: 500 turns is twice the `novel` ceiling, $50 about
# three times its $16.
MODEL_MAX = 100
TURNS_MIN, TURNS_MAX = 1, 500
BUDGET_MIN, BUDGET_MAX = 0.10, 50.00
NAME_MAX = 24
GLYPH_MAX = 2
LINE_MAX = 200
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]*")

POLICIES = ("allow", "ask", "off")
# Issued by the engine with the grant, never named by a row.
ENGINE_TOOLS = ("submit", "peers")
# What a row's `output.kind` may be: the three `submit` kinds, a reply read as it is, a helper.
OUTPUT_KINDS = ("artifact", "review", "session", "reply", "helper")
# Who writes a stage's artifact: the app from the reply of a row that only reads (`app`), the app
# from the reply of a row that runs code in a throwaway directory (`scratch`), the session.
WRITERS = ("app", "scratch", "session")
VARIANTS = ("novel",)
ENGINES = ("integrate", "estimate", "chat")
# What a process state may do in place of running an agent: the engine opens the pull request, or
# merges it.
ACTIONS = ("open-pr", "merge")
# The named guards a process transition may ask; each is a guard of `coscc/units/guards.py`.
PROCESS_GUARDS = ("skip-decision", "spike-holds", "dependency-merged", "ship-ready", "fast-lane")
# What a `{field, is}` condition may ask of a field that is no enum.
EMPTINESS = ("non-empty", "empty")
DEFAULT_PROCESS = "coscc-sdlc/full"
AGENT_TOOL = "Agent"


class PackError(ValueError):
    """The built-in pack cannot be used: every reason, one per line."""


def parse(text: str) -> tuple[dict[str, Any], str]:
    """`(frontmatter, body)` of a row file; `ValueError` naming the line that is not one."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("a row opens with a `---` line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("the frontmatter has no closing `---`") from None
    fields: dict[str, Any] = {}
    for n, raw in enumerate(lines[1:end], start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, sep, value = raw.partition(":")
        if not sep or not key or key != key.strip():
            raise ValueError(f"line {n} is not `key: <JSON>`")
        if key in fields:
            raise ValueError(f"line {n}: {key} is given twice")
        try:
            fields[key] = json.loads(value)
        except ValueError as e:
            raise ValueError(f"line {n}: {key} is not one line of JSON: {e}") from None
    return fields, "\n".join(lines[end + 1 :]).strip()


def render(fields: Mapping[str, Any], body: str = "") -> str:
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + (body.strip() + "\n" if body.strip() else "")


def _one_line(where: str, value: Any, limit: int = LINE_MAX) -> list[str]:
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        return [f"{where} is one line of text"]
    return [f"{where} is at most {limit} characters"] if len(value) > limit else []


def no_long_context(model: str) -> str:
    """Why `model` cannot carry the `[1m]` suffix (the 1M-context beta), `""` when it can: only an
    Opus or a Sonnet id has that window, and the API refuses the beta for any other."""
    if "[1m]" in model.lower() and not any(f in model.lower() for f in ("opus", "sonnet")):
        return f"{model} has no 1M context: only an opus or sonnet id takes [1m]"
    return ""


def check_model(where: str, model: Any) -> list[str]:
    if not isinstance(model, dict) or not set(model) <= {"id", "effort", "trial"}:
        return [f"{where} is {{id, effort, trial}}"]
    out: list[str] = []
    for given in [model.get("id")] + list(model.get("trial") or []):
        if given is not None and not (
            isinstance(given, str) and given.strip() and len(given) <= MODEL_MAX
        ):
            out.append(f"{where}: a model is a name of 1 to {MODEL_MAX} characters")
        elif given is not None and (why := no_long_context(given)):
            out.append(f"{where}: {why}")
    if "trial" in model and not (isinstance(model["trial"], list) and len(model["trial"]) == 2):
        out.append(f"{where}.trial names two models, one per arm")
    if model.get("effort") is not None and model["effort"] not in EFFORTS:
        out.append(f"{where}.effort must be one of {', '.join(EFFORTS)}")
    return out


def check_ceilings(where: str, ceilings: Any) -> list[str]:
    if not isinstance(ceilings, dict) or not set(ceilings) <= {"turns", "usd"}:
        return [f"{where} is {{turns, usd}}"]
    out: list[str] = []
    turns = ceilings.get("turns")
    if "turns" in ceilings and (
        isinstance(turns, bool) or not isinstance(turns, int) or not TURNS_MIN <= turns <= TURNS_MAX
    ):
        out.append(f"{where}.turns must be a whole number from {TURNS_MIN} to {TURNS_MAX}")
    usd = ceilings.get("usd")
    if "usd" in ceilings and (
        isinstance(usd, bool)
        or not isinstance(usd, (int, float))
        or not math.isfinite(usd)
        or not BUDGET_MIN <= usd <= BUDGET_MAX
    ):
        out.append(f"{where}.usd must be from ${BUDGET_MIN:.2f} to ${BUDGET_MAX:.2f}")
    return out


def check(
    row: Mapping[str, Any],
    catalog: Mapping[str, str] | None = None,
    rows: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Why `row` (its frontmatter, `key` and `body`) cannot run, `[]` when it can.

    `catalog` maps each tool a row may name to its effect (`kernel.Hooks.catalog`); without it the
    tool names and the read-only rule are not asked. `rows` are the other rows, for the rules
    between rows (a unique name, helpers that are helper rows). The output's
    fields are `contracts`' to check.
    """
    key = str(row.get("key") or "")
    raw = row.get("output")
    output: dict[str, Any] = raw if isinstance(raw, dict) else {}
    kind = output.get("kind")
    out = [
        f"{k}: no such key (use one of {', '.join(KEYS)})"
        for k in row
        if k not in (*KEYS, "key", BODY)
    ]
    out += _check_identity(key, row, rows)
    out += _check_parts(row)
    if kind not in OUTPUT_KINDS:
        out.append(f"output.kind must be one of {', '.join(OUTPUT_KINDS)}")
    if output.get("by") is not None and output["by"] not in WRITERS:
        out.append(f"output.by must be one of {', '.join(WRITERS)}")
    out += _check_tools(row, kind, output.get("by"), catalog)
    out += _check_links(row, rows)
    out += _check_trigger(row, kind)
    if "input" in row and not isinstance(row["input"], dict):
        out.append("input is {artifacts, outputs, answers, findings, data}")
    if BODY in row and not isinstance(row[BODY], str):
        out.append("the body is text")
    return out


def _check_identity(
    key: str, row: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]] | None
) -> list[str]:
    out: list[str] = []
    name = row.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX or not _NAME.fullmatch(name):
        out.append(
            f"name must be 1 to {NAME_MAX} ASCII letters, digits or hyphens, starting with a letter"
        )
    elif rows and any(
        str(r.get("name") or "").lower() == name.lower() for k, r in rows.items() if k != key
    ):
        out.append(f"name: {name} is another agent's")
    glyph = row.get("glyph")
    if glyph is not None and (
        not isinstance(glyph, str)
        or not 1 <= len(glyph) <= GLYPH_MAX
        or any(c.isspace() for c in glyph)
    ):
        out.append(f"glyph must be 1 or {GLYPH_MAX} characters with no space")
    for field in ("description", "warning", "consequence"):
        if field in row:
            out += _one_line(field, row[field], 400 if field == "warning" else LINE_MAX)
    return out


def _check_parts(row: Mapping[str, Any]) -> list[str]:
    """The model, the ceilings and the variants."""
    out = check_model("model", row["model"]) if "model" in row else []
    out += check_ceilings("ceilings", row["ceilings"]) if "ceilings" in row else []
    variants = row.get("variants", {})
    if not isinstance(variants, dict) or not set(variants) <= set(VARIANTS):
        return [*out, f"variants are {{{', '.join(VARIANTS)}}}"]
    for v, given in variants.items():
        if not isinstance(given, dict) or not set(given) <= {"model", "ceilings"}:
            out.append(f"variants.{v} is {{model, ceilings}}")
            continue
        if "model" in given:
            out += check_model(f"variants.{v}.model", given["model"])
        if "ceilings" in given:
            out += check_ceilings(f"variants.{v}.ceilings", given["ceilings"])
    return out


def _check_tools(
    row: Mapping[str, Any], kind: Any, by: Any, catalog: Mapping[str, str] | None
) -> list[str]:
    tools = row.get("tools", {})
    if not isinstance(tools, dict):
        return ["tools is {tool: allow|ask|off}"]
    out: list[str] = []
    for tool, policy in tools.items():
        if policy not in POLICIES:
            out.append(f"tools.{tool} must be one of {', '.join(POLICIES)}")
        if tool in ENGINE_TOOLS:
            out.append(f"tools.{tool}: the engine issues it, no row names it")
        elif catalog is not None and tool not in catalog:
            out.append(f"tools.{tool}: no such tool in the catalog")
    held = [t for t, p in tools.items() if p != "off"]
    beyond = [t for t in held if (catalog or {}).get(t, "read") != "read"]
    if by == "app" and beyond:
        out.append(
            "an output the app writes from the reply holds only reading tools, "
            f"not {', '.join(beyond)}"
        )
    if kind == "helper" and AGENT_TOOL in held:
        out.append(f"a helper holds no {AGENT_TOOL}")
    return out


def _check_links(row: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]] | None) -> list[str]:
    """The helpers it names are helper rows; the skills it names exist."""
    out: list[str] = []
    helpers = row.get("helpers", [])
    if not isinstance(helpers, list) or not all(isinstance(h, str) for h in helpers):
        out.append("helpers is a list of helper rows")
    elif rows is not None:
        for h in helpers:
            if ((rows.get(h) or {}).get("output") or {}).get("kind") != "helper":
                out.append(f"helpers: {h} is no helper row")
    skills = row.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        out.append("skills is a list of skill names")
    else:
        out += [f"skills: no skill {s}" for s in skills if not skill_path(s)]
    return out


def _check_trigger(row: Mapping[str, Any], kind: Any) -> list[str]:
    """A helper has none; an engine row names its engine; a state's agent has none, the process
    names it (`agent_for`)."""
    trigger = row.get("trigger")
    if trigger is None:
        return []
    if kind == "helper":
        return ["a helper has no trigger"]
    if not isinstance(trigger, dict) or set(trigger) != {"engine"}:
        return ['trigger is {"engine": <engine>}']
    what = trigger["engine"]
    return [] if what in ENGINES else [f"trigger.engine must be one of {', '.join(ENGINES)}"]


# --- loading ------------------------------------------------------------------


def owner_dir() -> Path:
    # Imported here: the loop child reads the rows' modules and never the database.
    from coscc.store import db

    return Path(ROOT or db.DEFAULT_DIR).expanduser().resolve() / LOCAL


def _stamp(directory: Path) -> tuple[tuple[str, int, int], ...]:
    found = []
    for path in sorted(directory.glob("**/*.md")) if directory.is_dir() else ():
        try:
            st = path.stat()
        except OSError:
            continue
        found.append((str(path), st.st_mtime_ns, st.st_size))
    return tuple(found)


def _skill_name(name: Any) -> bool:
    return isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]*", name) is not None


def skill_path(name: str) -> Path | None:
    """Where `name`'s text is read from: the owner's copy first, then the built-in."""
    if not _skill_name(name):
        return None
    for root in (owner_dir(), BUILTIN):
        path = root / "skills" / name / SKILL_FILE
        if path.is_file():
            return path
    return None


def skill(name: str) -> str:
    """The text of skill `name`; `LookupError` naming where it was looked for."""
    path = skill_path(name)
    if path is None:
        raise LookupError(
            f"no skill {name}; looked under {owner_dir() / 'skills'} and {BUILTIN / 'skills'}"
        )
    return path.read_text(encoding="utf-8", errors="replace")


def manifest() -> dict[str, Any]:
    return json.loads((BUILTIN / MANIFEST).read_text(encoding="utf-8"))


def version() -> str:
    """`<name>@<version>` of the built-in pack, as a run's `start` records it."""
    found = manifest()
    return f"{found['name']}@{found['version']}"


def _builtin() -> dict[str, dict[str, Any]]:
    """The built-in rows in the manifest's order; `PackError` with every reason when one is bad."""
    rows: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for entry in manifest()["agents"]:
        path = (BUILTIN / entry).resolve()
        key = path.stem
        try:
            fields, body = parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"{key}: {e}")
            continue
        rows[key] = {**fields, "key": key, BODY: body}
    for key, row in rows.items():
        problems += [f"{key}: {r}" for r in check(row, None, rows)]
    if problems:
        raise PackError("the built-in pack cannot be used:\n" + "\n".join(problems))
    return rows


_CACHE: dict[str, Any] = {}


def _loaded() -> dict[str, dict[str, Any]]:
    """Every row, the owner's layer laid over the built-in, each with `edited` (the keys the owner
    set) and `problems`; read again only when a file under either changed."""
    owner = owner_dir()
    # The built-in pack is never written while the app runs: its path is enough.
    stamp = (str(BUILTIN), str(owner), _stamp(owner))
    if _CACHE.get("stamp") == stamp:
        return _CACHE["rows"]
    builtin = _builtin()
    rows: dict[str, dict[str, Any]] = {}
    for key, base in builtin.items():
        row = {**base, "edited": [], "problems": [], "builtin": base}
        _lay_over(row, base, owner / "agents" / f"{key}.md")
        rows[key] = row
    owned_skills = {p.parent.name for p in (owner / "skills").glob(f"*/{SKILL_FILE}")}
    for key, row in rows.items():
        row["edited"] += [f"skill:{s}" for s in row.get("skills", []) if s in owned_skills]
        if not row["problems"]:
            others = {k: _fields(r) for k, r in rows.items()}
            row["problems"] = _safely(_fields(row), others)
    stray = sorted(p.stem for p in (owner / "agents").glob("*.md") if p.stem not in rows)
    _CACHE.update(stamp=stamp, rows=rows, stray=stray)
    return rows


def _lay_over(row: dict[str, Any], base: Mapping[str, Any], path: Path) -> None:
    """The owner's file `path`, if any, laid over `row`; its problems, with the built-in's values
    kept, when it cannot be read or is no row."""
    if not path.is_file():
        return
    try:
        fields, body = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        row["problems"] = [f"{path}: {e}"]
        return
    bad = _safely({**base, **fields, **({BODY: body} if body else {})})
    if bad:
        # The owner's values stay out: the row is the built-in's, its runs refused.
        row["problems"] = [f"{path}: {r}" for r in bad]
        return
    row.update(fields)
    if body:
        row[BODY] = body
    row["edited"] = [*fields, *([BODY] if body else [])]


def _safely(
    row: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]] | None = None
) -> list[str]:
    """`check`'s reasons, or the one a value of a shape no check expected raises (a hand edit)."""
    try:
        return check(row, None, rows)
    except (TypeError, AttributeError, ValueError, KeyError) as e:
        return [f"{type(e).__name__}: {e}"]


def _fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("edited", "problems", "builtin")}


def rows() -> dict[str, dict[str, Any]]:
    """Every agent's effective row, in the manifest's order."""
    return _loaded()


def row(key: str) -> dict[str, Any] | None:
    return _loaded().get(key)


def stray() -> list[str]:
    """The owner's files that name no agent: they change nothing."""
    _loaded()
    return list(_CACHE["stray"])


def problems(key: str, catalog: Mapping[str, str] | None = None) -> list[str]:
    """Why `key`'s row may not run now: no such row, its owner file, or `check` with `catalog`."""
    found = row(key)
    if found is None:
        return [f"no agent {key}"]
    if found["problems"] or catalog is None:
        return list(found["problems"])
    return check(_fields(found), catalog, {k: _fields(r) for k, r in rows().items()})


def tools(found: Mapping[str, Any], policy: str = "allow") -> tuple[str, ...]:
    """The tools a row holds under `policy`, in its order."""
    return tuple(t for t, p in (found.get("tools") or {}).items() if p == policy)


def hash_of(found: Mapping[str, Any]) -> str:
    """12 hex of sha256 over the effective frontmatter (sorted JSON), the body and each named skill's
    text: what a run records as `row_hash`."""
    front = {k: found[k] for k in KEYS if k in found}
    texts = []
    for name in found.get("skills") or []:
        try:
            texts.append(skill(name))
        except LookupError:
            texts.append("")
    said = json.dumps([front, found.get(BODY) or "", texts], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(said.encode("utf-8")).hexdigest()[:12]


def stamp(key: str, process_ref: str | None = None) -> dict[str, Any]:
    """What a run of `key` records of its row: `pack`, `row_hash`, `edited`; with the unit's
    process, `process` and `process_hash`."""
    found = row(key)
    if found is None:
        return {}
    out = {"pack": version(), "row_hash": hash_of(found), "edited": list(found["edited"])}
    if process(process_ref) is not None:
        out.update(process=process_ref, process_hash=process_hash(process_ref))
    return out


# --- processes ----------------------------------------------------------------
#
# `process.json` beside the rows: `{version, processes: {<name>: {start, end, states}}}`. A state
# runs an `agent` (a row) or an engine `action`; `next` is `[{to, when?}]`, the first whose `when`
# holds taken, the last the main line. A `when` is one condition or a list (all hold):
# `{field, is}` reads the state's own last output, `{guard}` asks a named engine guard. The
# state's artifact is `<state>.md`; its statuses follow from what it is (`statuses`).


# A condition of a `when`: `{field, is}` or `{guard}`.
Condition = TypedDict("Condition", {"field": str, "is": str, "guard": str}, total=False)


class Way(TypedDict):
    """A way on from a state: where to, and when it is taken."""

    to: str
    when: NotRequired[Condition | list[Condition]]


class State(TypedDict, total=False):
    """A state of a process: the agent it runs or the engine's action, and its ways on."""

    agent: str
    action: str
    optional: bool
    hint: str
    skip: str
    rerun: list[str]
    next: list[Way]
    when: Condition | list[Condition]


class Process(TypedDict):
    start: str
    end: str
    states: dict[str, State]


def _conditions(when: Any) -> list[Any]:
    if when is None:
        return []
    return list(when) if isinstance(when, list) else [when]


def output_fields(found: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """A row's output fields, `?` dropped from optional names."""
    fields = ((found or {}).get("output") or {}).get("fields") or {}
    return {k.rstrip("?"): t for k, t in fields.items()} if isinstance(fields, dict) else {}


def statuses(state: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    """What the state's artifact may carry: `changes-requested` for a review, `skipped` with `skip`."""
    kind = ((rows.get(str(state.get("agent"))) or {}).get("output") or {}).get("kind")
    return (
        "draft",
        *(("changes-requested",) if kind == "review" else ()),
        "accepted",
        "rejected",
        *(("skipped",) if state.get("skip") else ()),
    )


def _check_condition(where: str, c: Any, fields: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(c, dict) or set(c) not in ({"guard"}, {"field", "is"}):
        return [f"{where}: a condition is {{guard}} or {{field, is}}"]
    if "guard" in c:
        if c["guard"] not in PROCESS_GUARDS:
            return [f"{where}: no guard {c['guard']} (use one of {', '.join(PROCESS_GUARDS)})"]
        return []
    if fields is None:
        return [f"{where}: an action has no output to read {c['field']} from"]
    if c["field"] not in fields:
        return [f"{where}: {c['field']} is no field of the agent's output"]
    t = fields[c["field"]]
    allowed = t["enum"] if isinstance(t, dict) and "enum" in t else list(EMPTINESS)
    if c["is"] not in allowed:
        return [f"{where}: {c['field']} is never {c['is']!r} (it may be {', '.join(allowed)})"]
    return []


def _required_inputs(found: Mapping[str, Any] | None) -> list[str]:
    given = (found or {}).get("input") or {}
    names = [*(given.get("artifacts") or []), *(given.get("outputs") or [])]
    return [n for n in names if isinstance(n, str) and not n.endswith("?")]


def _check_state(
    where: str, st: Any, states: Mapping[str, Any], rows: Mapping[str, Any]
) -> list[str]:
    """One state: an agent or an action, its skip and rerun, each `when` and each way on."""
    if not isinstance(st, dict):
        return [f"{where}: a state is an object"]
    out: list[str] = []
    fields: Mapping[str, Any] | None = None
    if ("agent" in st) == ("action" in st):
        out.append(f"{where}: a state names exactly one of agent or action")
    elif "agent" in st:
        found = rows.get(str(st["agent"]))
        if found is None or (found.get("output") or {}).get("kind") == "helper":
            out.append(f"{where}: agent {st['agent']} is no row that runs a state")
        else:
            fields = output_fields(found)
    elif st["action"] not in ACTIONS:
        out.append(f"{where}: action must be one of {', '.join(ACTIONS)}")
    if st.get("skip") is not None and st["skip"] not in PROCESS_GUARDS:
        out.append(f"{where}.skip: no guard {st['skip']}")
    if not set(st.get("rerun") or []) <= {"fresh", "answers"}:
        out.append(f"{where}.rerun is a list of fresh, answers")
    for c in _conditions(st.get("when")):
        out += _check_condition(f"{where}.when", c, fields)
    for i, edge in enumerate(st.get("next") or []):
        if not isinstance(edge, dict) or edge.get("to") not in states:
            out.append(f"{where}.next[{i}]: {(edge or {}).get('to')!r} is no state")
            continue
        for c in _conditions(edge.get("when")):
            out += _check_condition(f"{where}.next[{i}]", c, fields)
    return out


def _must_reach(
    start: str, states: Mapping[str, Any], nexts: Mapping[str, list[str]], reached: set[str]
) -> dict[str, set[str]]:
    """What every path from `start` to each reached state has produced: each state on it, and its
    agent."""
    every = {n for k, st in states.items() for n in (k, st.get("agent")) if n}
    avail = {k: set() if k == start else set(every) for k in reached}
    changed = True
    while changed:
        changed = False
        for k in reached - {start}:
            got = set(every)
            for p in (p for p in reached if k in nexts[p]):
                got &= avail[p] | {p, states[p].get("agent")}
            if got != avail[k]:
                avail[k], changed = got, True
    return avail


def check_process(name: str, process: Any, rows: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Why `process` cannot run on `rows`, `[]` when it can: the start and every way on name a
    state; each state runs an agent (a row that is no helper) or an action; a `{guard}` is one of
    `PROCESS_GUARDS`; a `{field, is}` names a field of the state's agent output and a value it
    may take; every state is reached from the start and a path reaches the end; every input an
    agent requires is produced on every path to its state."""
    if not isinstance(process, dict) or not isinstance(process.get("states"), dict):
        return [f"{name}: a process is {{start, end, states}}"]
    states: dict[str, Any] = process["states"]
    start = process.get("start")
    out = [] if start in states else [f"{name}: start {start!r} is no state"]
    for key, st in states.items():
        out += _check_state(f"{name}.{key}", st, states, rows)
    if out:
        return out
    nexts = {k: [e["to"] for e in st.get("next") or []] for k, st in states.items()}
    reached, todo = {str(start)}, [str(start)]
    while todo:
        for to in nexts[todo.pop()]:
            if to not in reached:
                reached.add(to)
                todo.append(to)
    out += [f"{name}.{k}: no path from start reaches it" for k in states if k not in reached]
    if not any(not nexts[k] for k in reached):
        out.append(f"{name}: no path reaches the end")
    avail = _must_reach(str(start), states, nexts, reached)
    for k in sorted(reached, key=list(states).index):
        for n in _required_inputs(rows.get(str(states[k].get("agent")))):
            if n not in avail[k]:
                out.append(f"{name}.{k}: its input {n} is not produced on every path to it")
    return out


_PROCESSES: dict[str, Any] = {}


def builtin_rows() -> Mapping[str, Mapping[str, Any]]:
    """The built-in rows, read once: the processes are checked against them."""
    if "rows" not in _PROCESSES:
        _PROCESSES["rows"] = _builtin()
    return _PROCESSES["rows"]


def processes() -> dict[str, Process]:
    """The built-in pack's processes as `<pack>/<name>`; `PackError` with every reason when one
    cannot run on the built-in rows."""
    if "all" not in _PROCESSES:
        raw = json.loads((BUILTIN / PROCESS_FILE).read_text(encoding="utf-8"))
        rows_ = builtin_rows()
        pack_name = manifest()["name"]
        problems = [r for n, p in raw["processes"].items() for r in check_process(n, p, rows_)]
        if problems:
            raise PackError("the built-in pack cannot be used:\n" + "\n".join(problems))
        _PROCESSES["all"] = {f"{pack_name}/{n}": p for n, p in raw["processes"].items()}
    return _PROCESSES["all"]


def process(ref: str | None) -> Process | None:
    """The process `<pack>/<name>` names, `None` when none does."""
    return processes().get(str(ref or ""))


def agent_for(ref: str | None, state: str) -> str | None:
    """The agent the process's state runs: the one binding of a state to a row."""
    found = (process(ref) or {}).get("states", {}).get(state) or {}
    return found.get("agent")


def process_hash(ref: str | None) -> str:
    """12 hex of sha256 over the process (sorted JSON), as a run's `start` records it."""
    said = json.dumps(process(ref), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(said.encode("utf-8")).hexdigest()[:12]


def state_names() -> tuple[str, ...]:
    """Every state of every process, first seen first."""
    return tuple(dict.fromkeys(k for p in processes().values() for k in p["states"]))


def states_of(key: str) -> str:
    """Where `key` runs, in words: `impl in full, short`; `""` for a row no state runs."""
    where: dict[str, list[str]] = {}
    for ref, p in processes().items():
        for state, st in p["states"].items():
            if st.get("agent") == key:
                where.setdefault(state, []).append(ref.rpartition("/")[2])
    return "; ".join(f"{s} in {', '.join(ps)}" for s, ps in where.items())


# --- the owner's layer --------------------------------------------------------


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def owner_fields(key: str) -> tuple[dict[str, Any], str]:
    """What the owner's file of `key` sets: `(frontmatter, body)`, empty when there is none."""
    path = owner_dir() / "agents" / f"{key}.md"
    if not path.is_file():
        return {}, ""
    return parse(path.read_text(encoding="utf-8"))


def write(
    key: str, field: str, value: Any, catalog: Mapping[str, str] | None = None
) -> tuple[Any, Any]:
    """Set `field` of `key` in the owner's layer: a frontmatter key, `body`, or `skill:<name>` (the
    text of a skill the row names, for every row naming it). `None`, or the built-in's value, resets
    it. The row is checked as it would then stand, with `catalog` (`check`); a reason is a
    `ValueError` and nothing is written. `(old, new)` effective values."""
    found = row(key)
    if found is None:
        raise ValueError(f"no such agent: {key} (use one of {', '.join(rows())})")
    if field.startswith(SKILL):
        return _write_skill(found, field.removeprefix(SKILL), value)
    if field not in (*KEYS, BODY):
        raise ValueError(
            f"{field}: no such key (use one of {', '.join((*KEYS, BODY))}, {SKILL}<name>)"
        )
    base = found["builtin"]
    fields, body = owner_fields(key)
    old = found.get(field)
    if field == BODY:
        if value is not None and not isinstance(value, str):
            raise ValueError("the body is text")
        body = "" if value is None or value.strip() == str(base.get(BODY) or "") else value
    elif value is None or value == base.get(field):
        fields.pop(field, None)
    else:
        fields[field] = value
    after = {**_fields(base), **fields, **({BODY: body.strip()} if body.strip() else {})}
    others = {k: _fields(r) for k, r in rows().items() if k != key}
    reasons = check(after, catalog, {**others, key: after})
    if reasons:
        raise ValueError("; ".join(reasons))
    path = owner_dir() / "agents" / f"{key}.md"
    if fields or body.strip():
        _atomic(path, render(fields, body))
    else:
        path.unlink(missing_ok=True)
    return old, after.get(field)


def _write_skill(found: Mapping[str, Any], name: str, value: Any) -> tuple[str, str]:
    if not _skill_name(name):
        raise ValueError(f"{SKILL}{name}: a skill name is lowercase letters, digits and hyphens")
    if name not in (found.get("skills") or []):
        raise ValueError(f"{SKILL}{name}: {found['key']} names no such skill")
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{SKILL}{name} is text")
    builtin = BUILTIN / "skills" / name / SKILL_FILE
    base = builtin.read_text(encoding="utf-8") if builtin.is_file() else ""
    old = skill(name)
    path = owner_dir() / "skills" / name / SKILL_FILE
    if value is None or not value.strip() or value.strip() == base.strip():
        path.unlink(missing_ok=True)
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
        return old, base
    text = value if value.endswith("\n") else value + "\n"
    _atomic(path, text)
    return old, text
