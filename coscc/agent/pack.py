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
from typing import Any

BUILTIN = Path(__file__).resolve().parent.parent / "packs" / "coscc-sdlc"
LOCAL = Path("packs") / "local"
MANIFEST = Path(".claude-plugin") / "plugin.json"
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


def check_model(where: str, model: Any) -> list[str]:
    if not isinstance(model, dict) or not set(model) <= {"id", "effort", "trial"}:
        return [f"{where} is {{id, effort, trial}}"]
    out: list[str] = []
    for given in [model.get("id")] + list(model.get("trial") or []):
        if given is not None and not (
            isinstance(given, str) and given.strip() and len(given) <= MODEL_MAX
        ):
            out.append(f"{where}: a model is a name of 1 to {MODEL_MAX} characters")
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
    between rows (a unique name, helpers that are helper rows, one row per state). The output's
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
    out += _check_trigger(key, row, kind, rows)
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


def _check_trigger(
    key: str, row: Mapping[str, Any], kind: Any, rows: Mapping[str, Mapping[str, Any]] | None
) -> list[str]:
    trigger = row.get("trigger")
    if kind == "helper":
        return ["a helper has no trigger"] if trigger is not None else []
    if not isinstance(trigger, dict) or len(trigger) != 1:
        return ['trigger is {"state": <state>} or {"engine": <engine>}']
    ((how, what),) = trigger.items()
    if how == "engine":
        return [] if what in ENGINES else [f"trigger.engine must be one of {', '.join(ENGINES)}"]
    if how != "state" or not isinstance(what, str) or not what:
        return ['trigger is {"state": <state>} or {"engine": <engine>}']
    other = [
        k
        for k, r in (rows or {}).items()
        if k != key and (r.get("trigger") or {}).get("state") == what
    ]
    return [f"trigger: the state {what} is already {other[0]}'s"] if other else []


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


def skill_path(name: str) -> Path | None:
    """Where `name`'s text is read from: the owner's copy first, then the built-in."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
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
        path = owner / "agents" / f"{key}.md"
        if path.is_file():
            try:
                fields, body = parse(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                row["problems"] = [f"{path}: {e}"]
            else:
                row.update(fields)
                if body:
                    row[BODY] = body
                row["edited"] = [*fields, *([BODY] if body else [])]
        rows[key] = row
    owned_skills = {p.parent.name for p in (owner / "skills").glob(f"*/{SKILL_FILE}")}
    for key, row in rows.items():
        row["edited"] += [f"skill:{s}" for s in row.get("skills", []) if s in owned_skills]
        if not row["problems"]:
            row["problems"] = check(_fields(row), None, {k: _fields(r) for k, r in rows.items()})
    stray = sorted(p.stem for p in (owner / "agents").glob("*.md") if p.stem not in rows)
    _CACHE.update(stamp=stamp, rows=rows, stray=stray)
    return rows


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


def stamp(key: str) -> dict[str, Any]:
    """What a run of `key` records of its row: `pack`, `row_hash`, `edited`."""
    found = row(key)
    if found is None:
        return {}
    return {"pack": version(), "row_hash": hash_of(found), "edited": list(found["edited"])}


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


def write(key: str, field: str, value: Any) -> tuple[Any, Any]:
    """Set `field` (a frontmatter key, or `body`) of `key` in the owner's layer, or reset it to
    the built-in's when `value` is `None` or equals it. The row is checked as it would then stand;
    a reason is a `ValueError` and nothing is written. `(old, new)` effective values."""
    found = row(key)
    if found is None:
        raise ValueError(f"no such agent: {key} (use one of {', '.join(rows())})")
    if field not in (*KEYS, BODY):
        raise ValueError(f"{field}: no such key (use one of {', '.join((*KEYS, BODY))})")
    base = found["builtin"]
    fields, body = owner_fields(key)
    old = found.get(field)
    if field == BODY:
        body = "" if value is None or value == base.get(BODY) else str(value)
    elif value is None or value == base.get(field):
        fields.pop(field, None)
    else:
        fields[field] = value
    after = {**_fields(base), **fields, **({BODY: body} if body else {})}
    others = {k: _fields(r) for k, r in rows().items() if k != key}
    reasons = check(after, None, {**others, key: after})
    if reasons:
        raise ValueError("; ".join(reasons))
    path = owner_dir() / "agents" / f"{key}.md"
    if fields or body:
        _atomic(path, render(fields, body))
    else:
        path.unlink(missing_ok=True)
    return old, after.get(field)
