"""Ask a run: a person's question about one run, answered by a read-only follow-up run.

A follow-up is a run of stage `ask`, its `agent` the asked run's and its `parent_run` the run
asked; a follow-up's own follow-ups hang on that same run, its thread. It never writes and never
hands back an object: its answer is its reply.

A triggered row's run is resumed, its prompt cache still warm, when the thread's last session
ended under `WINDOW` ago with the same `row_hash`, grant, head and model and its transcript is
still there; its tools are offered as before (the cache holds them) and its grant loses `submit`.
Otherwise, and for every other run (a stage step, an estimate: their sessions could write), a
new session gets the run's outcome, last words and the end of its transcript, with Read, Grep
and Glob only; a later question resumes that one under the same rules. All it decides from is in
the run log, so a restart changes nothing. Its `end` holds its own cost (`spent_before`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

from coscc import kernel
from coscc.agent import pack, policy
from coscc.agent import sessions as sessions_mod
from coscc.agent.policy import Row
from coscc.runner import run as run_mod
from coscc.runner import triggers
from coscc.runner.queue import Refused
from coscc.store.db import Busy, Data
from coscc.units import Invalid, proposals, submit

# The app's `Core` (`coscc/http/app.py`), a layer above: its parts are read by name.
Core = Any

STAGE = "ask"
# A session older than this is started afresh: the hour-long prompt cache (`cache_hour`) is cold
# by then, and a resume would write it again at full price.
WINDOW = timedelta(minutes=55)
# A follow-up's ceilings: a few reads and an answer.
TURNS = 3
USD = 0.50
TEXT_MAX = 4_000
# Characters of the run's transcript a fresh follow-up reads, and of its last words.
EXCERPT = 6_000
LAST_WORDS = 2_000
# Characters of an answer the thread shows; its log holds all of it.
ANSWER = 20_000

ASK_SYSTEM = (
    "You answer a person's question about an earlier run of an agent of this app. You read; you "
    "change nothing. Answer briefly, in the person's language, citing what the run read or ran."
)
RESUMED = (
    "# A follow-up question\n\nThe person asks about the run you just did. Answer in words, "
    "briefly, citing what you read or ran. Read again if you must; change nothing, and do not "
    "call submit: this answer is not an output.\n\n"
)

# The threads with a follow-up going now, by their run.
_ASKING: set[str] = set()


class Followup(TypedDict):
    run: str
    at: str
    question: str
    outcome: str
    cost_usd: float | None
    resumed: bool
    why: str
    cache_read_tokens: int
    cache_creation_tokens: int
    answer: str


class AskState(TypedDict):
    """Whether a question may be asked now, and if so whether it resumes (else `why` not)."""

    may: bool
    resume: bool
    why: str


class Thread(TypedDict):
    run: str
    followups: list[Followup]
    ask: AskState


class Asked(TypedDict):
    run: str
    resumed: bool
    why: str


@dataclass
class _Plan:
    """How one follow-up runs: who, where, with what, and from which session."""

    agent: run_mod.Agent
    tree: str
    triggered: bool
    grant_now: dict[str, Any]
    servers: dict[str, Any] = field(default_factory=dict)
    mcp: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    session: str = ""
    # The run that opened `session`, whose data root it is resumed in (`run.Input.scratch_as`).
    scratch_as: str = ""
    why: str = ""


def _journal(core: Core) -> Any:
    journal = core.ws.journal()
    if journal is None:
        raise Refused("no working folder is set, so no run can be read", ("no-run-log",))
    return journal


def _run_records(journal: Any, run: str) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    """The `start` and `end` of `run` (`end` `None` while it goes); refused `no-run`."""
    try:
        found = journal.where("run", run)
    except Busy as e:
        raise Refused(str(e), ("unavailable",)) from e
    start = next((r for r in found if r.get("kind") == "start"), None)
    if start is None:
        raise Refused(f"no run {run} in this app's run log", ("no-run",))
    return start, next((r for r in found if r.get("kind") == "end"), None)


def root(core: Core, cwd: str, run: str) -> tuple[str, Mapping[str, Any], Mapping[str, Any] | None]:
    """The run a question about `run` is asked of (a follow-up's own `parent_run`), its `start`
    and `end`; refused unless it is a run of the workspace `cwd` a follow-up may ask."""
    ws = core.ws.key(core.ws.check(cwd))
    journal = _journal(core)
    start, end = _run_records(journal, run)
    if start.get("parent_run"):
        run = str(start["parent_run"])
        start, end = _run_records(journal, run)
    if start.get("workspace") != ws:
        raise Refused(f"no run {run} in this workspace", ("no-run",))
    if start.get("stage") == "chat":
        raise Refused("a conversation with Leif goes on in Talk", ("no-run",))
    return run, start, end


def _thread(journal: Any, run: str) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """Each follow-up of `run`, oldest first: its `start` and its `end` (`None` while it goes)."""
    ends = {r.get("run"): r for r in journal.where("parent_run", run, ("end",))}
    return [(s, ends.get(s.get("run"))) for s in journal.where("parent_run", run, ("start",))]


def _plan(core: Core, cwd: str, start: Mapping[str, Any], serve: bool = True) -> _Plan:
    """The follow-up's agent and grant: a triggered row's own, minus `submit`, with its servers
    as before (built only when `serve`: a look at the thread needs none); any other run's a
    reader of its tree."""
    key = str(start.get("agent") or start.get("stage") or "")
    tree = str((start.get("grants") or {}).get("cwd") or "")
    if not tree or not Path(tree).is_dir():
        tree = cwd
    found = pack.row(key)
    if start.get("trigger"):
        if found is None or pack.problems(key, triggers.effects(core.steps.hooks)):
            raise Refused(f"{key} cannot run now: its row is gone or broken", ("agent-invalid",))
        row = policy.row_for(key)
        unit = str(start.get("unit") or "")
        directory = core.ws.unit_dir(cwd, unit) if unit else None
        ws = core.ws.key(cwd)
        tools = triggers.feature_tools(core, key, row, cwd, ws, unit, tree, directory)
        mcp = kernel.granted(tuple(t for t, _ in tools))
        builtin = {t.name for t in kernel.BUILTINS}
        features = tuple(t for t in row.tools if t not in builtin)
        servers = {t.server: t.make(f) for t, f in tools if t.make is not None and serve}
        if row.submits and serve:
            # Its `submit` is still offered, so the tool list the cache holds is the same; the
            # grant below no longer holds it, so every call is refused.
            servers[submit.SERVER] = submit.Collector(
                key, triggers.effects(core.steps.hooks)
            ).server()
        now = run_mod.issue(row, core.sessions, cwd=tree, mcp=mcp, features=features)
        asked = replace(
            row,
            submits=False,
            max_turns=TURNS,
            max_budget_usd=min(row.max_budget_usd or USD, USD),
        )
        return _Plan(
            agent=replace(core.models.agent(key, row), row=asked),
            tree=tree,
            triggered=True,
            grant_now=policy.record(now),
            servers=servers,
            mcp=mcp,
            features=features,
        )
    row = Row(tools=policy.READ_TOOLS, max_turns=TURNS, max_budget_usd=USD)
    model = start.get("model")
    agent = run_mod.Agent(
        key, row, model=str(model) if model else None, system=ASK_SYSTEM, name=f"{key} ({STAGE})"
    )
    now = run_mod.issue(row, core.sessions, cwd=tree)
    return _Plan(agent=agent, tree=tree, triggered=False, grant_now=policy.record(now))


def _age(at: str) -> timedelta:
    try:
        then = datetime.fromisoformat(at)
    except ValueError:
        return timedelta.max
    return datetime.now(timezone.utc) - then


async def _decide(
    core: Core,
    journal: Any,
    run: str,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    plan: _Plan,
    head: str | None,
) -> None:
    """Sets `plan.session` to the thread's last session when it may be resumed, else
    `plan.why`, the reason a new session is opened. `head` `None` leaves the code it read
    unasked (a look at the thread, every 2 s while one is answered: no git)."""

    # A session stopped mid-turn is not gone on in: what it did last may be half done.
    def kept(e: Mapping[str, Any]) -> bool:
        return bool(e.get("session_id")) and e.get("outcome") != "cancelled"

    last: tuple[Mapping[str, Any], Mapping[str, Any]] | None = (
        (start, end) if plan.triggered and kept(end) else None
    )
    for s, e in _thread(journal, run):
        if e is not None and kept(e) and bool(s.get("triggered")) == plan.triggered:
            last = (s, e)
    if last is None:
        plan.why = (
            "its session could write, so a reader starts afresh"
            if not plan.triggered
            else "it was stopped before it ended"
            if end.get("outcome") == "cancelled"
            else "it kept no session"
        )
        return
    base, ended = last
    session = str(ended["session_id"])
    granted = base.get("row_grants") if base.get("parent_run") else base.get("grants")
    model = run_mod.model_of(run_mod.Ctx(core.sessions, None), plan.agent)
    if _age(str(ended.get("at") or "")) >= WINDOW:
        plan.why = f"its session ended over {int(WINDOW.total_seconds() // 60)} min ago"
    elif base.get("row_hash") != pack.stamp(plan.agent.key).get("row_hash"):
        plan.why = "the agent was edited since"
    elif granted != plan.grant_now:
        plan.why = "its tools or grant changed since"
    elif head is not None and str(base.get("head") or "") != head:
        plan.why = "the code it read moved since"
    elif (base.get("model") or None) != model:
        plan.why = "its model changed since"
    elif not await asyncio.to_thread(sessions_mod.exists, session, plan.tree):
        plan.why = "its transcript is gone"
    else:
        plan.session = session
        plan.scratch_as = str(base.get("scratch_as") or "")


def _check(
    core: Core, cwd: str, run: str, text: str = ""
) -> tuple[str, Mapping[str, Any], Mapping[str, Any], Any]:
    """Everything refused before spend: the run, a follow-up already going, an update, the cap."""
    if len(text) > TEXT_MAX:
        raise Invalid(f"a question is at most {TEXT_MAX} characters")
    run, start, end = root(core, cwd, run)
    if end is None or run in run_mod.LIVE:
        raise Refused("the run is still going: ask once it has ended", ("unit-busy",))
    if run in _ASKING:
        raise Refused("a question about this run is being answered", ("unit-busy",))
    core.steps.refuse_updating()
    today = core.autopilot.today(cwd)
    if today is None:
        raise Refused("the daily spend cannot be read now", ("unavailable",))
    if today[0] + USD > today[1]:
        raise Refused(
            f"the daily cap of ${today[1]:.2f} would pass (${today[0]:.2f} spent)",
            ("budget-reached",),
        )
    return run, start, end, _journal(core)


async def state(core: Core, cwd: str, run: str) -> Thread:
    """The thread of `run`: its follow-ups, and whether a question may be asked now."""
    try:
        root_run, start, end = root(core, cwd, run)
    except Invalid as e:
        return Thread(run=run, followups=[], ask=AskState(may=False, resume=False, why=str(e)))
    journal = _journal(core)
    data = Data(core.config.data_dir)
    shown: list[Followup] = []
    for s, e in await asyncio.to_thread(_thread, journal, root_run):
        shown.append(
            Followup(
                run=str(s.get("run") or ""),
                at=str(s.get("at") or ""),
                question=str(s.get("question") or ""),
                outcome=str((e or {}).get("outcome") or "running"),
                cost_usd=(e or {}).get("cost_usd"),
                resumed=bool(s.get("resumed")),
                why=str(s.get("fresh_why") or ""),
                cache_read_tokens=int((e or {}).get("cache_read_tokens") or 0),
                cache_creation_tokens=int((e or {}).get("cache_creation_tokens") or 0),
                answer=last_words(data, str(s.get("run") or ""), ANSWER) if e else "",
            )
        )
    try:
        _check(core, cwd, root_run)
        plan = _plan(core, cwd, start, serve=False)
        await _decide(core, journal, root_run, start, end or {}, plan, None)
    except Invalid as e:
        return Thread(
            run=root_run, followups=shown, ask=AskState(may=False, resume=False, why=str(e))
        )
    return Thread(
        run=root_run,
        followups=shown,
        ask=AskState(may=True, resume=bool(plan.session), why=plan.why),
    )


def last_words(data: Data, run: str, limit: int = LAST_WORDS) -> str:
    """The last thing `run`'s agent said, as its events kept it, cut at `limit` characters."""
    try:
        events, _ = data.step_events_page(run, None, 40)
    except Busy:
        return ""
    said = [e for e in events if e.get("kind") == "text" and e.get("role") != "user"]
    return str(said[-1].get("text") or "")[:limit] if said else ""


def _numbered(core: Core, start: Mapping[str, Any]) -> str:
    """The numbers the owner sees the run's proposals under, which the run never saw."""
    data = Data(core.config.data_dir)
    run = str(start.get("run") or "")
    made = [p for p in proposals.listed(data, str(start.get("workspace"))) if p["run"] == run]
    if not made:
        return ""
    return "The app kept what you proposed as:\n" + "\n".join(
        f"- #{p['id']} {p['title']}" for p in made
    )


def _summary(core: Core, start: Mapping[str, Any], end: Mapping[str, Any], tree: str) -> str:
    """What a fresh follow-up is told of the run: who, how it ended, what it made, its last
    words, and the end of its transcript."""
    data = Data(core.config.data_dir)
    run = str(start.get("run") or "")
    cost = end.get("cost_usd")
    lines = [
        f"Agent: {start.get('agent_name') or start.get('agent') or start.get('stage')}.",
        f"Unit: {start.get('unit')}." if start.get("unit") else "No unit: a run of the workspace.",
        f"Started {start.get('at')} by {start.get('started_by')}; ended {end.get('outcome')}"
        + (f", ${float(cost):.2f}" if cost is not None else "")
        + (f": {end.get('detail')}" if end.get("detail") else "."),
    ]
    made = [p for p in proposals.listed(data, str(start.get("workspace"))) if p["run"] == run]
    if made:
        lines.append("It proposed:")
        lines += [f"- #{p['id']} {p['title']}: {p['problem'][:300]}" for p in made]
    parts = ["# The run you are asked about\n\n" + "\n".join(lines)]
    if said := last_words(data, run):
        parts.append(f"# Its last words\n\n{said}")
    session = str(end.get("session_id") or "")
    if session:
        try:
            excerpt, _ = sessions_mod.transcript_excerpt(session, tree, EXCERPT)
        except Exception:  # noqa: BLE001 - a transcript gone or unreadable only leaves it out
            excerpt = ""
        if excerpt.strip():
            parts.append(f"# The end of what it did\n\n{excerpt}")
    return "\n\n".join(parts)


def ask(core: Core, cwd: str, run: str, text: str) -> asyncio.Future[Asked]:
    """Refused before spend (`_check`), else the follow-up in the background: what it is and
    whether it resumes, once that is known (a git read). `stop` and `triggers.stop_run` reach it."""
    if not text.strip():
        raise Invalid("ask a question")
    root_run, start, end, journal = _check(core, cwd, run, text)
    plan = _plan(core, cwd, start)
    _ASKING.add(root_run)
    loop = asyncio.get_running_loop()
    told: asyncio.Future[Asked] = loop.create_future()
    run_id = uuid.uuid4().hex
    triggers.spawn(
        loop,
        _ask(core, cwd, journal, root_run, start, end, plan, text.strip(), run_id, told),
        run_id,
        str(start.get("workspace") or ""),
    )
    return told


async def _ask(
    core: Core,
    cwd: str,
    journal: Any,
    root_run: str,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    plan: _Plan,
    text: str,
    run_id: str,
    told: asyncio.Future[Asked],
) -> None:
    try:
        head = await triggers.tree_head(plan.tree)
        await _decide(core, journal, root_run, start, end, plan, head)
        if plan.session:
            kept = await asyncio.to_thread(_numbered, core, start)
            prompt = RESUMED + (f"{kept}\n\n" if kept else "") + text
        else:
            summary = await asyncio.to_thread(_summary, core, start, end, plan.tree)
            prompt = f"{summary}\n\n# The question\n\n{text}"
        spent = (
            await asyncio.to_thread(journal.session_cost, plan.session) if plan.session else None
        )
        if not told.done():
            told.set_result(Asked(run=run_id, resumed=bool(plan.session), why=plan.why))

        async def finish(_got: kernel.Run) -> Mapping[str, Any]:
            return {"parent_run": root_run, "resumed": bool(plan.session)}

        stream = run_mod.run(
            plan.agent,
            run_mod.Input(
                plan.tree,
                prompt,
                str(start.get("workspace") or ""),
                workspace_dir=cwd,
                stage=STAGE,
                started_by="manual",
                start={
                    "parent_run": root_run,
                    "question": text,
                    "resumed": bool(plan.session),
                    "triggered": plan.triggered,
                    "row_grants": plan.grant_now,
                    **({"fresh_why": plan.why} if plan.why else {}),
                    **({"head": head} if head else {}),
                },
                session_id=plan.session or None,
                servers=plan.servers,
                mcp=plan.mcp,
                features=plan.features,
                run=run_id,
                spent_before=spent,
                cache_hour=plan.triggered,
                scratch_as=plan.scratch_as,
            ),
            ctx=run_mod.Ctx(core.sessions, journal, core.config.data_dir),
            finish=finish,
        )
        try:
            async for _ in stream:
                pass
        finally:
            await stream.aclose()
    except BaseException as e:
        if not told.done():
            told.set_exception(
                e if isinstance(e, Exception) else Invalid("the question was stopped")
            )
        raise
    finally:
        _ASKING.discard(root_run)
