"""Decisions: what was decided about a unit after its spec or plan, and by whose authority.

Each decision is a row of `unit_decisions` numbered `C<n>`, never reused and never deleted; one
that is reversed only gets the day it was withdrawn, once. Its authority is fixed when it is
written, not read off the writer's name: the routes write `person`, or `delegated` when they cite
a delegation in force; the agent's tool writes `agent`. Nobody overrides a stronger authority:
an `agent` decision cannot reverse a `person` or `delegated` one, a `delegated` one cannot
reverse a `person`, and a `person` decision reverses any.

The kernel never reads the table. Every write also appends, in the same transaction, the run-log
rows it reads: a `decision`, and a `decision-withdrawn` for the one it reverses
(`coscc/service/outdated.py`). Without a run log nothing is written.

The agent's tool and the spec and plan prompt block are in `agent`; the routes and the line on
the unit screen are in `routes` and `_JS`. What each can do is in `coscc/features/decisions.md`.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Literal, TypedDict, get_args

from claude_agent_sdk import McpServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from fastapi import APIRouter, Request
from starlette.routing import BaseRoute

from coscc import units
from coscc.data import Busy, Unusable
from coscc.hooks import Block, Facts, Parts, Tool
from coscc.plugin import Ctx, Plugin, body
from coscc.service.common import OWNER, Invalid

Authority = Literal["person", "delegated", "agent"]
AUTHORITIES: tuple[Authority, ...] = get_args(Authority)
# The stages whose prompt lists the decisions, and the stages the agent may record one in (a
# prose stage cannot carry a tool).
BLOCK_STAGES = ("spec", "plan")
TOOL_STAGES = ("spike", "impl")
# The longest text a decision may carry. Chosen, not measured.
TEXT_MAX = 4000
# Writes tried when another writer takes the number first.
TRIES = 2
NUMBER = re.compile(r"C\d+")
DELEGATION = re.compile(r"D\d+")
UNIT = re.compile(r"[\w.-]{1,120}")
NO_LOG = "there is no working folder, so the decision cannot be recorded in the run log"

TABLE = """CREATE TABLE IF NOT EXISTS unit_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace   TEXT NOT NULL,
    unit        TEXT NOT NULL,
    text        TEXT NOT NULL,
    authority   TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    date        TEXT NOT NULL,
    delegation  TEXT NOT NULL DEFAULT '',
    reverses    TEXT NOT NULL DEFAULT '',
    withdrawn   TEXT NOT NULL DEFAULT ''
)"""


class Decision(TypedDict):
    id: str
    unit: str
    text: str
    authority: Authority
    by: str
    date: str
    delegation: str
    reverses: str
    withdrawn: str


class Listed(TypedDict):
    workspace: str
    unit: str
    decisions: list[Decision]


def _in_force(d: Mapping[str, Any], day: str, workspace: str) -> bool:
    """Delegation `d` holds on `day` (ISO) in `workspace` (a slot): from its first day, to its
    last if it has one, before the day it was withdrawn, and in its workspace or all."""
    start, until = str(d.get("from_day") or ""), str(d.get("until_day") or "")
    gone, where = str(d.get("withdrawn") or ""), str(d.get("workspace") or "")
    return (
        bool(start)
        and start <= day
        and (not until or day <= until)
        and (not gone or day < gone)
        and (not where or where == workspace)
    )


def _opens_with(by: str, name: object) -> bool:
    """`by` opens with `name`, case aside, and the name ends there or at a character that is not
    a letter: `Leif (CoS)` does, `Leifson` does not."""
    b, n = by.strip().casefold(), str(name or "").strip().casefold()
    return bool(n) and b.startswith(n) and (len(b) == len(n) or not b[len(n)].isalpha())


def _override_refusal(writer: Authority, target: Authority, id: str) -> str | None:
    """Why `writer` may not reverse the decision `id` of authority `target`, else `None`."""
    if writer == "agent" and target != "agent":
        return f"{id} is a {target} decision, which an agent decision cannot reverse"
    if writer == "delegated" and target == "person":
        return f"{id} is a person's decision, which a delegated decision cannot reverse"
    return None


def _decision(row: sqlite3.Row) -> Decision:
    authority = next((a for a in AUTHORITIES if a == row["authority"]), None)
    if authority is None:
        raise Invalid(f"C{row['id']} has the authority {row['authority']!r}, which is not known")
    return {
        "id": f"C{row['id']}",
        "unit": row["unit"],
        "text": row["text"],
        "authority": authority,
        "by": row["recorded_by"],
        "date": row["date"],
        "delegation": row["delegation"],
        "reverses": row["reverses"],
        "withdrawn": row["withdrawn"],
    }


def _text_of(raw: object) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise Invalid("a decision needs text")
    if len(text) > TEXT_MAX:
        raise Invalid(f"a decision is at most {TEXT_MAX} characters, this one is {len(text)}")
    return text


def _unit_of(raw: object) -> str:
    if not isinstance(raw, str) or not UNIT.fullmatch(raw):
        raise Invalid(f"not a unit name: {raw!r}")
    return raw


def _reverses_of(raw: object) -> str:
    """`C<n>` as sent, `''` for none."""
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str) or not NUMBER.fullmatch(raw.strip()):
        raise Invalid(f"a decision is named C<n>, got {raw!r}")
    return raw.strip()


def _records(d: Decision, workspace: str) -> list[dict[str, Any]]:
    """The run-log rows the kernel reads for the decision `d`: itself, and the one it reverses."""
    base = {"workspace": workspace, "unit": d["unit"]}
    rows: list[dict[str, Any]] = [
        {
            "kind": "decision",
            **base,
            "id": d["id"],
            "authority": d["authority"],
            "reverses": d["reverses"],
            "by": d["by"],
            "delegation": d["delegation"],
        }
    ]
    if d["reverses"]:
        rows.append(
            {
                "kind": "decision-withdrawn",
                **base,
                "id": d["reverses"],
                "by": d["by"],
                "reversed_by": d["id"],
            }
        )
    return rows


class Book:
    """The decisions of every unit. Holds only the `Ctx`."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def rows(self, workspace: str, unit: str) -> list[Decision]:
        """Every decision of the unit, withdrawn ones included, oldest first."""
        with self.ctx.data.connect() as conn:
            found = conn.execute(
                "SELECT * FROM unit_decisions WHERE workspace = ? AND unit = ? ORDER BY id",
                (workspace, unit),
            ).fetchall()
        return [_decision(r) for r in found]

    def _next(self) -> int:
        with self.ctx.data.connect() as conn:
            return int(
                conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM unit_decisions").fetchone()[0]
            )

    def record(
        self,
        workspace: str,
        unit: str,
        text: object,
        authority: Authority,
        by: str,
        delegation: str = "",
        reverses: object = "",
    ) -> Decision:
        """Write the decision and its run-log rows in one transaction, and return it. `Invalid`:
        no run log, bad input, or a reversal the authorities or the target's state forbid."""
        journal = self.ctx.journal()
        if journal is None:
            raise Invalid(NO_LOG)
        day = date.today().isoformat()
        d: Decision = {
            "id": "",
            "unit": _unit_of(unit),
            "text": _text_of(text),
            "authority": authority,
            "by": by,
            "date": day,
            "delegation": delegation,
            "reverses": _reverses_of(reverses),
            "withdrawn": "",
        }
        for _ in range(TRIES):
            number = self._next()
            d["id"] = f"C{number}"
            try:
                journal.append_with(_records(d, workspace), self._write(number, workspace, d))
            except sqlite3.IntegrityError:
                # Another writer took the number between the read and the insert; nothing of
                # this one was kept.
                continue
            return d
        raise Invalid("other decisions were written at the same moment; try again")

    def _write(
        self, number: int, workspace: str, d: Decision
    ) -> Callable[[sqlite3.Connection], None]:
        """What runs inside the run log's transaction: the checks that read the table, the
        withdrawal and the insert. A refusal raised here rolls the run-log rows back too."""

        def also(conn: sqlite3.Connection) -> None:
            if d["reverses"]:
                target = conn.execute(
                    "SELECT * FROM unit_decisions WHERE id = ?", (int(d["reverses"][1:]),)
                ).fetchone()
                if (
                    target is None
                    or target["workspace"] != workspace
                    or target["unit"] != d["unit"]
                ):
                    raise Invalid(f"{d['reverses']} is not a decision of this unit")
                if target["withdrawn"]:
                    raise Invalid(f"{d['reverses']} was withdrawn on {target['withdrawn']}")
                refused = _override_refusal(
                    d["authority"], _decision(target)["authority"], d["reverses"]
                )
                if refused:
                    raise Invalid(refused)
                conn.execute(
                    "UPDATE unit_decisions SET withdrawn = ? WHERE id = ? AND withdrawn = ''",
                    (d["date"], target["id"]),
                )
            conn.execute(
                "INSERT INTO unit_decisions "
                "(id, workspace, unit, text, authority, recorded_by, date, delegation, reverses) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    number,
                    workspace,
                    d["unit"],
                    d["text"],
                    d["authority"],
                    d["by"],
                    d["date"],
                    d["delegation"],
                    d["reverses"],
                ),
            )

        return also

    def delegation_or_refuse(self, workspace: str, by: str, cited: object) -> str:
        """The `D<n>` a decision recorded by `by` may cite today in `workspace`, or `Invalid`
        naming why not. What the delegation `covers` is not checked."""
        named = str(cited or "").strip()
        if not DELEGATION.fullmatch(named):
            raise Invalid(f"a delegation is named D<n>, got {cited!r}")
        try:
            rows = self.ctx.data.decisions()
        except (Unusable, sqlite3.Error, OSError) as e:
            raise Invalid(f"the decisions could not be read, so nothing was written: {e}") from e
        d = next((d for d in rows if f"D{d.get('id')}" == named), None)
        if d is None:
            raise Invalid(f"there is no decision {named}")
        if d["kind"] != "delegation":
            raise Invalid(f"{named} is a decision, not a delegation")
        slot = units.slot(workspace)
        if d["workspace"] and d["workspace"] != slot:
            raise Invalid(f"{named} does not cover this workspace")
        if not _in_force(d, date.today().isoformat(), slot):
            raise Invalid(f"{named} is not in force today")
        if not _opens_with(by, d["agent"]):
            raise Invalid(f"{named} delegates to {d['agent']}, and this decision is by {by}")
        return named


def _indented(text: str) -> str:
    return text.replace("\n", "\n  ")


def render(book: Book, facts: Facts) -> str:
    """The block of spec and plan: the unit's live decisions and how to weigh them."""
    if facts.stage not in BLOCK_STAGES:
        return ""
    try:
        rows = book.rows(facts.workspace_key, facts.unit)
    except sqlite3.OperationalError:
        # The table is made when the app starts; an app built without starting has none, and
        # a block that raises would fail the step it was to inform.
        return ""
    live = [d for d in rows if not d["withdrawn"]]
    if not live:
        return ""
    parts = [
        "# Decisions that came after",
        "",
        "These were decided about this unit after its first spec or plan was written. None has "
        "been withdrawn.",
        "",
        *(f"- {d['id']} ({d['authority']}): {_indented(d['text'])}" for d in live),
        "",
        "Follow the decision of the strongest authority: `person` over `delegated` over `agent`. "
        "When an `agent` decision contradicts a `person` or `delegated` one and does not reverse "
        "it, follow the person's and raise the contradiction as a numbered question `N.` under "
        "`## Open questions`.",
    ]
    return "\n".join(parts)


def _text(obj: dict[str, Any], refused: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(obj)}]}
    if refused:
        out["is_error"] = True
    return out


def _no(reason: str) -> dict[str, Any]:
    return _text({"result": "refused", "reason": reason}, True)


def build_tools(book: Book, facts: Facts) -> list[SdkMcpTool[Any]]:
    """The run's one tool. Whoever calls it, what it writes is an `agent` decision."""

    async def _record(args: dict[str, Any]) -> dict[str, Any]:
        try:
            got = await asyncio.to_thread(
                book.record,
                facts.workspace_key,
                facts.unit,
                args.get("text"),
                "agent",
                f"agent:{facts.stage}",
                "",
                args.get("reverses"),
            )
        except Invalid as e:
            return _no(str(e))
        except Busy:
            return _no("the database is busy; try again in a moment")
        return _text({"result": "recorded", "id": got["id"], "authority": got["authority"]})

    schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": f"the decision, at most {TEXT_MAX} characters",
            },
            "reverses": {
                "type": "string",
                "description": "C<n> of an earlier agent decision this one replaces",
            },
        },
        "required": ["text"],
    }
    described = (
        "Record a decision about this unit that nothing a person said settled. It goes into "
        "the spec and plan unconfirmed, labelled as inferred by an agent, and a person can "
        "reverse it. `reverses` withdraws an earlier agent decision; a person's or "
        "delegated one cannot be reversed by an agent."
    )
    return [tool("record_decision", described, schema)(_record)]


def agent(ctx: Ctx) -> Parts:
    book = Book(ctx)

    def make(facts: Facts) -> McpServerConfig:
        return create_sdk_mcp_server("decisions", "1.0.0", build_tools(book, facts))

    return Parts(
        tools=(Tool("decisions", ("record_decision",), TOOL_STAGES, make),),
        blocks=(Block("decisions", lambda facts: render(book, facts)),),
    )


def routes(ctx: Ctx) -> Sequence[BaseRoute]:
    book = Book(ctx)
    router = APIRouter()

    def person(workspace: str, sent: Mapping[str, object]) -> Decision:
        by = str(sent.get("by") or OWNER)
        cited = sent.get("delegation")
        delegation = book.delegation_or_refuse(workspace, by, cited) if cited else ""
        authority: Authority = "delegated" if delegation else "person"
        return book.record(
            workspace,
            str(sent.get("unit") or ""),
            sent.get("text"),
            authority,
            by,
            delegation,
            sent.get("reverses"),
        )

    def reverse(workspace: str, sent: Mapping[str, object]) -> Decision:
        target = _reverses_of(sent.get("id"))
        if not target:
            raise Invalid("send the id of the decision to reverse")
        text = sent.get("text") or f"Reverses {target}."
        return book.record(
            workspace,
            str(sent.get("unit") or ""),
            text,
            "person",
            str(sent.get("by") or OWNER),
            reverses=target,
        )

    @router.get("/api/decisions")
    async def listed(request: Request) -> Listed:
        """Every decision of a unit, withdrawn ones included. Reads only."""
        workspace = ctx.workspace_key(request.query_params.get("cwd", ""))
        unit = _unit_of(request.query_params.get("unit", ""))
        found = await asyncio.to_thread(book.rows, workspace, unit)
        return {"workspace": workspace, "unit": unit, "decisions": found}

    @router.post("/api/decisions")
    async def add(request: Request) -> Decision:
        """Write a `person` decision, or a `delegated` one when it cites a `delegation` in force
        for `by`; `reverses` withdraws an earlier one."""
        sent = await body(request)
        workspace = ctx.workspace_key(str(sent.get("cwd", "")))
        return await asyncio.to_thread(person, workspace, sent)

    @router.post("/api/decisions/reverse")
    async def reverse_one(request: Request) -> Decision:
        """Write a `person` decision that reverses the decision `id`, whatever its authority."""
        sent = await body(request)
        workspace = ctx.workspace_key(str(sent.get("cwd", "")))
        return await asyncio.to_thread(reverse, workspace, sent)

    return router.routes


# On an open unit, its decisions with their authority, and a Reverse button on each live agent
# one. The address's `ws` is a folder name; `/api/workspaces` says where it is. It draws into its
# own child of the slot, and again every few seconds, since another script clears the slot.
_JS = """
(function () {
  if (window.__coscc_decisions) return;
  window.__coscc_decisions = true;
  var listed = null;
  function here() {
    listed = listed || window.coscc.api("/api/workspaces")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { return j.workspaces || []; })
      .catch(function () { return []; });
    var ws = new URLSearchParams(window.location.search).get("ws");
    return listed.then(function (list) {
      return list.filter(function (w) { return w.name === ws; })[0] || list[0] || null;
    });
  }
  function label(d) {
    if (d.authority === "agent") return "inferred by an agent";
    if (d.authority === "delegated") return "delegated" + (d.delegation ? " (" + d.delegation + ")" : "");
    return "person";
  }
  function box(el) {
    var mine = el.querySelector("[data-decisions]");
    if (!mine) {
      mine = document.createElement("div");
      mine.setAttribute("data-decisions", "");
      mine.style.cssText = "margin:8px 0 0";
      el.appendChild(mine);
    }
    return mine;
  }
  function reverse(w, unit, d) {
    var sent = {cwd: w.path, unit: unit, id: d.id};
    return window.coscc.api("/api/decisions/reverse", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(sent)
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { window.alert(j.error || "It was not reversed."); });
    }).then(function () { draw(); });
  }
  function row(w, unit, d, by) {
    var p = document.createElement("div");
    p.style.cssText = "display:flex;gap:8px;align-items:baseline;margin:2px 0;overflow-wrap:anywhere";
    if (d.withdrawn) p.style.opacity = "0.55";
    var head = document.createElement("strong");
    head.textContent = d.id;
    var who = document.createElement("span");
    who.style.cssText = "color:var(--gray-11);font-size:12px";
    who.textContent = label(d);
    var text = document.createElement("span");
    text.style.cssText = "flex:1;min-width:0;white-space:pre-wrap";
    if (d.withdrawn) text.style.textDecoration = "line-through";
    text.textContent = d.text;
    p.appendChild(head);
    p.appendChild(who);
    p.appendChild(text);
    if (d.withdrawn) {
      var gone = document.createElement("span");
      gone.style.cssText = "color:var(--gray-11);font-size:12px";
      gone.textContent = by[d.id] ? "reversed by " + by[d.id] : "withdrawn " + d.withdrawn;
      p.appendChild(gone);
    } else if (d.authority === "agent") {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = "Reverse";
      b.setAttribute("aria-label", "Reverse " + d.id);
      b.style.cssText = "cursor:pointer";
      b.onclick = function () { b.disabled = true; reverse(w, unit, d); };
      p.appendChild(b);
    }
    return p;
  }
  function draw() {
    var el = document.getElementById("slot-unit");
    var unit = new URLSearchParams(window.location.search).get("id");
    if (!el || !unit) return;
    here().then(function (w) {
      if (!w) return null;
      var url = "/api/decisions?cwd=" + encodeURIComponent(w.path) + "&unit=" + encodeURIComponent(unit);
      return window.coscc.api(url).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { return j && {w: w, list: j.decisions || []}; });
    }).then(function (got) {
      var mine = box(document.getElementById("slot-unit") || el);
      if (!got || !got.list.length) { mine.textContent = ""; return; }
      var by = {};
      got.list.forEach(function (d) { if (d.reverses) by[d.reverses] = d.id; });
      var title = document.createElement("div");
      title.style.cssText = "font-weight:600;margin-bottom:2px";
      title.textContent = "Decisions that came after";
      mine.textContent = "";
      mine.appendChild(title);
      got.list.forEach(function (d) { mine.appendChild(row(got.w, unit, d, by)); });
    }).catch(function () {});
  }
  window.coscc.slot("slot-unit", draw);
  window.coscc.every(15000, draw);
})();
"""

PLUGIN = Plugin(
    "decisions",
    routes,
    scripts=(_JS,),
    tables=(TABLE,),
    agent=agent,
    summary="Records decisions about a unit by their authority; none overrides a person's.",
)
