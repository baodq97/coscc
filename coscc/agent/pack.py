"""Every agent is one row of a pack: `agents/<key>.md`, its frontmatter the agent's data and its
body the agent's system prompt, with the skills it names beside it in `skills/<name>/SKILL.md`.

The built-in pack (`coscc/packs/coscc-sdlc/`) ships with the package and is never written. Every
other pack is a plugin folder under `<data root>/packs/`: each one imported (`import_zip`), and
the owner's own, `local`. A file of `local` whose key another pack has holds only what differs: a
frontmatter key there replaces that row's, a non-empty body replaces its body, a skill file
replaces that skill; reset is deleting the key. Any other file of `local` is a whole row of the
owner's. All are read again when a file under them changes (mtime), so the next run sees an edit
without a restart.

A frontmatter line is `key: <one line of JSON>` (JSON is YAML, so a plugin reader sees valid
YAML); `#` lines are comments. The keys are Managed Agents' (`name`, `description`, `model`,
`skills`, `tools`) and coscc's (`KEYS`). `check` holds every row to the same rules at load and at
save; a bad built-in row stops the load, a bad imported pack loads none of its rows, a bad owner
file leaves its agent with `problems` and its runs refused (`agent-invalid`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from coscc import bus

BUILTIN = Path(__file__).resolve().parent.parent / "packs" / "coscc-sdlc"
MANIFEST = Path(".claude-plugin") / "plugin.json"
PROCESS_FILE = "process.json"
SKILL_FILE = "SKILL.md"
# An imported zip's bounds. Chosen, not measured: the built-in pack zips to about 60 KB.
ZIP_MAX = 1_000_000
ZIP_ENTRIES = 200

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
    "default",
    "cwd",
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
# A row a trigger starts may hold Bash only inside Claude Code's OS sandbox:
# `{"Bash": {"sandbox": {"network": ["127.0.0.1:3000"]}}}`, each host a loopback one with its port.
SANDBOX_HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]{1,5})")
# Issued by the engine with the grant, never named by a row.
ENGINE_TOOLS = ("submit", "peers", "run_agent")
# What a row's `output.kind` may be: the `submit` kinds, a reply read as it is, a helper.
# `proposal` hands back work for the Backlog (`coscc/units/proposals.py`); `verdict` grades criteria;
# `draft` a row or a process for a person to save (`coscc/units/submit.py` `draft_problem`).
OUTPUT_KINDS = ("artifact", "review", "session", "proposal", "verdict", "draft", "reply", "helper")
# Who writes a stage's artifact: the app from the reply of a row that only reads (`app`), the app
# from the reply of a row that runs code in a throwaway directory (`scratch`), the session.
WRITERS = ("app", "scratch", "session")
VARIANTS = ("novel",)
ENGINES = ("integrate", "estimate", "chat")
# What may start a row that no state runs: the engine alone, or any of the rest
# (`coscc/runner/triggers.py`): a bus event, a schedule, a press, Leif's `run_agent`.
TRIGGERS = ("engine", "event", "schedule", "manual", "leif")
# Whether a triggered row runs on its event or schedule in a workspace nobody chose for.
DEFAULTS = ("on", "off")
# Where a triggered row runs, beside its workspace: `trunk`, the tree detached at the fetched
# trunk, so it reads what shipped and never a unit's branch.
CWDS = ("trunk",)
# What the engine does after a `verdict`: one proposal for the Backlog per criterion not met.
THENS = ("proposal-if-no",)
# A schedule's hours and an event's delay. Chosen: a year.
HOURS_MAX = 8760
# Claude Code's tools known to only read, all of its own a row checked with no catalog may hold.
KNOWN_READ = ("Read", "Glob", "Grep", "SendMessage", "peers")
# What a process state may do in place of running an agent: the engine opens the pull request, or
# merges it.
ACTIONS = ("open-pr", "merge")
# The named guards a process transition may ask; each is a guard of `coscc/units/guards.py`.
STATE_KEYS = ("agent", "action", "optional", "hint", "label", "skip", "rerun", "next", "when")
PROCESS_KEYS = ("start", "end", "states")
PROCESS_GUARDS = ("skip-decision", "spike-holds", "dependency-merged", "ship-ready", "fast-lane")
# What a `{field, is}` condition may ask of a field that is no enum.
EMPTINESS = ("non-empty", "empty")
DEFAULT_PROCESS = "coscc-sdlc/full"
AGENT_TOOL = "Agent"


class PackError(ValueError):
    """A pack cannot be used or written: every reason (`reasons`), and a `code` for a caller to
    branch on."""

    def __init__(self, reasons: str | list[str], code: str = "pack-refused"):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        self.code = code
        super().__init__("\n".join(self.reasons))


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
    skills: Collection[str] = (),
) -> list[str]:
    """Why `row` (its frontmatter, `key` and `body`) cannot run, `[]` when it can.

    `catalog` maps each tool a row may name to its effect (`kernel.Hooks.catalog`); without it the
    tool names and the read-only rule are not asked. `rows` are the other rows, for the rules
    between rows (a unique name, helpers that are helper rows). `skills` are the skills a pack
    being imported brings, beside those already in place. The output's fields are `contracts`' to
    check.
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
    if output.get("by") == "session" and kind not in ("artifact", "session"):
        out.append(
            "output.by session (the branch and the push) is for an artifact or session output"
        )
    out += _check_tools(row, kind, output.get("by"), catalog)
    out += _check_links(row, rows, skills)
    out += _check_trigger(row, kind, catalog)
    if output.get("then") is not None and (output["then"] not in THENS or kind != "verdict"):
        out.append(f"output.then: a verdict may have {', '.join(THENS)}")
    if "cwd" in row and (row["cwd"] not in CWDS or not triggered(row)):
        out.append(f"cwd: a row a trigger starts may run in {', '.join(CWDS)}")
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
        if tool == "Bash" and isinstance(policy, dict):
            out += _check_sandbox(row, policy)
        elif policy not in POLICIES:
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
    writes = [t for t in held if t in ("Write", "Edit", "NotebookEdit")]
    if writes and "Bash" not in held:
        out.append(
            f"a row holding {', '.join(writes)} holds Bash too, to commit its change: "
            "open-pr pushes commits only"
        )
    if kind == "helper" and AGENT_TOOL in held:
        out.append(f"a helper holds no {AGENT_TOOL}")
    return out


def _check_sandbox(row: Mapping[str, Any], given: Mapping[str, Any]) -> list[str]:
    """`{"sandbox": {"network": [host:port, ...]}}`, only on a row a trigger starts, each host a
    loopback one with its port: the network a sandboxed Bash may reach."""
    shape = 'tools.Bash is allow, ask, off or {"sandbox": {"network": ["127.0.0.1:<port>"]}}'
    box = given.get("sandbox")
    if set(given) != {"sandbox"} or not isinstance(box, dict) or not set(box) <= {"network"}:
        return [shape]
    if not triggered(row):
        return ["tools.Bash.sandbox: only a row a trigger starts runs Bash in the sandbox"]
    network = box.get("network", [])
    if not isinstance(network, list) or not all(isinstance(h, str) for h in network):
        return [shape]
    return [
        f"tools.Bash.sandbox.network: {h} is no loopback host with its port "
        "(127.0.0.1, localhost or [::1], then :<port>)"
        for h in network
        if not ((m := SANDBOX_HOST.fullmatch(h)) and 1 <= int(m.group(1)) <= 65535)
    ]


def sandbox_of(found: Mapping[str, Any] | None) -> tuple[str, ...] | None:
    """The hosts a row's sandboxed Bash may reach, `()` for none; `None` when its Bash, if it holds
    one, is not sandboxed."""
    given = ((found or {}).get("tools") or {}).get("Bash")
    box = given.get("sandbox") if isinstance(given, dict) else None
    if not isinstance(box, dict):
        return None
    return tuple(h for h in box.get("network") or () if isinstance(h, str))


def _check_links(
    row: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]] | None, skills: Collection[str]
) -> list[str]:
    """The helpers it names are helper rows; the skills it names exist."""
    out: list[str] = []
    helpers = row.get("helpers", [])
    if not isinstance(helpers, list) or not all(isinstance(h, str) for h in helpers):
        out.append("helpers is a list of helper rows")
    elif rows is not None:
        for h in helpers:
            if ((rows.get(h) or {}).get("output") or {}).get("kind") != "helper":
                out.append(f"helpers: {h} is no helper row")
    named = row.get("skills", [])
    if not isinstance(named, list) or not all(isinstance(s, str) for s in named):
        out.append("skills is a list of skill names")
    else:
        out += [f"skills: no skill {s}" for s in named if s not in skills and not skill_path(s)]
    return out


def _check_trigger(
    row: Mapping[str, Any], kind: Any, catalog: Mapping[str, str] | None
) -> list[str]:
    """A helper has none; an engine row names its engine and nothing else; a state's agent has
    none, the process names it (`agent_for`). Any other trigger is `TRIGGERS`' shapes, and a row an
    event, a schedule or Leif starts holds only reading tools, and Bash only in Claude Code's OS
    sandbox (`_check_sandbox`): no person watches it start (a press, too, is only a read in v1)."""
    trigger, default = row.get("trigger"), row.get("default")
    if trigger is None:
        return [] if default is None else ["default: only a row with a trigger has one"]
    if kind == "helper":
        return ["a helper has no trigger"]
    if not isinstance(trigger, dict) or not trigger:
        return [f"trigger is {{{', '.join(TRIGGERS)}}}"]
    out = [
        f"trigger.{k}: no such trigger (use one of {', '.join(TRIGGERS)})"
        for k in trigger
        if k not in TRIGGERS
    ]
    if "engine" in trigger:
        if len(trigger) > 1:
            out.append("trigger.engine: an engine row has no other trigger")
        if trigger["engine"] not in ENGINES:
            out.append(f"trigger.engine must be one of {', '.join(ENGINES)}")
        if default is not None:
            out.append("default: an engine row runs when the engine says, not on or off")
        return out
    if default is not None and default not in DEFAULTS:
        out.append(f"default must be one of {', '.join(DEFAULTS)}")
    if ("event" in trigger or "schedule" in trigger) and default is None:
        out.append("default: a row with an event or a schedule says whether it is on or off")
    for k in ("manual", "leif"):
        if k in trigger and trigger[k] is not True:
            out.append(f"trigger.{k} is true")
    if "schedule" in trigger:
        out += _check_hours("trigger.schedule", trigger["schedule"], "hours", required=True)
    if "event" in trigger:
        out += _check_event(row, trigger["event"])
    if reads_only(row):
        held = [t for t, p in (row.get("tools") or {}).items() if p != "off"]
        asked = [t for t, p in (row.get("tools") or {}).items() if p == "ask"]
        if asked:
            out.append(
                f"tools: a row a trigger starts has no one to ask, so {', '.join(asked)} is "
                "allow or off"
            )
        if catalog is None:
            # No catalog at load: a feature's tool (lower case) waits for the run's check, which
            # has the catalog and refuses one that does more than read; Claude Code's own must be
            # known to read.
            beyond = [t for t in held if t not in KNOWN_READ and not t.islower()]
        else:
            beyond = [t for t in held if catalog.get(t) != "read"]
        if sandbox_of(row) is not None:
            beyond = [t for t in beyond if t != "Bash"]
        if beyond:
            out.append(
                f"a row a trigger starts holds only reading tools, not {', '.join(beyond)}"
                + (' (Bash only as {"sandbox": {"network": [...]}})' if "Bash" in beyond else "")
            )
    return out


def _check_hours(where: str, given: Any, key: str, required: bool) -> list[str]:
    if not isinstance(given, dict):
        return [f"{where} is an object"]
    if key not in given:
        return [f"{where}.{key} is required"] if required else []
    n = given[key]
    if (
        isinstance(n, bool)
        or not isinstance(n, int)
        or not (1 if required else 0) <= n <= HOURS_MAX
    ):
        return [f"{where}.{key} must be a whole number from {1 if required else 0} to {HOURS_MAX}"]
    return []


def _check_event(row: Mapping[str, Any], event: Any) -> list[str]:
    """`{name, after_hours?}`: a bus fact whose payload names the workspace, and the unit when the
    row reads one."""
    if not isinstance(event, dict) or not set(event) <= {"name", "after_hours"}:
        return ["trigger.event is {name, after_hours}"]
    name = event.get("name")
    if name not in bus.NAMES:
        return [f"trigger.event.name: no bus event {name!r}"]
    fields = bus.fields_of(str(name))
    out = [] if "workspace" in fields else [f"trigger.event.name: {name} names no workspace"]
    raw = row.get("input")
    given: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if (given.get("artifacts") or given.get("outputs")) and "unit" not in fields:
        out.append(f"trigger.event.name: {name} names no unit, and the row reads one")
    return out + _check_hours("trigger.event", event, "after_hours", required=False)


def reads_only(found: Mapping[str, Any] | None) -> bool:
    """Whether a row holds only reading tools, with no one to ask: a trigger starts it, which is
    what `triggered` says (`_check_trigger`'s rule, and what the page hides from its Tools tab)."""
    return triggered(found)


def triggered(found: Mapping[str, Any] | None, how: str = "") -> bool:
    """Whether a row has a trigger other than the engine's; with `how`, that one."""
    trigger = (found or {}).get("trigger")
    if not isinstance(trigger, dict) or not trigger or "engine" in trigger:
        return False
    return not how or bool(trigger.get(how))


# --- loading ------------------------------------------------------------------
#
# Many packs, one key space: the built-in, then each imported pack under `<data>/packs/<name>/`
# (in name order), then the owner's own, `local`. A row key is one pack's; only `local` lays a file
# over another pack's row. A `local/agents/<key>.md` whose key no other pack has is a whole row of
# the owner's own. An imported pack with a problem loads none of its rows; only a bad built-in
# stops the app.

LOCAL_NAME = "local"
# An import is unpacked here, under `packs/`, then renamed into place.
IMPORTING = ".import-"
# A pack's, an agent's or a process's name.
_KEY = re.compile(r"[a-z][a-z0-9-]*")
KEY_MAX = 24


def key_problem(key: Any, what: str = "a key") -> str:
    """Why `key` cannot name a pack, an agent or a process, `""` when it can."""
    if isinstance(key, str) and len(key) <= KEY_MAX and _KEY.fullmatch(key):
        return ""
    return f"{what} is 1 to {KEY_MAX} lowercase letters, digits or hyphens, starting with a letter"


def packs_dir() -> Path:
    # Imported here: the loop child reads the rows' modules and never the database.
    from coscc.store import db

    return Path(ROOT or db.DEFAULT_DIR).expanduser().resolve() / "packs"


def owner_dir() -> Path:
    return packs_dir() / LOCAL_NAME


def _imported_dirs() -> list[Path]:
    """Every imported pack's folder: a real directory under `packs/` but `local` and in-flight
    imports."""
    root = packs_dir()
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.iterdir()
        if p.is_dir() and not p.is_symlink() and p.name != LOCAL_NAME and not p.name.startswith(".")
    )


def _stamp(directory: Path) -> tuple[tuple[str, int, int], ...]:
    found = []
    for path in sorted(directory.glob("**/*")) if directory.is_dir() else ():
        if path.suffix not in (".md", ".json"):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        found.append((str(path), st.st_mtime_ns, st.st_size))
    return tuple(found)


def _skill_name(name: Any) -> bool:
    return isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]*", name) is not None


def skill_path(name: str, owner: bool = True) -> Path | None:
    """Where `name`'s text is read from: the owner's copy first (unless `owner` is false), then
    the built-in's, then each imported pack's (whose skill names are no other pack's)."""
    if not _skill_name(name):
        return None
    roots = [*([owner_dir()] if owner else []), BUILTIN, *_imported_dirs()]
    for root in roots:
        path = root / "skills" / name / SKILL_FILE
        if path.is_file():
            return path
    return None


def skill(name: str) -> str:
    """The text of skill `name`; `LookupError` naming where it was looked for."""
    path = skill_path(name)
    if path is None:
        raise LookupError(f"no skill {name}; looked under {packs_dir()} and {BUILTIN / 'skills'}")
    return path.read_text(encoding="utf-8", errors="replace")


def skill_base(name: str) -> str:
    """The text of skill `name` without the owner's copy: what a reset puts back."""
    path = skill_path(name, owner=False)
    return path.read_text(encoding="utf-8", errors="replace") if path else ""


def manifest() -> dict[str, Any]:
    return json.loads((BUILTIN / MANIFEST).read_text(encoding="utf-8"))


def version() -> str:
    """`<name>@<version>` of the built-in pack."""
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


def _read_processes(directory: Path) -> tuple[dict[str, Any], list[str]]:
    """The `processes` of `directory`'s `process.json` (`{}` with none) and why it cannot be read."""
    path = directory / PROCESS_FILE
    if not path.is_file():
        return {}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, [f"{PROCESS_FILE}: {e}"]
    found = raw.get("processes") if isinstance(raw, dict) else None
    if not isinstance(found, dict):
        return {}, [f"{PROCESS_FILE} is {{version, processes: {{<name>: process}}}}"]
    bad = [f"{PROCESS_FILE}: {n}: {why}" for n in found if (why := key_problem(n, "a name"))]
    return ({n: p for n, p in found.items() if not key_problem(n)}, bad)


def _read_pack(
    directory: Path, taken: Mapping[str, Mapping[str, Any]], catalog: Mapping[str, str] | None
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    """`(manifest, rows, processes, problems)` of the pack folder `directory` read as an imported
    pack beside the rows `taken`: its manifest names it, its keys are no other pack's, each row
    passes `check` (with `catalog` when given). Its processes are checked once every row is in."""
    problems: list[str] = []
    try:
        found = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        found, problems = {}, [f"{MANIFEST}: {e}"]
    if not isinstance(found, dict):
        found, problems = {}, [f"{MANIFEST} is {{name, version, description}}"]
    name = found.get("name")
    if why := key_problem(name, f"{MANIFEST}: name"):
        problems.append(why)
    elif name in (manifest()["name"], LOCAL_NAME):
        problems.append(f"{MANIFEST}: name {name} is the app's own pack")
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted((directory / "agents").glob("*.md")):
        key = path.stem
        try:
            fields, body = parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"agents/{path.name}: {e}")
            continue
        if why := key_problem(key):
            problems.append(f"agents/{path.name}: {why}")
        elif key in taken:
            problems.append(f"agents/{path.name}: {key} is another pack's agent")
        else:
            rows[key] = {**fields, "key": key, BODY: body}
    skills = {p.parent.name for p in (directory / "skills").glob(f"*/{SKILL_FILE}")}
    for s in sorted(skills):
        other = skill_path(s, owner=False)
        if other is not None and directory not in other.parents:
            problems.append(f"skills/{s}: {s} is another pack's skill")
    every = {**taken, **rows}
    for key, r in rows.items():
        problems += [f"{key}: {why}" for why in _safely(r, every, catalog, skills)]
    procs, bad = _read_processes(directory)
    return found, rows, procs, problems + bad


_CACHE: dict[str, Any] = {}


def _entry(base: dict[str, Any], pack_name: str, own: bool = False) -> dict[str, Any]:
    return {**base, "edited": [], "problems": [], "builtin": base, "pack": pack_name, "own": own}


# A `held` scope: `[the stamp it saw, open]`, one list its copies (`asyncio.to_thread`, a task it
# started) share; a task that outlives the scope finds it closed and looks at the files again.
_HELD: ContextVar[list[Any] | None] = ContextVar("pack_held", default=None)


@contextmanager
def held() -> Iterator[None]:
    """One look at the packs for everything inside: the files under `packs/` are walked once,
    not at every row asked; a board read asks for rows thousands of times."""
    scope: list[Any] = [None, True]
    token = _HELD.set(scope)
    try:
        yield
    finally:
        scope[1] = False
        _HELD.reset(token)


def _loaded() -> dict[str, dict[str, Any]]:
    """Every row of every pack, the owner's files laid over them, each with `edited` (the keys the
    owner set), `problems`, `pack` and `own`; read again only when a file under `packs/` changed,
    looked at once in a `held` scope."""
    scope = _HELD.get()
    if scope is not None and not scope[1]:
        scope = None
    if scope is not None and scope[0] is not None and _CACHE.get("stamp") is scope[0]:
        return _CACHE["rows"]
    root = packs_dir()
    # The built-in pack is never written while the app runs: its path is enough.
    stamp = (str(BUILTIN), str(root), _stamp(root))
    if _CACHE.get("stamp") == stamp:
        if scope is not None:
            scope[0] = _CACHE["stamp"]
        return _CACHE["rows"]
    builtin_name = manifest()["name"]
    rows = {k: _entry(base, builtin_name) for k, base in _builtin().items()}
    packs: dict[str, dict[str, Any]] = {}
    for directory in _imported_dirs():
        taken = {k: _fields(r) for k, r in rows.items()}
        found, mine, procs, problems = _read_pack(directory, taken, None)
        if found.get("name") != directory.name:
            problems.append(f"{MANIFEST}: name {found.get('name')!r} is not its folder's")
        packs[directory.name] = {"manifest": found, "processes": procs, "problems": problems}
        if not problems:
            rows.update({k: _entry(r, directory.name) for k, r in mine.items()})
    owner = owner_dir()
    for path in sorted((owner / "agents").glob("*.md")):
        if path.stem in rows:
            _lay_over(rows[path.stem], rows[path.stem]["builtin"], path)
        else:
            rows[path.stem] = _own(path)
    owned_skills = {p.parent.name for p in (owner / "skills").glob(f"*/{SKILL_FILE}")}
    for key, row in rows.items():
        if not row["own"]:
            row["edited"] += [f"skill:{s}" for s in row.get("skills", []) if s in owned_skills]
        if not row["problems"]:
            others = {k: _fields(r) for k, r in rows.items()}
            row["problems"] = _safely(_fields(row), others)
    procs, problems = _read_processes(owner)
    packs[LOCAL_NAME] = {"manifest": _local_manifest(), "processes": procs, "problems": problems}
    every = _every_process(rows, packs)
    names = tuple(dict.fromkeys(k for p in every.values() for k in p["states"]))
    plain = {k: _fields(r) for k, r in rows.items()}
    for key, row in rows.items():
        if not row["problems"] and (row["pack"] != builtin_name or row["edited"]):
            row["problems"] = _later(key, plain[key], plain, names)
    _CACHE.update(stamp=stamp, rows=rows, packs=packs, processes=every)
    if scope is not None:
        scope[0] = stamp
    return rows


# What a layer above adds to `check` (`coscc/units/contracts.py`: a row's input and output as the
# engine reads them), asked of every row not as built: `(key, row, rows, state names) -> reasons`.
ROW_CHECKS: list[
    Callable[[str, Mapping[str, Any], Mapping[str, Any], Collection[str]], list[str]]
] = []


def _later(
    key: str, found: Mapping[str, Any], rows_: Mapping[str, Any], names: Collection[str]
) -> list[str]:
    out: list[str] = []
    for c in ROW_CHECKS:
        try:
            out += c(key, found, rows_, names)
        except (TypeError, AttributeError, ValueError, KeyError) as e:
            out.append(f"{type(e).__name__}: {e}")
    return out


def needs_catalog(found: Mapping[str, Any]) -> bool:
    """Whether a row must be checked with the catalog before it runs: one not as the built-in
    pack ships it (an edit, a whole row of the owner's, an imported pack's)."""
    return bool(found.get("edited")) or found.get("pack") != manifest()["name"]


def _own(path: Path) -> dict[str, Any]:
    """A whole row of the owner's own, checked as a whole row."""
    key = path.stem
    try:
        fields, body = parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        row = _entry({"key": key, BODY: ""}, LOCAL_NAME, own=True)
        row["problems"] = [f"{path}: {e}"]
        return row
    row = _entry({**fields, "key": key, BODY: body}, LOCAL_NAME, own=True)
    bad = [why] if (why := key_problem(key)) else []
    row["problems"] = [f"{path}: {r}" for r in bad + _safely(_fields(row))]
    return row


def _lay_over(row: dict[str, Any], base: Mapping[str, Any], path: Path) -> None:
    """The owner's file `path`, if any, laid over `row`; its problems, with the pack's values
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
        # The owner's values stay out: the row is the pack's, its runs refused.
        row["problems"] = [f"{path}: {r}" for r in bad]
        return
    row.update(fields)
    if body:
        row[BODY] = body
    row["edited"] = [*fields, *([BODY] if body else [])]


def _safely(
    row: Mapping[str, Any],
    rows: Mapping[str, Mapping[str, Any]] | None = None,
    catalog: Mapping[str, str] | None = None,
    skills: Collection[str] = (),
) -> list[str]:
    """`check`'s reasons, or the one a value of a shape no check expected raises (a hand edit)."""
    try:
        return check(row, catalog, rows, skills)
    except (TypeError, AttributeError, ValueError, KeyError) as e:
        return [f"{type(e).__name__}: {e}"]


_BOOKKEEPING = ("edited", "problems", "builtin", "pack", "own")


def _fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _BOOKKEEPING}


# A row tried before it is saved (`triggers.trial`), seen only inside that run's context:
# `[the row, the loaded rows it was laid over, the two together]`.
_TRIED: ContextVar[list[Any] | None] = ContextVar("pack_tried", default=None)


@contextmanager
def trying(made: Mapping[str, Any]) -> Iterator[None]:
    """`made` (a whole new row, as `new_row_problems` takes it) among the rows for what starts
    inside: a task begun here keeps seeing it; nothing is written and nothing else sees it."""
    token = _TRIED.set([_entry(dict(made), LOCAL_NAME, own=True), None, None])
    try:
        yield
    finally:
        _TRIED.reset(token)


def rows() -> dict[str, dict[str, Any]]:
    """Every agent's effective row: the built-in's in the manifest's order, then each imported
    pack's, then the owner's own; and the row tried, inside `trying`."""
    loaded, tried = _loaded(), _TRIED.get()
    if tried is None:
        return loaded
    if tried[1] is not loaded:
        tried[1:] = [loaded, {**loaded, str(tried[0]["key"]): tried[0]}]
    return tried[2]


def row(key: str) -> dict[str, Any] | None:
    return rows().get(key)


def problems(key: str, catalog: Mapping[str, str] | None = None) -> list[str]:
    """Why `key`'s row may not run now: no such row, its owner file, or `check` with `catalog`."""
    found = row(key)
    if found is None:
        return [f"no agent {key}"]
    if found["problems"] or catalog is None:
        return list(found["problems"])
    return check(_fields(found), catalog, {k: _fields(r) for k, r in rows().items()})


def tools(found: Mapping[str, Any], policy: str = "allow") -> tuple[str, ...]:
    """The tools a row holds under `policy`, in its order; a sandboxed Bash is allowed."""
    return tuple(
        t
        for t, p in (found.get("tools") or {}).items()
        if p == policy or (policy == "allow" and isinstance(p, dict))
    )


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


def pack_version(name: str) -> str:
    """`<name>@<version>` of pack `name`, as a run's `start` records it."""
    if name == manifest()["name"]:
        return version()
    _loaded()
    found = (_CACHE["packs"].get(name) or {}).get("manifest") or {}
    return f"{name}@{found.get('version') or ''}"


def stamp(key: str, process_ref: str | None = None) -> dict[str, Any]:
    """What a run of `key` records of its row: `pack`, `row_hash`, `edited`; with the unit's
    process, `process` and `process_hash`."""
    found = row(key)
    if found is None:
        return {}
    out = {
        "pack": pack_version(str(found["pack"])),
        "row_hash": hash_of(found),
        "edited": list(found["edited"]),
    }
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
    label: str
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


def _required_inputs(found: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """`(state, <name>)` for each artifact a row requires, `(agent, <key>)` for each output."""
    given = (found or {}).get("input") or {}
    names = [
        *(("state", n) for n in given.get("artifacts") or []),
        *(("agent", n) for n in given.get("outputs") or []),
    ]
    return [(t, n) for t, n in names if isinstance(n, str) and not n.endswith("?")]


def _check_state(
    where: str, st: Any, states: Mapping[str, Any], rows: Mapping[str, Any]
) -> list[str]:
    """One state: an agent or an action, its skip and rerun, each `when` and each way on."""
    if not isinstance(st, dict):
        return [f"{where}: a state is an object"]
    out: list[str] = [
        f"{where}: no such key {k} (use one of {', '.join(STATE_KEYS)})"
        for k in st
        if k not in STATE_KEYS
    ]
    for k, kind in (("optional", bool), ("hint", str), ("label", str)):
        if k in st and not isinstance(st[k], kind):
            out.append(f"{where}.{k} must be {kind.__name__}")
    if "agent" in st and "when" in st:
        out.append(f"{where}.when: an agent state reads none; put the condition on a way")
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
    if st.get("skip") is not None and st["skip"] != "skip-decision":
        out.append(f"{where}.skip: only skip-decision, not {st['skip']}")
    if not set(st.get("rerun") or []) <= {"fresh", "answers"}:
        out.append(f"{where}.rerun is a list of fresh, answers")
    elif (
        fields is not None
        and "questions" in fields
        and not st.get("optional")
        and "answers" not in (st.get("rerun") or [])
    ):
        out.append(
            f"{where}: tick 'go on after an answer' — its agent asks questions (rerun: answers)"
        )
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
) -> dict[str, set[tuple[str, str]]]:
    """What every path from `start` to each reached state has produced: `(state, <name>)` for each
    state on it and `(agent, <key>)` for its agent, two names apart."""

    def made(k: str) -> set[tuple[str, str]]:
        agent = states[k].get("agent")
        return {("state", k), *((("agent", str(agent)),) if agent else ())}

    every = {n for k in states for n in made(k)}
    avail: dict[str, set[tuple[str, str]]] = {
        k: set() if k == start else set(every) for k in reached
    }
    changed = True
    while changed:
        changed = False
        for k in reached - {start}:
            got = set(every)
            for p in (p for p in reached if k in nexts[p]):
                got &= avail[p] | made(p)
            if got != avail[k]:
                avail[k], changed = got, True
    return avail


def _kind(rows: Mapping[str, Mapping[str, Any]], st: Mapping[str, Any]) -> str | None:
    return ((rows.get(str(st.get("agent"))) or {}).get("output") or {}).get("kind")


def _check_walk(
    name: str, states: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """What the loop's walk assumes: an opener that is not optional, a review state that can send
    the unit back, and a fast-lane way that is never the main line (the last way)."""
    out = []
    if all(st.get("optional") for st in states.values()):
        out.append(f"{name}: every state is optional, so there is no opener")
    for k, st in states.items():
        ways = st.get("next") or []
        if _kind(rows, st) == "review" and not any(
            c.get("field") == "verdict" and c.get("is") == "changes-requested"
            for w in ways
            for c in _conditions(w.get("when"))
        ):
            out.append(
                f"{name}.{k}: a review state needs a way on when verdict is changes-requested"
            )
        if ways and any(c.get("guard") == "fast-lane" for c in _conditions(ways[-1].get("when"))):
            out.append(f"{name}.{k}: the fast-lane way is the last way, which is the main line")
    return out


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
    out = [
        f"{name}: no such key {k} (use one of {', '.join(PROCESS_KEYS)})"
        for k in process
        if k not in PROCESS_KEYS
    ]
    if not isinstance(process.get("end"), str):
        out.append(f"{name}.end must be str")
    if start not in states:
        out.append(f"{name}: start {start!r} is no state")
    # A state's name is its artifact's file name: never a path.
    bad = [f"{name}.{k!r}: {why}" for k in states if (why := key_problem(k, "a state name"))]
    if bad:
        return out + bad
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
    out += _check_walk(name, states, rows)
    avail = _must_reach(str(start), states, nexts, reached)
    reviews = {("state", k) for k, st in states.items() if _kind(rows, st) == "review"}
    out += [
        f"{name}.{k}: a review state is not on every path to it"
        for k in reached
        if states[k].get("action") == "merge" and not avail[k] & reviews
    ]
    for k in sorted(reached, key=list(states).index):
        for t, n in _required_inputs(rows.get(str(states[k].get("agent")))):
            if (t, n) not in avail[k]:
                out.append(f"{name}.{k}: its input {n} is not produced on every path to it")
    return out


_PROCESSES: dict[str, Any] = {}


def builtin_rows() -> Mapping[str, Mapping[str, Any]]:
    """The built-in rows, read once: the processes are checked against them."""
    if "rows" not in _PROCESSES:
        _PROCESSES["rows"] = _builtin()
    return _PROCESSES["rows"]


def builtin_processes() -> dict[str, Process]:
    """The built-in pack's processes as `<pack>/<name>`, read once; `PackError` with every reason
    when one cannot run on the built-in rows. The loop child reads only these and the snapshot's."""
    if "all" not in _PROCESSES:
        raw = json.loads((BUILTIN / PROCESS_FILE).read_text(encoding="utf-8"))
        rows_ = builtin_rows()
        pack_name = manifest()["name"]
        problems = [r for n, p in raw["processes"].items() for r in check_process(n, p, rows_)]
        if problems:
            raise PackError("the built-in pack cannot be used:\n" + "\n".join(problems))
        _PROCESSES["all"] = {f"{pack_name}/{n}": p for n, p in raw["processes"].items()}
    return _PROCESSES["all"]


def _every_process(
    rows_: Mapping[str, Mapping[str, Any]], packs: Mapping[str, dict[str, Any]]
) -> dict[str, Process]:
    """The built-in's processes, then each loaded pack's that `check_process` passes against every
    row; a refused one is its pack's problem."""
    out = dict(builtin_processes())
    plain = {k: _fields(r) for k, r in rows_.items()}
    for name, info in packs.items():
        if info["problems"] and name != LOCAL_NAME:
            continue
        for n, p in info["processes"].items():
            try:
                bad = check_process(n, p, plain)
            except (TypeError, AttributeError, ValueError, KeyError) as e:
                bad = [f"{n}: {type(e).__name__}: {e}"]
            info["problems"] += bad
            if not bad:
                out[f"{name}/{n}"] = p
    return out


def processes() -> dict[str, Process]:
    """Every pack's processes that can run, as `<pack>/<name>`, on or off."""
    _loaded()
    return _CACHE["processes"]


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


class Resolved(TypedDict):
    """A process as the snapshot hands it to the loop: itself, the rows it runs (their `output`
    alone) and its `process_hash`."""

    process: Process
    rows: Mapping[str, Mapping[str, Any]]
    hash: NotRequired[str]


def resolved(ref: str | None) -> Resolved | None:
    """`{process, rows, hash}`: the process `ref` names, the `output` of each row it runs and its
    `process_hash`, all the loop needs to walk it; `None` when no pack has it."""
    found = process(ref)
    if found is None:
        return None
    agents = {str(st.get("agent")) for st in found["states"].values() if st.get("agent")}
    return {
        "process": found,
        "rows": {k: {"output": (row(k) or {}).get("output") or {}} for k in sorted(agents)},
        "hash": process_hash(ref),
    }


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


# --- on or off per workspace -------------------------------------------------
#
# The prefs `packs.state` `{pack: {workspace key: "on" | "off"}}` and `packs.process`
# `{workspace key: "<pack>/<process>"}`. A pack is on and its `full` process the default until
# the owner says otherwise. A unit records the process when it opens; nothing re-reads these after.

STATE_PREF = "packs.state"
PROCESS_PREF = "packs.process"


def _pref(data: Any, name: str) -> dict[str, Any]:
    stored = data.pref(name, {})
    return stored if isinstance(stored, dict) else {}


class ProcessShown(Process):
    ref: str
    name: str
    # A process of the owner's own pack, `local`: they may change or remove it.
    own: bool


class PackShown(TypedDict):
    name: str
    version: str
    description: str
    on: bool
    process: str
    processes: list[ProcessShown]
    # `local`, the owner's own pack; one they imported (which they may remove).
    own: bool
    imported: bool
    # Why a row or a process of it does not load, in words; `[]` when all do.
    problems: list[str]


def pack_names() -> list[str]:
    """Every pack: the built-in, each imported one, the owner's own."""
    _loaded()
    return [manifest()["name"], *(n for n in _CACHE["packs"] if n != LOCAL_NAME), LOCAL_NAME]


def pack_on(data: Any, name: str, key: str) -> bool:
    """Whether pack `name` is on in workspace `key`: the built-in and `local` until switched off,
    an imported pack once switched on."""
    mine = _pref(data, STATE_PREF).get(name)
    said = mine.get(key) if isinstance(mine, dict) else None
    if said in DEFAULTS:
        return said == "on"
    return name in (manifest()["name"], LOCAL_NAME)


def chosen_process(data: Any, key: str) -> str:
    ref = _pref(data, PROCESS_PREF).get(key)
    return ref if isinstance(ref, str) and process(ref) is not None else DEFAULT_PROCESS


def default_process(data: Any, key: str) -> str | None:
    """The process a new unit of workspace `key` walks; `None` when its pack is off."""
    ref = chosen_process(data, key)
    return ref if pack_on(data, ref.partition("/")[0], key) else None


def _local_manifest() -> dict[str, Any]:
    try:
        found = json.loads((owner_dir() / MANIFEST).read_text(encoding="utf-8"))
    except OSError, ValueError:
        found = {}
    return {"name": LOCAL_NAME, "version": "1.0.0", **(found if isinstance(found, dict) else {})}


def packs_shown(data: Any, key: str) -> list[PackShown]:
    """The packs for Settings: name, version, on, the default process, each process's states,
    whose it is and what does not load."""
    _loaded()
    builtin_name = manifest()["name"]
    out: list[PackShown] = []
    for name in pack_names():
        info: dict[str, Any] = _CACHE["packs"].get(name) or {"manifest": manifest(), "problems": []}
        found: dict[str, Any] = info["manifest"]
        out.append(
            {
                "name": name,
                "version": str(found.get("version") or ""),
                "description": str(found.get("description") or ""),
                "on": pack_on(data, name, key),
                "process": chosen_process(data, key),
                "processes": [
                    ProcessShown(ref=ref, name=ref.partition("/")[2], own=name == LOCAL_NAME, **p)
                    for ref, p in processes().items()
                    if ref.partition("/")[0] == name
                ],
                "own": name == LOCAL_NAME,
                "imported": name not in (builtin_name, LOCAL_NAME),
                "problems": list(info["problems"]),
            }
        )
    return out


def set_packs(
    data: Any, key: str, name: str, on: bool | None = None, chosen: str | None = None
) -> None:
    """Switch pack `name` on or off for workspace `key` and/or choose its default process;
    `PackError` for a pack or a process that is not there."""
    if name not in pack_names():
        raise PackError(f"not a pack: {name}")
    if chosen is not None and (process(chosen) is None or not chosen.startswith(f"{name}/")):
        raise PackError(f"not a process of {name}: {chosen}")
    if on is not None:
        state = _pref(data, STATE_PREF)
        mine = state.get(name)
        state[name] = {**(mine if isinstance(mine, dict) else {}), key: "on" if on else "off"}
        data.set_pref(STATE_PREF, state)
    if chosen is not None:
        refs = _pref(data, PROCESS_PREF)
        data.set_pref(PROCESS_PREF, {**refs, key: chosen})


# The pref `agents.state` `{agent: {workspace key: "on" | "off"}}`: whether a triggered row runs on
# its event or schedule there; else its `default`. A press and Leif run it either way.
AGENT_STATE_PREF = "agents.state"


def agent_on(data: Any, key: str, workspace: str) -> bool:
    """Whether `key`'s event or schedule runs it in `workspace`: never while its pack is off."""
    found = row(key) or {}
    if not pack_on(data, str(found.get("pack") or ""), workspace):
        return False
    chosen = _pref(data, AGENT_STATE_PREF).get(key)
    said = chosen.get(workspace) if isinstance(chosen, dict) else None
    if said in DEFAULTS:
        return said == "on"
    return found.get("default") == "on"


def set_agent_on(data: Any, key: str, workspace: str, on: bool) -> None:
    """`PackError` for a row with no event or schedule to be on for."""
    if not (triggered(row(key), "event") or triggered(row(key), "schedule")):
        raise PackError(f"{key} has no event or schedule to turn on or off")
    state = _pref(data, AGENT_STATE_PREF)
    mine = state.get(key)
    state[key] = {**(mine if isinstance(mine, dict) else {}), workspace: "on" if on else "off"}
    data.set_pref(AGENT_STATE_PREF, state)


# --- the owner's pack ---------------------------------------------------------


def _atomic(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8") if isinstance(text, str) else text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _own_manifest() -> None:
    """`local`'s `plugin.json`, written on its first write."""
    path = owner_dir() / MANIFEST
    if not path.is_file():
        _atomic(path, json.dumps({"name": LOCAL_NAME, "version": "1.0.0"}, indent=2) + "\n")


def owner_fields(key: str) -> tuple[dict[str, Any], str]:
    """What the owner's file of `key` sets: `(frontmatter, body)`, empty when there is none."""
    path = owner_dir() / "agents" / f"{key}.md"
    if not path.is_file():
        return {}, ""
    return parse(path.read_text(encoding="utf-8"))


def write(
    key: str, field: str, value: Any, catalog: Mapping[str, str] | None = None
) -> tuple[Any, Any]:
    """Set `field` of `key` in the owner's pack: a frontmatter key, `body`, or `skill:<name>` (the
    text of a skill the row names, for every row naming it). On another pack's row `None`, or that
    pack's value, resets it; on a whole row of the owner's own `None` removes the key. The row is
    checked as it would then stand, with `catalog` (`check`); a reason is a `ValueError` and
    nothing is written. `(old, new)` effective values."""
    found = row(key)
    if found is None:
        raise ValueError(f"no such agent: {key} (use one of {', '.join(rows())})")
    if field.startswith(SKILL):
        return _write_skill(found, field.removeprefix(SKILL), value)
    if field not in (*KEYS, BODY):
        raise ValueError(
            f"{field}: no such key (use one of {', '.join((*KEYS, BODY))}, {SKILL}<name>)"
        )
    own = bool(found["own"])
    base = {"key": key} if own else found["builtin"]
    fields, body = owner_fields(key)
    old = found.get(field)
    if field == BODY:
        if value is not None and not isinstance(value, str):
            raise ValueError("the body is text")
        same = not own and value is not None and value.strip() == str(base.get(BODY) or "")
        body = "" if value is None or same else value
    elif value is None or (not own and value == base.get(field)):
        fields.pop(field, None)
    else:
        fields[field] = value
    after = {**_fields(base), **fields, **({BODY: body.strip()} if body.strip() else {})}
    others = {k: _fields(r) for k, r in rows().items() if k != key}
    reasons = check(after, catalog, {**others, key: after})
    if reasons:
        raise ValueError("; ".join(reasons))
    path = owner_dir() / "agents" / f"{key}.md"
    if own or fields or body.strip():
        _own_manifest()
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
    base = skill_base(name)
    old = skill(name)
    path = owner_dir() / "skills" / name / SKILL_FILE
    if value is None or not value.strip() or value.strip() == base.strip():
        path.unlink(missing_ok=True)
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
        return old, base
    text = value if value.endswith("\n") else value + "\n"
    _own_manifest()
    _atomic(path, text)
    return old, text


# The smallest row that runs: a reader a person starts, handing back proposals. It reads files.
BLANK: dict[str, Any] = {
    "model": {"id": "claude-sonnet-5-5[1m]", "effort": "low"},
    "tools": {"Read": "allow", "Glob": "allow", "Grep": "allow"},
    "output": {
        "kind": "proposal",
        "version": 1,
        "fields": {
            "proposals": {
                "list": {
                    "type": "text",
                    "slug": "text",
                    "title": "text",
                    "problem": "text",
                    "sources": {"list": "text"},
                }
            }
        },
    },
    "trigger": {"manual": True},
    "ceilings": {"turns": 4, "usd": 0.5},
}


def plain_rows() -> dict[str, Mapping[str, Any]]:
    """Every row's frontmatter, `key` and body, as `check` and `check_process` read them."""
    return {k: _fields(r) for k, r in rows().items()}


def new_row_problems(made: Mapping[str, Any], catalog: Mapping[str, str] | None) -> list[str]:
    """Why `made`, a whole new row (its frontmatter, `key` and body), cannot join the rows: a bad
    or taken key, `check` with `catalog` beside every row, and `ROW_CHECKS` (its input and output
    as the engine reads them)."""
    key = str(made.get("key") or "")
    if why := key_problem(key):
        return [why]
    if key in rows():
        return [f"{key} is taken: an agent of {rows()[key]['pack']} has it"]
    every = {**plain_rows(), key: dict(made)}
    reasons = _safely(made, every, catalog)
    if reasons:
        return reasons
    names = tuple(dict.fromkeys(k for p in processes().values() for k in p["states"]))
    return _later(key, made, every, names)


def new_row(
    key: str,
    name: str,
    start: str | None,
    catalog: Mapping[str, str] | None = None,
    given: Mapping[str, Any] | None = None,
) -> None:
    """Write `local/agents/<key>.md`: a copy of row `start` (every key but `variants` and
    `model.trial`, which would run what the page does not show; its body, its skills by name)
    named `name`, the row `given` (`{fields, body}`, a draft a person saves), or the `BLANK`
    row. A taken or bad key, or a row `new_row_problems` refuses, is a `PackError` with every
    reason and nothing is written."""
    reasons: list[str] = []
    base: Mapping[str, Any] = BLANK
    if start and given is not None:
        reasons.append("from and row: give one")
    elif start:
        found = row(start)
        if found is None:
            reasons.append(f"no agent {start} to start from")
        else:
            copied = {k: found[k] for k in KEYS if k in found and k not in ("variants", "glyph")}
            copied["description"] = f"{name}, copied from {start}."
            if isinstance(copied.get("trigger"), dict):
                # A state or the engine runs its own agent: a copy runs where a process puts it.
                kept = {k: v for k, v in copied["trigger"].items() if k not in ("state", "engine")}
                copied = {k: v for k, v in copied.items() if k != "trigger"} | (
                    {"trigger": kept} if kept else {}
                )
            if isinstance(copied.get("model"), dict):
                copied["model"] = {k: v for k, v in copied["model"].items() if k != "trial"}
            base = {**copied, BODY: found.get(BODY) or ""}
    elif given is not None:
        if not isinstance(given.get("fields"), dict) or not isinstance(given.get("body"), str):
            reasons.append("row is {fields, body}: fields an object, body text")
        else:
            base = {**given["fields"], BODY: given["body"]}
    fields = {k: v for k, v in {**base, "name": name}.items() if k != BODY}
    if not start and given is None:
        fields["description"] = f"{name}, an agent of your own."
    body = str(base.get(BODY) or "")
    made = {**fields, "key": key, BODY: body}
    reasons = reasons or new_row_problems(made, catalog)
    if reasons:
        raise PackError(reasons)
    _own_manifest()
    _atomic(owner_dir() / "agents" / f"{key}.md", render(fields, body))


def naming(key: str) -> list[str]:
    """Every process that runs `key` in one of its states, `<pack>/<name>`."""
    return [
        ref
        for ref, p in processes().items()
        if any(st.get("agent") == key for st in p["states"].values())
    ]


def delete_row(key: str) -> None:
    """Remove a whole row of the owner's own; `PackError` for any other row, or while a process
    names it."""
    found = row(key)
    if found is None or not found["own"]:
        raise PackError([f"{key} is no agent of your own pack"])
    if named := naming(key):
        raise PackError([f"{key} runs in {', '.join(named)}"], code="in-use")
    (owner_dir() / "agents" / f"{key}.md").unlink(missing_ok=True)


def write_process(name: str, given: Any) -> tuple[Any, Any]:
    """Set the owner's process `local/<name>` to `given`, or remove it with `None`, in
    `local/process.json`, once `check_process` passes against every row. `(old, new)`."""
    if why := key_problem(name, "a process name"):
        raise PackError([why])
    found, bad = _read_processes(owner_dir())
    if bad:
        raise PackError(bad)
    old = found.get(name)
    if given is None:
        if old is None:
            raise PackError([f"no process {LOCAL_NAME}/{name}"])
        found.pop(name)
    else:
        reasons = check_process(name, given, {k: _fields(r) for k, r in rows().items()})
        if reasons:
            raise PackError(reasons)
        found[name] = given
    _own_manifest()
    text = json.dumps({"version": 1, "processes": found}, indent=2, ensure_ascii=False)
    _atomic(owner_dir() / PROCESS_FILE, text + "\n")
    return old, given


# --- export and import --------------------------------------------------------

# The only files a pack zip holds.
_ENTRY = re.compile(
    r"(\.claude-plugin/plugin\.json|agents/[a-z][a-z0-9-]*\.md|skills/[a-z][a-z0-9-]*/SKILL\.md"
    r"|process\.json)"
)


def export_zip(name: str) -> bytes:
    """Pack `name` as a zip of its plugin folder. For `local`: its manifest, its own rows, its
    processes and the skills no other pack has; never its files laid over other packs' rows."""
    import io
    import zipfile

    if name not in pack_names():
        raise PackError([f"not a pack: {name}"])
    directory = {manifest()["name"]: BUILTIN, LOCAL_NAME: owner_dir()}.get(name)
    directory = directory or packs_dir() / name
    files: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory).as_posix()
        if path.is_file() and not path.is_symlink() and _ENTRY.fullmatch(rel):
            files[rel] = path.read_bytes()
    if name == LOCAL_NAME:
        own = {k for k, r in rows().items() if r["own"]}
        files = {
            rel: blob
            for rel, blob in files.items()
            if not rel.startswith(("agents/", "skills/"))
            or (rel.startswith("agents/") and rel[7:-3] in own)
            or (rel.startswith("skills/") and skill_path(rel.split("/")[1], owner=False) is None)
        }
        files[MANIFEST.as_posix()] = json.dumps(_local_manifest(), indent=2).encode() + b"\n"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, blob in files.items():
            z.writestr(rel, blob)
    return out.getvalue()


def import_zip(
    blob: bytes,
    catalog: Mapping[str, str] | None = None,
    more: Callable[[str, Mapping[str, Any]], list[str]] | None = None,
) -> str:
    """Put the pack in zip `blob` in place under `<data>/packs/<name>/`; its name.

    The zip holds at most `ZIP_MAX` bytes and `ZIP_ENTRIES` entries, only the files of a plugin
    folder (`_ENTRY`), no absolute path, no `..`, no link. It is unpacked into a fresh
    `packs/.import-*` folder, read as a pack (`_read_pack`: its manifest name, keys no other pack's,
    every row `check`ed with `catalog`, and `more`'s reasons for a row) and every process
    `check_process`ed against every row, then renamed into place. A refusal is a `PackError` with
    every reason, and nothing is left behind."""
    import io
    import shutil
    import stat
    import zipfile

    if len(blob) > ZIP_MAX:
        raise PackError([f"the zip is over {ZIP_MAX} bytes"])
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        entries = z.infolist()
    except (zipfile.BadZipFile, ValueError) as e:
        raise PackError([f"not a zip: {e}"]) from None
    reasons = [] if len(entries) <= ZIP_ENTRIES else [f"over {ZIP_ENTRIES} entries"]
    total = 0
    for e in entries:
        mode = e.external_attr >> 16
        total += e.file_size
        if e.is_dir():
            continue
        if e.flag_bits & 1 or e.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            reasons.append(f"{e.filename}: encrypted, or packed other than stored or deflated")
        elif stat.S_ISLNK(mode):
            reasons.append(f"{e.filename}: a link")
        elif e.filename.startswith("/") or "\\" in e.filename or ".." in e.filename.split("/"):
            reasons.append(f"{e.filename}: not a path inside the pack")
        elif not _ENTRY.fullmatch(e.filename):
            reasons.append(
                f"{e.filename}: a pack holds only {MANIFEST}, agents/*.md, "
                f"skills/*/{SKILL_FILE} and {PROCESS_FILE}"
            )
    if total > ZIP_MAX:
        reasons.append(f"it unpacks to over {ZIP_MAX} bytes")
    if reasons:
        raise PackError(reasons)
    root = packs_dir()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=IMPORTING, dir=root))
    try:
        try:
            for e in entries:
                if not e.is_dir():
                    _atomic(staging / e.filename, z.read(e))
        except Exception as e:  # noqa: BLE001 - any failure to unpack a stranger's zip is a refusal
            raise PackError([f"the zip cannot be unpacked: {type(e).__name__}: {e}"]) from None
        taken = {k: _fields(r) for k, r in rows().items()}
        found, mine, procs, problems = _read_pack(staging, taken, catalog)
        # A skill only the owner's pack has is theirs too.
        problems += [
            f"skills/{p.parent.name}: {p.parent.name} is your own pack's skill"
            for p in sorted((staging / "skills").glob(f"*/{SKILL_FILE}"))
            if (owner_dir() / "skills" / p.parent.name / SKILL_FILE).is_file()
        ]
        name = str(found.get("name") or "")
        if name and (name in pack_names() or (root / name).exists()):
            problems.append(f"{MANIFEST}: name {name} is taken")
        every = {**taken, **mine}
        names = {
            *state_names(),
            *(k for p in procs.values() if isinstance(p, dict) for k in (p.get("states") or {})),
        }
        problems += [f"{k}: {why}" for k, r in mine.items() for why in _later(k, r, every, names)]
        problems += [why for k, r in mine.items() for why in (more(k, r) if more else [])]
        for n, p in procs.items():
            try:
                problems += check_process(n, p, every)
            except (TypeError, AttributeError, ValueError, KeyError) as e:
                problems.append(f"{n}: {type(e).__name__}: {e}")
        if problems:
            raise PackError(problems)
        staging.rename(root / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return name


def remove_pack(name: str) -> None:
    """Remove an imported pack's folder; `PackError` for the built-in, `local` or no such pack."""
    if name in (manifest()["name"], LOCAL_NAME) or name not in pack_names():
        raise PackError([f"{name} is no imported pack"])
    import shutil

    shutil.rmtree(packs_dir() / name)
