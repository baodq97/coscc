"""Who a stage's session is: its glyph, name, meaning and role, resolved in one place.

Each field resolves override first (an `agent:<key>` row of the `prefs` table), then default
(`agents.json`, shipped with the package). Label, address and commit attribution are built
here so only a name that passed `check_field` reaches a trailer. Bad data never raises: it
falls back and is named in `problems`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent / "agents.json"

PREFIX = "agent:"
FIELDS = ("glyph", "name", "meaning", "role")

OVERRIDE = "override"
DEFAULT = "default"

# ASCII only, so a name is safe as the local part of the trailer's address.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
NAME_MAX = 24
GLYPH_MAX = 2
MEANING_MAX = 60
ROLE_MAX = 200

DOMAIN = "agents.coscc.invalid"

# `Author: <value>`, the value running to the `. ` before the header's next field (`Status:`,
# `Concluded by:`), or to the end of the line.
_AUTHOR = re.compile(r"(?:^|\s)Author:\s*(.*?)(?:\.\s+[A-Z][\w-]*(?: [a-z][\w-]*)*:.*)?$")
_ANSWERS = "## Answers"


def check_field(field: str, value: Any) -> str:
    """Why `value` may not be `field`'s, or `""`. The duplicate-name rule needs the other
    rows (`Agents.set_agent_field`)."""
    if field not in FIELDS:
        return f"no such field: {field} (use one of {', '.join(FIELDS)})"
    if not isinstance(value, str):
        return f"{field} must be text"
    if field == "name":
        if not 1 <= len(value) <= NAME_MAX or not _NAME.fullmatch(value):
            return (
                f"name must be 1 to {NAME_MAX} ASCII letters, digits or hyphens, "
                "starting with a letter"
            )
    elif field == "glyph":
        if not 1 <= len(value) <= GLYPH_MAX or any(c.isspace() for c in value):
            return f"glyph must be 1 or {GLYPH_MAX} characters with no space"
    else:
        limit = MEANING_MAX if field == "meaning" else ROLE_MAX
        if len(value) > limit:
            return f"{field} must be at most {limit} characters"
        if "\n" in value or "\r" in value:
            return f"{field} must be one line"
    return ""


def load_defaults(path: str | Path | None = None) -> tuple[dict[str, dict[str, str]], list[str]]:
    """The shipped rows, `{key: {field: value}}` in the file's order, and what was wrong
    with them. A field that breaks `check_field` is dropped, and a row with no `name` or
    `glyph` left is skipped. Never raises."""
    where = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except OSError as e:
        return {}, [f"{where.name} could not be read: {e}"]
    except ValueError as e:
        return {}, [f"{where.name} is not JSON: {e}"]
    rows = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return {}, [f'{where.name} has no "agents" object']
    out: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for key, entry in rows.items():
        if not isinstance(entry, dict):
            problems.append(f"{where.name}: {key!r} is not an agent entry: {entry!r}")
            continue
        row: dict[str, str] = {}
        for field in FIELDS:
            value = entry.get(field, "")
            if value == "" and field in ("meaning", "role"):
                row[field] = ""
                continue
            reason = check_field(field, value)
            if reason:
                problems.append(f"{where.name}: {key!r}: {reason}, ignored")
                value = ""
            row[field] = value
        if not row["name"] or not row["glyph"]:
            problems.append(f"{where.name}: {key!r} has no name or no glyph, skipped")
            continue
        out[str(key)] = row
    return out, problems


def overrides_from(raw_rows: dict[str, str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Parse `agent:<key>` rows as `Data.pref_rows(PREFIX)` returns them. A field that breaks
    `check_field` is skipped and named; the row's other fields still count. Never raises."""
    out: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for key, raw in sorted(raw_rows.items()):
        if not key.startswith(PREFIX):
            continue
        name = key[len(PREFIX) :]
        try:
            value = json.loads(raw)
        except TypeError, ValueError:
            problems.append(f"{key}: the stored value is not JSON ({raw!r}), ignored")
            continue
        if not isinstance(value, dict):
            problems.append(f"{key}: the stored value is not an object ({value!r}), ignored")
            continue
        fields: dict[str, str] = {}
        for field, given in value.items():
            reason = check_field(str(field), given)
            if reason:
                problems.append(f"{key}: {reason}, ignored")
                continue
            fields[str(field)] = given
        if fields:
            out[name] = fields
    return out, problems


def resolve(
    key: str, defaults: dict[str, dict[str, str]], overrides: dict[str, dict[str, str]]
) -> dict[str, Any] | None:
    """One row, each field override first, then default, with `source` saying which.
    `None` for a key the defaults do not have: an override alone makes no agent."""
    base = defaults.get(key)
    if base is None:
        return None
    mine = overrides.get(key) or {}
    row: dict[str, Any] = {"key": key, "source": {}}
    for field in FIELDS:
        if field in mine:
            row[field], row["source"][field] = mine[field], OVERRIDE
        else:
            row[field], row["source"][field] = base.get(field, ""), DEFAULT
    return row


def agent_for(
    key: str, overrides: dict[str, dict[str, str]] | None = None
) -> dict[str, Any] | None:
    """The resolved row for `key`, or `None` for one the table does not know."""
    return resolve(key, load_defaults()[0], overrides or {})


def table(overrides: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Every row Settings shows, in the default file's order, and every problem found.

    An override for a key the defaults do not have changes nothing, and says so."""
    defaults, problems = load_defaults()
    rows = [resolve(key, defaults, overrides) for key in defaults]
    problems += [
        f"{PREFIX}{k}: no agent called {k!r}, ignored"
        for k in sorted(overrides)
        if k not in defaults
    ]
    return {"rows": rows, "problems": problems}


def label(row: dict[str, Any]) -> str:
    """`<Name> (agent, <key>)`: what every `Author:`, trailer and comment names."""
    return f"{row['name']} (agent, {row['key']})"


def address(row: dict[str, Any]) -> str:
    return f"{str(row['name']).lower()}@{DOMAIN}"


def settings_json(row: dict[str, Any]) -> str:
    """The `--settings` a preset session gets: `attribution` and nothing else."""
    return json.dumps(
        {
            "attribution": {
                "commit": f"Co-authored-by: {label(row)} <{address(row)}>",
                "pr": label(row),
            }
        },
        ensure_ascii=False,
    )


def identity_section(row: dict[str, Any]) -> str:
    """The section a prompt opens with. An empty field is left out of its sentence."""
    glyph, name, meaning, role = (str(row.get(f) or "") for f in FIELDS)
    who = " ".join(p for p in (glyph, name) if p)
    if meaning:
        who += f" ({meaning})"
    lines = ["# Who you are", "", f"You are {who}, the agent of the {row['key']} stage."]
    if role:
        lines.append(f"Your role: {role}")
    lines += [
        "",
        "Where the rules below ask for your name and do not spell it out, write exactly "
        f"`{label(row)}`. That name is a role, not a person, and it grants nothing: what you "
        "may do is still decided by your tools and the gate.",
    ]
    return "\n".join(lines)


def of_record(record: dict[str, Any], overrides: dict[str, dict[str, str]] | None = None) -> str:
    """A record's own `agent`; for an older one, its stage's name in today's table; else `""`."""
    if record.get("agent"):
        return str(record["agent"])
    row = agent_for(str(record.get("stage") or ""), overrides)
    return str(row["name"]) if row else ""


def author_of(text: str) -> str:
    """The value of the last `Author:` above `## Answers`, or `""`."""
    found = ""
    for line in text.splitlines():
        if line.strip() == _ANSWERS:
            break
        match = _AUTHOR.search(line)
        if match:
            found = match.group(1).strip().rstrip(".").strip()
    return found
