"""The agent sessions of a workspace, and sending one a message."""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, TypedDict

from coscc.agent import pack, policy
from coscc.agent.policy import Row
from coscc.agent import sessions as reader
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.kernel import Invalid, Run
from coscc.leif import spend
from coscc.runner import ask
from coscc.runner import run as run_mod
from coscc.runner import triggers
from coscc.runner.queue import Refused
from coscc.runner.triggers import Reply
from coscc.store.db import Busy, Data
from coscc.units.board import FOLDED_STATES, paused_label
from coscc.units.read import Card, cards
from coscc.units import proposals
from coscc.units.submit import SERVER
from coscc.units.workspaces import Workspaces

# The app's `Core` (`coscc/http/app.py`), a layer above: its parts are read by name.
Core = Any

# Leif's row, and what its turns are recorded under: the engine that opens them.
LEIF = "leif"
CHAT = "chat"


class ChatSession(TypedDict):
    """One Claude session started in the workspace's folder: by the app's chat, or in a
    terminal (`resumable` false: read only, unless the app may resume foreign sessions)."""

    session_id: str
    summary: str
    last_modified: int
    created_at: int | None
    git_branch: str | None
    resumable: bool


class ChatSessions(TypedDict):
    cwd: str
    sessions: list[ChatSession]


class ChatMessage(TypedDict):
    role: str
    text: str
    uuid: str


class LeifRun(TypedDict):
    """A run Leif started from a conversation, and how it ended (`running` until it has).
    `said` is the start of the person's message of the turn that started it (`SAID` characters),
    so it is shown after that turn's answer; "" for a turn that kept none."""

    run: str
    agent: str
    name: str
    at: str
    outcome: str
    cost_usd: float | None
    proposals: int
    said: str


class ChatHistory(TypedDict):
    session_id: str
    messages: list[ChatMessage]
    runs: list[LeifRun]


# How much of the person's message a chat turn's `start` keeps: what Talk finds the turn by.
SAID = 200

# What the app puts before a person's message when runs Leif started have ended since its last
# turn, and the line after which the person's own words follow; `history` shows only those.
NOTE_HEAD = "[The app: runs you started that ended since your last turn]"
NOTE_END = "[The person's message]"


class Chat:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        sessions: Sessions,
        refuse_updating: Callable[[], None],
        agent_for: Callable[[str, Row], run_mod.Agent],
        leif_server: Callable[[str, Mapping[str, str]], Any] | None = None,
    ) -> None:
        self.config = config
        self.ws = ws
        self.sessions = sessions
        self.refuse_updating = refuse_updating
        self.agent_for = agent_for
        # The `cos` server holding `run_agent` and the reads below, made per workspace and turn
        # (`triggers.leif_server`).
        self.leif_server = leif_server
        # Each conversation's turn, as its `run_agent` reads it (`stream`).
        self._turn: dict[str, dict[str, str]] = {}

    # -- sessions -----------------------------------------------------------

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        """The conversations in the workspace's folder: the app's chats, resumable, and those begun
        in a terminal, read only. An agent's run is no conversation: it is asked from its run page."""
        self.ws.check(cwd)
        chats, runs = self._sessions(cwd)
        rows = []
        for row in reader.list_for_directory(cwd, limit=limit):
            if row["session_id"] in runs:
                continue
            row["resumable"] = self.config.may_resume(row["session_id"] in chats)
            rows.append(row)
        return {"cwd": cwd, "sessions": rows}

    def _sessions(self, cwd: str) -> tuple[set[str], set[str]]:
        """The session ids the run log names in the workspace: chat turns', and every other run's."""
        journal = self.ws.journal()
        if journal is None:
            return set(), set()
        try:
            ends = journal.records(self.ws.key(cwd), kinds=("end",))
        except Busy as e:
            raise Invalid(str(e)) from e
        chats = {
            str(r["session_id"]) for r in ends if r.get("session_id") and r.get("stage") == CHAT
        }
        runs = {str(r["session_id"]) for r in ends if r.get("session_id")} - chats
        return chats, runs

    def _spent(self, session_id: str) -> dict[str, float]:
        """What the conversation cost before this turn (`Journal.session_cost`)."""
        journal = self.ws.journal()
        if journal is None or not session_id:
            return {}
        return journal.session_cost(session_id)

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        """The conversation as its transcript holds it, the app's notes left out, and the runs
        Leif started from it."""
        self.ws.check(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        messages = reader.history(session_id, cwd)
        for m in messages:
            if m["role"] == "user" and m["text"].startswith(NOTE_HEAD) and NOTE_END in m["text"]:
                m["text"] = m["text"].split(NOTE_END, 1)[1].lstrip("\n")
        return {
            "session_id": session_id,
            "messages": messages,
            "runs": self.leif_runs(session_id),
        }

    def leif_runs(self, session_id: str) -> list[LeifRun]:
        """The runs Leif started from the conversation `session_id`, oldest first."""
        journal = self.ws.journal()
        if journal is None or not session_id:
            return []
        try:
            turns = {
                str(r.get("run"))
                for r in journal.where("session_id", session_id, ("end",))
                if r.get("stage") == CHAT
            }
            started = [
                r
                for r in journal.where("started_by", "leif", ("start",))
                if r.get("chat_run") in turns
            ]
            out = []
            for r in started:
                run = str(r.get("run") or "")
                end = next(iter(journal.where("run", run, ("end",))), {})
                key = str(r.get("agent") or r.get("stage") or "")
                out.append(
                    LeifRun(
                        run=run,
                        agent=key,
                        name=str(end.get("agent_name") or (pack.row(key) or {}).get("name") or key),
                        at=str(end.get("at") or r.get("at") or ""),
                        outcome=str(end.get("outcome") or "running"),
                        cost_usd=end.get("cost_usd"),
                        proposals=int(end.get("proposals") or 0),
                        said=next(
                            (
                                str(t.get("said") or "")
                                for t in journal.where("run", str(r.get("chat_run")), ("start",))
                            ),
                            "",
                        ),
                    )
                )
        except Busy as e:
            raise Invalid(str(e)) from e
        return out

    def _note(self, session_id: str) -> tuple[str, list[str]]:
        """What the app tells Leif before the person's message, and the runs it names: those Leif
        started from this conversation that ended and no earlier turn was told of (each turn's
        `start` keeps `told`)."""
        journal = self.ws.journal()
        if journal is None or not session_id:
            return "", []
        turns = [
            str(r.get("run"))
            for r in journal.where("session_id", session_id, ("end",))
            if r.get("stage") == CHAT
        ]
        told = {
            str(t)
            for run in turns
            for r in journal.where("run", run, ("start",))
            for t in r.get("told") or ()
        }
        ended = [
            r
            for r in self.leif_runs(session_id)
            if r["outcome"] != "running" and r["run"] not in told
        ]
        if not ended:
            return "", []
        lines = [
            f"- {r['name']} ({r['agent']}) run {r['run']}: {r['outcome']}, "
            f"{r['proposals']} proposals, {_usd(r['cost_usd'])}; read it with runs() when asked"
            for r in ended
        ]
        return "\n".join([NOTE_HEAD, *lines, NOTE_END, ""]), [r["run"] for r in ended]

    def check_send(self, cwd: str, text: str, session_id: str | None = None) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Separate from `stream` because a generator's first item is pulled only after the
        caller has committed to streaming, when the status line is gone.
        """
        self.ws.check(cwd)
        self.refuse_updating()
        if not text.strip():
            raise Invalid("text is required")
        if session_id and not self._is_chat(cwd, session_id):
            raise Refused(
                "that session is not a conversation of this workspace: ask a run from its page",
                ("no-run",),
            )

    def _is_chat(self, cwd: str, session_id: str) -> bool:
        """Whether every `end` naming `session_id`, in any workspace, is a chat turn of `cwd`'s.
        With no run log nothing names it, and nothing is resumed either (`Sessions.known`)."""
        journal = self.ws.journal()
        if journal is None:
            return True
        try:
            ends = journal.where("session_id", session_id, ("end",))
        except Busy as e:
            raise Invalid(str(e)) from e
        ws = self.ws.key(cwd)
        return all(r.get("stage") == CHAT and r.get("workspace") == ws for r in ends)

    async def stream(
        self,
        cwd: str,
        text: str,
        session_id: str | None = None,
        resume: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """`chunk` and `tool` as the reply arrives, then one `done` with the `session_id`: one
        run of the `chat` agent (`run_mod.run`), with its `start` and `end` like any agent's.

        Re-runs the pure `check_send` so no caller can skip it. `resume` is a `suspend` row
        of a chat turn an update paused: the turn goes on from its safe point, on its
        model, with what is left of its ceiling, and opens nothing when none is. A turn refused
        or failed is `Invalid` once its `end` is written.
        """
        self.check_send(cwd, text, session_id)
        spent = self._spent(session_id or "")
        note, told = self._note(session_id or "")
        run_id = uuid.uuid4().hex
        # The turn `run_agent` is called from. A kept client keeps the server its first turn made,
        # so the conversation's one dict is updated each turn rather than a new one handed over.
        turn = self._turn.get(session_id or "") or {}
        turn.update(run=run_id, session=session_id or "")
        # Leif's row, holding the machine's own tools (`COS_TOOLS`) rather than the row's.
        row = replace(policy.row_for(LEIF), tools=tuple(self.config.effective_tools()))
        agent = self.agent_for(LEIF, row)
        if resume is not None and resume.get("model"):
            agent = replace(
                agent,
                model=str(resume["model"]),
                sources={**agent.sources, "model_source": "resumed"},
            )
        got: Run | None = None
        async for kind, payload in run_mod.run(
            agent,
            run_mod.Input(
                cwd,
                note + text,
                self.ws.key(cwd),
                stage=CHAT,
                run=run_id,
                start={"said": text[:SAID], **({"told": told} if told else {})},
                session_id=session_id,
                keep=True,
                resume=resume,
                spent_before=spent or None,
                cache_hour=True,
                **(
                    {
                        "servers": {SERVER: self.leif_server(cwd, turn)},
                        "mcp": policy.LEIF_TOOLS,
                    }
                    if self.leif_server is not None
                    else {}
                ),
            ),
            ctx=run_mod.Ctx(self.sessions, self.ws.journal(), self.config.data_dir),
        ):
            if kind == "done":
                got = payload
            else:
                yield (kind, payload)
        if got is None:
            return
        if got.session:
            turn["session"] = got.session
            self._turn[got.session] = turn
        if got.status in ("refused", "failed"):
            raise Invalid(got.detail)
        yield (
            "done",
            {"session_id": got.session, "run": got.run, "status": got.status, "cost": got.cost},
        )


# -- Leif's reads ---------------------------------------------------------------------------
# Read-only tools on the chat's `cos` server, beside `run_agent`: each a thin call into what the
# app already computes for its pages. They read no vault value, password or cookie, and write
# nothing.

# What one read hands the chat at most, in characters (about 2,000 tokens). Chosen, not measured.
READ_BUDGET = 8000
# How far back `spend` looks, as the Insights page does.
SPEND_DAYS = 30
# Units and days `spend` lists at most.
SPEND_TOP = 10
SPEND_DAYS_SHOWN = 7
# Runs `unit` lists, the latest; and characters of its brief.
UNIT_RUNS = 15
BRIEF_CHARS = 800
PROBLEM_CHARS = 400
SOURCE_CHARS = 200

_WORKSPACE = {"workspace": {"type": "string"}}


def _fit(head: list[str], lines: Sequence[str], hint: str = "") -> str:
    """`head`, then as many of `lines` as fit `READ_BUDGET`, then how many were left out."""
    out, used = list(head), sum(len(h) + 1 for h in head)
    for i, line in enumerate(lines):
        if used + len(line) + 1 > READ_BUDGET:
            out.append(f"... {len(lines) - i} more not shown{hint}")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _usd(value: Any) -> str:
    return "$?" if value is None else f"${float(value):.2f}"


def where(core: Core, cwd: str, workspace: Any) -> tuple[str, str]:
    """`(name, folder)` of `workspace`, a configured workspace's name or folder; the chat's own
    when it is empty. Anything else is refused, naming the workspaces there are."""
    asked = str(workspace or "").strip() or cwd
    rows = core.ws.all()["workspaces"]
    for r in rows:
        if asked == r["name"]:
            return r["name"], r["path"]
    if core.ws.is_member(asked):
        return core.ws.name(asked) or asked, asked
    names = ", ".join(r["name"] for r in rows) or "none"
    raise Invalid(f"not a configured workspace: {asked} (one of: {names})")


def waits_on_person(c: Card) -> str:
    """Why an open unit waits on the person, as the studio's `unitState` groups it `Needs you`
    (and the board's own `needs-you`); `""` when it does not, or it is shipped, dropped, paused
    or an idea."""
    hold = (c["hold"] or {}).get("state")
    if c["why"] in CLOSED or hold in ("paused", "dropped") or c["phase"] == "pre-intent":
        return ""
    if c["paused"]:
        return paused_label(c["paused"])
    if c["open"]:
        return f"{c['open']} open question{'s' * (c['open'] > 1)}"
    if c["missing"]:
        return "needs " + " and ".join(c["missing"])
    if c["state"]["state"] == "needs-you":
        return c["attention_reason"] or c["state"]["label"]
    return ""


# What closes a unit for `waits_on_person`: shipped, dropped, paused.
CLOSED = ("finished", "outdated-main", "dropped", "rejected", "paused")


def _card_line(c: Card) -> str:
    parts = [c["name"], c["state"]["label"]]
    if c["next_stage"]:
        parts.append(f"next {c['next_stage']}" + (f" ({c['why']})" if c["why"] else ""))
    elif c["why"]:
        parts.append(c["why"])
    parts.append(_usd(c["cost_usd"]))
    if c["pr"]:
        parts.append(f"PR #{c['pr']['number']}")
    waits = waits_on_person(c)
    if waits:
        parts.append(f"waits on you: {waits}")
    return " · ".join(parts)


async def read_board(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    got = cards(await core.board(here, "held"))
    units = got["units"]
    folded = [c for c in units if c["state"]["state"] in FOLDED_STATES]
    live = [c for c in units if c["state"]["state"] not in FOLDED_STATES]
    counts = Counter(c["state"]["label"] for c in units)
    head = [
        f"workspace {name}: {len(units)} units, {_usd(sum(c['cost_usd'] for c in units))} in all; "
        + ", ".join(f"{label} {n}" for label, n in counts.most_common()),
        "running: " + (", ".join(f"{r['unit']} {r['stage']}" for r in got["running"]) or "nothing"),
        "unit · state · next stage (why) · cost · PR — open units first, then done and dropped, newest first:",
    ]
    lines = [_card_line(c) for c in live] + [_card_line(c) for c in reversed(folded)]
    return _fit(head, lines, "; ask unit(name) for one")


def _pick(units: Sequence[Mapping[str, Any]], asked: str) -> str:
    """The unit `asked` names: its whole name, or its number with or without its leading zeros."""
    names = [str(u.get("name") or "") for u in units]
    if asked in names:
        return asked
    number = asked.split("_", 1)[0].lstrip("0")
    found = [n for n in names if number and n.split("_", 1)[0].lstrip("0") == number]
    if len(found) == 1:
        return found[0]
    raise Invalid(f"no unit {asked!r} here; read board() for the names")


async def read_unit(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    board = await core.boards.get(here, "held")
    d = await core.unit(here, _pick(board.get("units") or [], str(args.get("name") or "").strip()))
    c = d["card"]
    lines = [f"{name}/{_card_line(c)}"]
    if c["hold"]:
        lines.append(f"hold: {c['hold']['state']} by {c['hold']['by']}: {c['hold']['reason']}")
    lines.append("stages:")
    for s in d["stages"]:
        last = s["last_run"]
        ran = f" (last run {last['outcome']}, {_usd(last['cost_usd'])})" if last else ""
        lines.append(f"- {s['stage']}: {s['status'] or 'not yet'}{ran}")
    asked = [q for q in d["questions"] if not q["answered"]]
    lines.append(f"open questions: {len(asked)}")
    for q in asked:
        rec = f" — recommended: {q['recommendation'][:200]}" if q["recommendation"] else ""
        lines.append(f"- {q['artifact']} Q{q['n']}: {q['text'][:300]}{rec}")
    if d["rounds"]:
        r = d["rounds"][-1]
        lines.append(
            f"review round {r['n']} of {len(d['rounds'])}: {r['verdict'] or 'unfinished'}, "
            f"{r['findings_open']} of {r['findings']} findings open"
        )
        for f in r["items"]:
            lines.append(f"- {f['severity']} {f['label']} {f['place']}: {f['text'][:200]}")
    runs = d["runs"][-UNIT_RUNS:]
    lines.append(
        f"runs: {len(d['runs'])}, {_usd(sum(r['cost_usd'] or 0 for r in d['runs']))}"
        + (f"; the last {len(runs)}:" if len(runs) < len(d["runs"]) else ":")
    )
    for r in runs:
        lines.append(
            f"- {r['stage']} {r['outcome'] or 'running'} {_usd(r['cost_usd'])} {r['ended'][:16]}"
        )
    brief = d["brief"].strip()
    if brief:
        cut = f" ... ({len(brief)} chars)" if len(brief) > BRIEF_CHARS else ""
        lines.append(f"brief: {brief[:BRIEF_CHARS]}{cut}")
    return _fit([], lines)


async def read_needs_you(core: Core, _cwd: str, _args: Mapping[str, Any]) -> str:
    lines = []
    for r in core.ws.all()["workspaces"]:
        if r["missing"]:
            continue
        try:
            got = cards(await core.board(r["path"], "held"))
        except Invalid as e:
            lines.append(f"{r['name']}: not read ({e})")
            continue
        for c in got["units"]:
            waits = waits_on_person(c)
            if waits:
                lines.append(f"{r['name']}/{c['name']} · {waits} · at {c['at'] or '-'}")
    n = sum("·" in line for line in lines)
    head = [f"waiting on you: {n} unit{'s' * (n != 1)}, open units only"]
    return _fit(head, lines)


def _spend(core: Core, key: str) -> dict[str, Any]:
    journal = core.ws.journal()
    if journal is None:
        return spend.model([])
    since = (datetime.now(timezone.utc) - timedelta(days=SPEND_DAYS)).isoformat()
    try:
        rows = journal.records(key)
    except Busy as e:
        raise Invalid(str(e)) from e
    return spend.model(r for r in rows if str(r.get("at") or "") >= since)


async def read_spend(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    today = core.autopilot.today(here)
    found = await asyncio.to_thread(_spend, core, core.ws.key(here))
    head = [
        f"today, every workspace: {_usd(today[0])} of the {_usd(today[1])} daily cap, "
        f"{_usd(max(0.0, today[1] - today[0]))} left"
        if today
        else "today: the run log cannot be read now",
        f"workspace {name}, the last {SPEND_DAYS} days: {_usd(found['total']['usd'])} "
        f"over {found['total']['steps']} runs",
    ]
    lines = [f"by day (last {SPEND_DAYS_SHOWN}):"]
    lines += [
        f"- {d['key']}: {_usd(d['usd'])}, {d['steps']} runs"
        for d in found["by_day"][:SPEND_DAYS_SHOWN]
    ]
    lines.append("by agent:")
    lines += [f"- {a['key']}: {_usd(a['usd'])}, {a['steps']} runs" for a in found["by_agent"]]
    lines.append(f"costliest units (top {SPEND_TOP}), with their costliest stages:")
    for u in [u for u in found["by_unit"] if u["key"]][:SPEND_TOP]:
        stages = found["unit_stages"].get(u["key"]) or []
        top = ", ".join(f"{s['key']} {_usd(s['usd'])} x{s['steps']}" for s in stages[:4])
        lines.append(f"- {u['key']}: {_usd(u['usd'])} over {u['steps']} runs ({top})")
    return _fit(head, lines)


async def read_agents(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    with pack.held():
        page = core.agents.agent_page(core.ws.key(here), cwd=here)
    lines = []
    for r in page["rows"]:
        conf, ceil = r["config"], r["config"]["ceilings"]
        trigger = r["row"].get("trigger") or {}
        fired = ", ".join(k for k, v in trigger.items() if v and k != "engine")
        on = "" if r["on"] is None else f" · {'on' if r['on'] else 'off'} in {name}"
        lines.append(
            f"{r['key']} ({r['row'].get('name') or r['key']}, {r['group']}) · "
            f"{conf['model'] or 'default model'} {conf['effort'] or ''} · "
            f"≤{ceil['max_turns'] or '?'} turns, "
            f"{_usd(ceil['max_budget_usd']) if ceil['max_budget_usd'] else 'no $ ceiling'} · "
            f"trigger {fired or trigger.get('engine') or 'its stage'}{on} · "
            f"{r['runs_30d']} runs {_usd(r['cost_30d'])} in 30d"
            + (f" · problems: {'; '.join(r['problems'])}" if r["problems"] else "")
        )
    return _fit([f"{len(lines)} agents:"], lines)


def _source(s: Mapping[str, Any]) -> str:
    return f"{s.get('kind') or ''} {s.get('id') or ''}".strip() + (
        f" ({s['unit']})" if s.get("unit") else ""
    )


def _is(key: str, asked: str) -> bool:
    """Whether `asked` names the agent `key`: its key, its name, or a word of either."""
    asked = asked.strip().lower()
    name = str((pack.row(key) or {}).get("name") or "").lower()
    return not asked or asked in key.lower() or asked == name


async def read_proposals(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    got = await asyncio.to_thread(core.agents.proposals_view, here)
    asked = str(args.get("agent") or "")
    made = [p for p in got["proposals"] if _is(p["agent"], asked)]
    shown = made if args.get("include_decided") else [p for p in made if p["state"] == "pending"]
    lines = []
    for p in shown:
        sources = "; ".join(_source(s) for s in p["sources"])
        line = (
            f"#{p['id']} · {p['state']} · {p['agent']} · {p['type']} · {p['title']} · "
            f"{p['at'][:16]} · run {p['run'] or '-'} · problem: {p['problem'][:PROBLEM_CHARS]} · "
            f"sources: {sources[:SOURCE_CHARS]}"
        )
        if p["state"] != "pending":
            line += f" · by {p['by'] or '-'}: {p['reason'][:200]}"
        lines.append(line)
    pending = sum(p["state"] == "pending" for p in made)
    head = [
        f"workspace {name}: {pending} pending proposals"
        + (f" from {asked}" if asked else "")
        + (f", {len(made) - pending} decided shown" if args.get("include_decided") else "")
        + ", newest first"
    ]
    return _fit(head, lines)


# Runs `runs` lists at most, newest first, and characters of each one's last words.
RUNS_SHOWN = 8
SUMMARY_CHARS = 700


def _runs(core: Core, ws: str, asked: str) -> list[str]:
    journal = core.ws.journal()
    if journal is None:
        return []
    try:
        ends = [
            r
            for r in journal.records(ws, kinds=("end",))
            if pack.triggered(pack.row(str(r.get("agent") or "")))
            and _is(str(r.get("agent")), asked)
        ]
    except Busy as e:
        raise Invalid(str(e)) from e
    asks = Counter(str(r.get("parent_run")) for r in ends if r.get("stage") == ask.STAGE)
    runs = [r for r in ends if r.get("stage") != ask.STAGE]
    data = Data(core.config.data_dir)
    made = proposals.listed(data, ws)
    lines = []
    for end in reversed(runs[-RUNS_SHOWN:]):
        run, key = str(end.get("run") or ""), str(end.get("agent"))
        start = next(iter(journal.where("run", run, ("start",))), {})
        lines.append(
            f"- {end.get('agent_name') or key} ({key}), run {run}, ended {str(end.get('at'))[:16]}, "
            f"started by {start.get('started_by') or '?'}: {end.get('outcome')}, "
            f"{_usd(end.get('cost_usd'))}"
            + (f", {end.get('detail')}" if end.get("detail") else "")
            + (f"; the owner asked this run {asks[run]} follow-up question(s) afterwards, already answered (none waits for them)" if asks[run] else "")
        )
        lines += [
            f"  proposed #{p['id']} ({p['state']}): {p['title']}" for p in made if p["run"] == run
        ]
        said = triggers.last_words(data, run)
        if said:
            lines.append(f"  its last words: {said[:SUMMARY_CHARS]}")
    return lines


async def read_runs(core: Core, cwd: str, args: Mapping[str, Any]) -> str:
    name, here = where(core, cwd, args.get("workspace"))
    asked = str(args.get("agent") or "")
    lines = await asyncio.to_thread(_runs, core, core.ws.key(here), asked)
    head = [
        f"workspace {name}: the last {RUNS_SHOWN} runs of the agents a trigger starts"
        + (f", {asked}'s" if asked else "")
        + ", newest first; each one's page is /run/<workspace>/<run>"
    ]
    return _fit(head, lines or ["- none"])


READS: dict[str, tuple[Callable[..., Any], str, dict[str, Any]]] = {
    "board": (
        read_board,
        "Use instead of guessing or saying you cannot see the project when asked about a "
        "workspace's units: how many, which state, what runs next and why, their cost and PR. "
        "Counts by state, then one line a unit, open ones first. `workspace` is a configured "
        "workspace's name; this chat's when left out.",
        {"type": "object", "properties": _WORKSPACE, "additionalProperties": False},
    ),
    "unit": (
        read_unit,
        "Use instead of board when one unit is named: its stages and status, open questions, "
        "the last review round's findings, its runs with outcome and cost, and its brief. "
        "`name` is its number (`7`) or whole name.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, **_WORKSPACE},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    "needs_you": (
        read_needs_you,
        "Use instead of reading every board when asked what waits on the person: every open "
        "unit in every workspace that waits on them (open questions, a run paused at its "
        "ceiling, a missing input), with why.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "spend": (
        read_spend,
        "Use instead of adding up unit costs when asked about money: today's spend of every "
        "workspace against the daily cap and what is left, then one workspace's last 30 days "
        "by day, by agent, and its costliest units with the stages that cost most.",
        {"type": "object", "properties": _WORKSPACE, "additionalProperties": False},
    ),
    "agents": (
        read_agents,
        "Use instead of answering from memory when asked how agents are set up: each agent's "
        "model, effort, turn and $ ceilings, trigger, on or off in the workspace, and its "
        "last 30 days of runs and cost.",
        {"type": "object", "properties": _WORKSPACE, "additionalProperties": False},
    ),
    "proposals": (
        read_proposals,
        "Use instead of guessing when asked what agents proposed for the Backlog: pending "
        "proposals, newest first, each with its number, agent, run, problem and sources. `agent` "
        "keeps one agent's (its key or name); `include_decided` adds the accepted and dismissed "
        "ones with their reason.",
        {
            "type": "object",
            "properties": {
                **_WORKSPACE,
                "agent": {"type": "string"},
                "include_decided": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    ),
    "runs": (
        read_runs,
        "Use instead of agents or proposals when asked what an agent did or found: its last runs, "
        "newest first, each with who started it, how it ended, its cost, what it proposed and its "
        "last words. A follow-up the owner asked a finished run is already answered, never a question waiting. `agent` keeps one agent's (its key or name).",
        {
            "type": "object",
            "properties": {**_WORKSPACE, "agent": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
}


def read_tools(core: Core, cwd: str) -> list[Any]:
    """`READS` as the chat's MCP tools for workspace `cwd`; a refusal is the tool's error."""
    from claude_agent_sdk import tool

    def handler(read: Callable[..., Any]) -> Callable[[Mapping[str, Any]], Any]:
        async def handle(args: Mapping[str, Any]) -> Reply:
            try:
                return {"content": [{"type": "text", "text": await read(core, cwd, args)}]}
            except Invalid as e:
                return {"content": [{"type": "text", "text": f"refused: {e}"}], "is_error": True}

        return handle

    return [tool(name, said, schema)(handler(read)) for name, (read, said, schema) in READS.items()]
