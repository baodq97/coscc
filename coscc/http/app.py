"""The app: its parts, assembled and wired, and the FastAPI app that serves them.

`Core` builds every part and says who listens to which fact. `build` makes the app: the
routes (`coscc/http/routes.py`, which translate requests into calls on a `Core` and decide
nothing), the features' routes, the studio, and the lifespan. `python -m coscc.http` writes
`ui/src/api.gen.ts`.

A refusal is `Invalid`, answered in one place: 400, or 503 while an update is under way
(`Updating`), or 409 when this install cannot be updated (`NotUpdatable`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from coscc import features
from coscc.agent import pack
from coscc.agent.sessions import Sessions
from coscc.bus import Event
from coscc.config import Config, from_env
from coscc.github.integrate import open_prs_once
from coscc.github.integration import Integration
from coscc.http import plugin, routes, studio
from coscc.kernel import Invalid
from coscc.leif.agents import Agents, Models
from coscc.leif.answers import Answers
from coscc.leif.autopilot import Autopilot, autopilot_values
from coscc.leif.backlog import Backlog
from coscc.leif import chat
from coscc.leif.chat import Chat
from coscc.leif.insights import Activity
from coscc.runner.queue import Attempts, Holds, Updating
from coscc.runner.resume import Resume
from coscc.runner import triggers
from coscc.runner.steps import Steps
from coscc.runner.watch import Watch
from coscc.store.db import Busy, Data, in_thread
from coscc.units.ideas import Ideas
from coscc import units
from coscc.units import proposals, read, states
from coscc.units.read import Asked, Board, Detail
from coscc.units.workspaces import Workspaces
from coscc.update import updater as updater_mod
from coscc.update.updater import (
    NotUpdatable,
    refuse_mechanical_while_updating,
    refuse_while_updating,
)

log = logging.getLogger(__name__)

# How often the triggers are asked for what is due (`triggers.tick`). Chosen, not measured.
TICK_SECONDS = 300.0
# How long `shutdown` waits for what it cancelled. Chosen, not measured: past it a step's
# thread is left running rather than an update held up.
SHUTDOWN_WITHIN = 10.0


@dataclass
class Core:
    config: Config
    sessions: Sessions

    def __post_init__(self) -> None:
        # The owner's layer of the agents' rows lives under this data root.
        pack.ROOT = self.config.data_dir
        # Which workspaces there are, and where each keeps its units.
        self.ws = Workspaces(self.config, self.sessions)
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self.ws.is_member
        # Which sessions are this app's runs, from the run log: resumable after a restart too.
        self.sessions.known = self._known_session
        # Told of every step, integration and chat turn that ends.
        self.updater = updater_mod.Updater(self.config, self)
        self.bus = self.sessions.bus
        # What holds each unit now: its attempt, and the scheduler that launches them.
        self.attempts = Attempts(self.config.data_dir, self.bus, self._capacity)
        self.attempts.admitting = self._admitting
        self.holds = Holds(self.attempts)
        # The parts below `Core`, each given what it reads.
        self.agents = Agents(
            self.config, self.ws, lambda: self.steps.hooks, lambda cwd: self.autopilot.today(cwd)
        )
        self.models = Models(self.config, self.ws)
        self.activity = Activity(self.config, self.ws)
        self.chat = Chat(
            self.config,
            self.ws,
            self.sessions,
            lambda: refuse_while_updating(self.updater),
            self.models.agent,
            lambda cwd, turn: triggers.leif_server(self, cwd, chat.read_tools(self, cwd), turn),
        )
        self.ideas = Ideas(self.config, self.ws)
        self.backlog = Backlog(
            self.config, self.ws, self.holds, self.sessions, self.updater, self.models, self.bus
        )

        # An answer written and a step or an integration ended each schedule an autopilot
        # pass; the autopilot, built after them, is looked up when the event comes.
        self.answers = Answers(
            self.config, self.ws, self.holds, self.agents.agent, self.ideas, self.bus
        )
        self.steps = Steps(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.ideas,
            self.bus,
            agent_of=self.agents.agent,
            stage_config=self.models.stage_config,
            ci_red=self.models.ci_red,
            worktree=self.answers.worktree,
            ingest=self.answers.ingest,
            post_new_rounds=self.answers.post_new_rounds,
            sync_pr=self.answers.sync_pr,
            refuse_updating=lambda: refuse_while_updating(self.updater),
            refuse_mechanical=lambda: refuse_mechanical_while_updating(self.updater),
            identity=self._identity,
        )
        # Above `runner`: a unit's pull request, which runs its attempts on the steps.
        self.integration = Integration(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.steps,
            self.bus,
            config_for=self.models.config_for,
        )
        self.watch = Watch(self.config, self.ws)
        self.boards = Board(
            self.config,
            self.ws,
            self.bus,
            self.attempts.unfinished,
            lambda cwd: open_prs_once(cwd)(),
            self._attach,
        )
        # Each feature's slow reads (`Ctx.asks`), by its name: `ctx_of` makes them.
        self.asks: dict[str, Asked] = {}
        # Each feature's `Feature.stop`, bound to its `Ctx`, by its name: `ctx_of` makes them.
        self.stops: dict[str, Callable[[], Awaitable[None]]] = {}
        self.autopilot = Autopilot(
            self.config,
            self.ws,
            self.holds,
            self.steps,
            self.integration,
            self.boards,
        )
        # Who listens to what. The updater hears every ending; the autopilot hears those that
        # free a unit or leave a person's answer, and not a step that ended because the app
        # is going down.
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "integration.escalated",
            "retake.ended",
            "estimate.ended",
            "estimate.refused",
            "chat-turn.ended",
        ):
            self.bus.subscribe(name, lambda _: self.updater.job_ended())
        for name in (
            "step.ended",
            "step.refused",
            "integration.ended",
            "integration.refused",
            "answer.written",
            "shortlist.saved",
            "hold.moved",
        ):
            self.bus.subscribe(name, self._wake_autopilot)
        self.resume = Resume(
            self.config,
            self.ws,
            self.holds,
            self.sessions,
            self.steps,
            takers={
                "integrate": lambda _cwd, record: self.integration.resume(record),
                "estimate": lambda cwd, record: _drain(
                    self.backlog.propose_estimates(cwd, resume=record)
                ),
                "chat": lambda cwd, record: self._resume_chat(cwd, record),
            },
            refuse_updating=lambda: refuse_while_updating(self.updater),
            finish=self._resumed,
        )

    def _identity(self) -> dict[str, str]:
        """The running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:
            # A record field, never a reason to refuse a step.
            log.exception("the version of the app could not be read")
            return {"version": "", "commit": ""}

    async def _resumed(self) -> None:
        """Once every row is taken up: a merge asked for before the app went down is recorded
        before the autopilot could ask for it again."""
        await self.integration.reconcile_prs()
        self.autopilot.resume()

    async def _resume_chat(self, cwd: str, record: dict[str, Any]) -> None:
        """Nobody is reading this turn now; its reply is in the session, and its `chat`
        row is written as any turn's is."""
        async for _ in self.chat.stream(
            cwd,
            str(record.get("message") or ""),
            str(record.get("session_id") or ""),
            resume=record,
        ):
            pass

    def _capacity(self, workspace: str, slot: str) -> int:
        """Agent sessions at once: the workspace's `max_parallel`. Heavy work: one."""
        if slot == "agent":
            return int(autopilot_values(self.config, workspace)["max_parallel"])
        return 1

    def _known_session(self, session_id: str) -> bool:
        """Whether an `end` or a `suspend` row of the run log names `session_id`; a run log that
        cannot be read now names none."""
        journal = self.ws.journal()
        if journal is None:
            return False
        try:
            return bool(journal.where("session_id", session_id, ("end", "suspend")))
        except Busy, OSError, sqlite3.Error:
            log.warning("the run log could not be read for session %s", session_id)
            return False

    def _admitting(self) -> bool:
        """No queued attempt is moved on from the press of Apply until the update goes no
        further: what began then would be refused `updating`, or cut by the hand-off."""
        return not self.updater.window and self.updater.state not in ("pending", "applying")

    def update_over(self) -> None:
        """Told by the updater once a cancel or a failure left it idle: the queue moves on."""
        self.attempts.wake_all()

    def _wake_autopilot(self, event: Event) -> None:
        if not event.payload.get("going_down"):
            self.autopilot.nudge(event.payload.get("workspace", ""))

    async def _attach(self, cwd, data, journal, key, prs, fresh) -> Callable[[], None]:
        """What the board read adds from above `units`: each unit's integration. Returns what
        starts the CI asks that found no answer, which the read calls last."""
        asks = await self.integration.attach_integration(
            cwd, data["units"], journal, key, prs, fresh
        )
        return lambda: self.integration.ask_ci(
            asks, ended=lambda tree: self.boards.changed((tree,))
        )

    async def board(
        self, cwd: str, which: Literal["new", "held", "next"] = "new"
    ) -> dict[str, Any]:
        """The board of `cwd` (`Board.get`) with what the autopilot shows on it."""
        data = await self.boards.get(cwd, which)
        self.autopilot.show(self.ws.key(cwd), data)
        return data

    async def unit(self, cwd: str, name: str) -> Detail:
        """One unit as its page shows it (`read.detail`), from the board held."""
        board = await self.boards.get(cwd, "held")
        unit = next((u for u in board.get("units") or [] if u.get("name") == name), None)
        if unit is None:
            raise Invalid(f"no unit {name} in {cwd}")
        key, meta = self.ws.key(cwd), self.ws.unit_meta()
        journal = self.ws.journal()
        timeline = await in_thread(journal.timeline, key, name) if journal else []
        outputs = await in_thread(meta.outputs, key, name)
        decisions = await in_thread(meta.decisions, key, name)
        graded = await in_thread(meta.graded, key, name)
        found = read.grader()
        shown = None
        if found is not None:
            verdict = await in_thread(meta.verdict, key, name)
            made = await in_thread(proposals.listed, Data(self.config.data_dir), key, found[0])
            shown = read.outcome(*found, verdict, [p for p in made if p["unit"] == name])
        idea = units.unit_dir(cwd, name, self.config.data_dir) / states.brief_file()
        brief = await in_thread(idea.read_text, "utf-8") if idea.is_file() else ""
        made = await in_thread(proposals.origin, Data(self.config.data_dir), key, name)
        origin = None
        if made is not None:
            row = pack.row(made["agent"]) or {}
            origin = read.Origin(
                id=made["id"],
                title=made["title"],
                agent=made["agent"],
                name=str(row.get("name") or made["agent"]),
                run=made["run"],
            )
        return read.detail(unit, timeline, outputs, decisions, graded, shown, brief, origin)

    def _asks(self) -> list[tuple[str, asyncio.Task]]:
        """The background `gh` asks running now: CI, the board's and each feature's."""
        return [
            *((f"CI ask of {u} in {ws}", t) for (ws, u), t in self.integration.ci_asks.items()),
            *((f"gh ask for {' '.join(k)}", t) for k, t in self.boards.prs.asks.items()),
            *(
                (f"{name} ask for {' '.join(k)}", t)
                for name, asked in self.asks.items()
                for k, t in asked.asks.items()
            ),
        ]

    async def shutdown(self) -> None:
        """Cancel every triggered agent run, autopilot pass, step, board read and background `gh`
        ask still running, let every tree removal end as it would, and wait for all of them,
        `SHUTDOWN_WITHIN` seconds at most from the call (a triggered run before the clock). An ask a request begins meanwhile is cancelled and
        waited for too. Once this returns nothing they started still writes, unless it
        outlived the deadline: each such one is logged by name.

        No `end` is written: a step with no `end` is what an app that went down mid-step looks like.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SHUTDOWN_WITHIN
        # Every agent run a trigger started: each writes its `end` once cancelled.
        await triggers.stop()
        # The queue first: an integration waited for below frees its slot, and what is queued
        # stays queued for the next start rather than begin in a process going down.
        self.attempts.closed = True
        # The autopilot next, so no pass starts a step while the rest go down. A pass may be
        # in a board read's thread, so it is waited for too; taken before `stop` drops it.
        autopilot = [
            *((f"autopilot of {k}", t) for k, t in self.autopilot.tasks.items()),
            *((f"pull request reader of {k}", t) for k, t in self.autopilot.pr_readers.items()),
            *(("autopilot pass", t) for t in self.autopilot.pending),
        ]
        for key in list(self.autopilot.tasks):
            self.autopilot.stop(key)
        for t in list(self.autopilot.pending):
            t.cancel()
        # A CI ask and a held `gh` answer hold nothing worth keeping, but their `gh` is reaped.
        cancelled = [*autopilot, *self._asks()]
        # An integration is waited for, not cancelled: it stops between two `git`s or not at all.
        integrations = [
            (f"integration of {r.unit} in {r.workspace}", r.task)
            for r in self.steps.tasks.values()
            if r.stage == "integrate" and r.task is not None and not r.task.done()
        ]
        steps = [
            (f"{r.stage} step of {r.unit} in {r.workspace}", r.task)
            for r in self.steps.tasks.values()
            if r.stage != "integrate" and r.task is not None and not r.task.done()
        ]
        # A step's task in its attempt's `ending`, still in its `after_end` (`holds.finishing`).
        steps += [
            (f"after-end of {entry['unit']} in {entry['workspace']}", t)
            for entry, t in self.holds.finishing.values()
            if not t.done() and t not in [s for _label, s in steps]
        ]
        cancelled += steps
        for _label, t in cancelled:
            t.cancel()
        # Each feature cancels and waits for its own work.
        stopping = [
            (f"{name} feature", asyncio.ensure_future(s())) for name, s in self.stops.items()
        ]
        # Board reads are cancelled there, and tree removals left to end.
        waited = cancelled + integrations + stopping + await self.boards.stop()
        while True:
            left = {t for _label, t in waited if not t.done()}
            if left:
                await asyncio.wait(left, timeout=max(0.0, deadline - loop.time()))
            # Only the board has a door: an ask a request began meanwhile is cancelled here.
            seen = {t for _label, t in waited}
            late = [(label, t) for label, t in self._asks() if t not in seen and not t.done()]
            for _label, t in late:
                t.cancel()
            waited += late
            if not late or loop.time() >= deadline:
                break
        late_integrations = [t for _label, t in integrations if not t.done()]
        for t in late_integrations:
            # Past the deadline: its `git` is killed rather than an update held up.
            t.cancel()
        if late_integrations:
            await asyncio.wait(late_integrations, timeout=1.0)
        for label, t in waited:
            if not t.done():
                log.warning(
                    "shutdown returns with the %s still running after %gs", label, SHUTDOWN_WITHIN
                )


async def _drain(agen: AsyncIterator[Any]) -> None:
    async for _ in agen:
        pass


async def _refused(_: Request, e: Exception) -> JSONResponse:
    """`error`, its words; a gate's refusal adds its `code` (`guards.REASONS`, the first) and every
    one of its `reasons`, for a caller to branch on; a pack's refusal its own `code` and every
    reason in words."""
    status = 503 if isinstance(e, Updating) else 409 if isinstance(e, NotUpdatable) else 400
    reasons = list(getattr(e, "reasons", ()) or ())
    code = getattr(e, "code", "") or (reasons[0] if reasons else "")
    coded = {"code": code, "reasons": reasons} if reasons else {}
    return JSONResponse({"error": str(e), **coded}, status_code=status)


async def _gone(_: Request, _e: Exception) -> Response:
    """A caller that left while its body was read: nothing to answer and nothing wrong here, so
    no 500 and no traceback; 499 is what a proxy logs for it."""
    return Response(status_code=499)


def build(config: Config | None = None, *, starting: bool = False) -> FastAPI:
    """The app. `starting` is the served one (`run.served`): its start makes the features'
    tables, takes up what an update paused, reads every board once and runs the schedules."""
    config = config or from_env()
    sessions = Sessions(config)
    core = Core(config, sessions)

    async def schedules() -> None:
        # The first round waits one period, so a start spends nothing at once.
        while True:
            await asyncio.sleep(TICK_SECONDS)
            await triggers.tick(core)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task] = []
        if starting:
            plugin.create_tables(Data(config.data_dir), tables)
            work = (core.resume.resume_after_update(), core.boards.warm(), schedules())
            tasks = [asyncio.create_task(w) for w in work]
        yield
        for task in tasks:
            task.cancel()
        # Steps first: each is a task that would otherwise write its `end` after its client
        # was closed. `shutdown` writes none, on purpose.
        await core.shutdown()
        await sessions.close_all()

    # The routes themselves, not `include_router`, which keeps them behind one entry of `routes`.
    # Read now, so a test can patch `features.FEATURES`.
    ctxs = {f.name: plugin.ctx_of(core, f) for f in features.FEATURES}
    plugin.check_declarations()
    triggers.listen(core)
    core.steps.hooks = plugin.hooks_of(features.FEATURES, ctxs)
    # Checked now, created when the app starts: `typescript()` and the tests build an app
    # that never opens the database.
    tables = plugin.tables_of(features.FEATURES)
    # The studio last: it answers every path no route took.
    served = [
        *routes.router.routes,
        *(r for f in features.FEATURES for r in f.routes(ctxs[f.name])),
        *studio.router.routes,
    ]
    # No `/docs` or `/openapi.json`: the studio answers those paths; `typescript()` reads the schema.
    api = FastAPI(
        title="coscc",
        lifespan=lifespan,
        routes=served,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    api.state.config = config
    api.state.sessions = sessions
    api.state.core = core
    api.state.tables = tables
    # The names for Settings: only this module imports `features`.
    api.state.features = tuple(f.name for f in features.FEATURES)
    # Each feature's `Ctx` and the plugins themselves, for the Settings panel's states.
    api.state.ctxs = ctxs
    api.state.plugins = features.FEATURES
    api.add_exception_handler(Invalid, _refused)
    api.add_exception_handler(ClientDisconnect, _gone)
    return api


def _ts(schema: dict[str, Any]) -> str:
    """One OpenAPI schema as a TypeScript type."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "anyOf" in schema:
        return " | ".join(_ts(s) for s in schema["anyOf"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        item = _ts(schema.get("items") or {})
        return f"({item})[]" if "|" in item else f"{item}[]"
    if kind == "object":
        if "properties" not in schema:
            extra = schema.get("additionalProperties")
            return f"Record<string, {_ts(extra) if isinstance(extra, dict) else 'unknown'}>"
        need = set(schema.get("required") or ())
        fields = (
            f"  {json.dumps(k)}{'' if k in need else '?'}: {_ts(v)};"
            for k, v in schema["properties"].items()
        )
        return "{\n" + "\n".join(fields) + "\n}"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(kind), "unknown")


def typescript() -> str:
    """The routes' shapes as TypeScript, which `ui/src/api.gen.ts` holds: each schema a type,
    and `Get` the answer of each `GET` route that names one. `tests/test_api_types.py` fails
    when the file is stale; `uv run python -m coscc.http > ui/src/api.gen.ts` writes it."""
    import tempfile

    with tempfile.TemporaryDirectory() as data:
        schema = build(Config(workspaces=(), data_dir=data)).openapi()
    out = [
        "// Made by `uv run python -m coscc.http > ui/src/api.gen.ts` from the app's routes. Do not edit.",
        "",
    ]
    for name, s in sorted(schema.get("components", {}).get("schemas", {}).items()):
        if name not in ("HTTPValidationError", "ValidationError"):
            out += [f"export type {name} = {_ts(s)};", ""]
    gets = []
    for path, ops in sorted(schema["paths"].items()):
        ok = ops.get("get", {}).get("responses", {}).get("200", {})
        answer = ok.get("content", {}).get("application/json", {}).get("schema", {})
        if "$ref" in answer or answer.get("type") == "array" or "anyOf" in answer:
            gets.append(f"  {json.dumps(path)}: {_ts(answer)};")
    out += ["export type Get = {", *gets, "};", ""]
    return "\n".join(out)
