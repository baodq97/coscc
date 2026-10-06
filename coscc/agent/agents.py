"""Who an agent is: its glyph, name, meaning and role, read from its row (`coscc/agent/pack.py`).

The meaning is the row's `description`, the role its body. Label, address and commit attribution
are built here from a name `pack.check` passed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from coscc.agent import pack

FIELDS = ("glyph", "name", "meaning", "role")
# Where each field lives in a row.
WHERE = {"glyph": "glyph", "name": "name", "meaning": "description", "role": pack.BODY}

OVERRIDE = "override"
DEFAULT = "default"


DOMAIN = "agents.coscc.invalid"

# `Author: <value>`, the value running to the `. ` before the header's next field (`Status:`,
# `Concluded by:`), or to the end of the line.
_AUTHOR = re.compile(r"(?:^|\s)Author:\s*(.*?)(?:\.\s+[A-Z][\w-]*(?: [a-z][\w-]*)*:.*)?$")
_ANSWERS = "## Answers"


def agent_for(key: str) -> dict[str, Any] | None:
    """`key`'s identity, each field with `source` (`override` when the owner's layer sets it), or
    `None` for a key no row names."""
    found = pack.row(key)
    if found is None:
        return None
    row: dict[str, Any] = {"key": key, "source": {}}
    for field in FIELDS:
        where = WHERE[field]
        row[field] = str(found.get(where) or "")
        row["source"][field] = OVERRIDE if where in found["edited"] else DEFAULT
    return row


def shown(key: str) -> bool:
    """Whether the Agents page lists `key` as an agent: a row a process state or Gebo's integration
    opens."""
    trigger = (pack.row(key) or {}).get("trigger") or {}
    return bool(pack.states_of(key)) or trigger.get("engine") == "integrate"


def table() -> dict[str, Any]:
    """Every agent the page lists, in the pack's order, and every problem found."""
    rows = [agent_for(k) for k in pack.rows() if shown(k)]
    problems = [f"{k}: {p}" for k, r in pack.rows().items() for p in r["problems"]] + [
        f"{pack.owner_dir() / 'agents' / k}.md: no agent called {k!r}, ignored"
        for k in pack.stray()
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
    """The section a prompt opens with. An empty field is left out of its sentence. The role is the
    row's body, the system prompt, so it is not said again here."""
    glyph, name, meaning = (str(row.get(f) or "") for f in ("glyph", "name", "meaning"))
    who = " ".join(p for p in (glyph, name) if p)
    if meaning:
        who += f" ({meaning})"
    lines = ["# Who you are", "", f"You are {who}, the agent of the {row['key']} stage."]
    lines += [
        "",
        "Where the rules below ask for your name and do not spell it out, write exactly "
        f"`{label(row)}`. That name is a role, not a person, and it grants nothing: what you "
        "may do is still decided by your tools and the gate.",
    ]
    return "\n".join(lines)


def of_record(record: dict[str, Any]) -> str:
    """A record's own `agent_name`; else its stage's name in today's rows; else `""`."""
    if record.get("agent_name"):
        return str(record["agent_name"])
    row = agent_for(str(record.get("stage") or ""))
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
