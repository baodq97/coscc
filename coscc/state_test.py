"""Tests for the page's state, and one guard for a failure that is invisible when it hits.

Reflex refuses an assignment to a var that was never declared, and it refuses it **at the
moment the handler runs** — on the server, inside an event, where the only symptom is that
the browser receives nothing. Measured 2026-09-22 while building `0014`'s controls: the
handler created a work unit, then assigned `self.unit` (the var is called `unit_id`), and
the exception killed the event after the side effect. The unit existed and the page looked
as though the button had done nothing, so the next thing a person does is press it again
and get a second unit.

That is why this is an `ast` walk rather than a click test: the class of bug is "a handler
touches a name that is not there", and it costs nothing to find every one of them at once.
"""

from __future__ import annotations

import ast
import re
import unittest

from coscc import present
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "state.py"


def _split_from(name: str) -> list[Path]:
    """`0095`: `<name>.py` and the `<name>_*.py` modules it was split into, tests left out."""
    here = Path(__file__).resolve().parent
    return [here / f"{name}.py"] + [
        p for p in sorted(here.glob(f"{name}_*.py")) if not p.name.endswith("_test.py")
    ]


def _source_text(name: str = "state") -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _split_from(name))

# Names assigned on `self` that are not page state. Kept short and explicit: anything added
# here stops being checked, so each entry should be something that is plainly not a var.
NOT_A_VAR = {"membership"}


def _state_class(tree: ast.Module) -> ast.ClassDef:
    """`StudioState` as one class: its own body, then the body of every `rx.State` mixin it
    inherits from a `state_*.py` module (`0095`), so a walk sees every var and handler."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StudioState":
            break
    else:
        raise AssertionError("StudioState is not in state.py any more")
    body = list(node.body)
    for path in _split_from("state")[1:]:
        for other in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
            if isinstance(other, ast.ClassDef) and any(
                k.arg == "mixin" for k in other.keywords
            ):
                body += other.body
    whole = ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords, body=body,
                         decorator_list=node.decorator_list, type_params=[])
    return ast.copy_location(whole, node)


class EveryVarAHandlerSetsIsDeclared(unittest.TestCase):
    def test_no_handler_assigns_a_name_the_state_never_declared(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)

        declared = {
            node.target.id
            for node in state.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        # Computed vars are read-only, but a handler assigning one would be just as wrong,
        # so they count as known names rather than as targets.
        declared |= {
            node.name
            for node in state.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("unit_id", declared, "the fixture for this test has moved")

        unknown = []
        for node in ast.walk(state):
            if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _self_names(target):
                    if name not in declared and name not in NOT_A_VAR:
                        unknown.append(f"state.py:{node.lineno}: self.{name}")
        self.assertEqual(
            unknown,
            [],
            "these assign a state var that was never declared, which Reflex refuses at "
            "run time inside the event — the browser just receives nothing:\n  "
            + "\n  ".join(unknown),
        )


class PostingARoundLocksTheButtonWhileItRuns(unittest.TestCase):
    """`0021` review F1. Reflex sends state to the browser only at a `yield` or at the end of
    the handler, so a busy flag raised and cleared with no `yield` between never arrives:
    the *Post to PR* button would not lock during the up to 60s `gh` may take."""

    def test_a_yield_follows_raising_posting_round_before_the_service_is_awaited(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        [handler] = [
            n
            for n in state.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_review_comment"
        ]
        body = handler.body
        raised = next(
            i
            for i, stmt in enumerate(body)
            if isinstance(stmt, ast.Assign)
            and "posting_round" in [name for t in stmt.targets for name in _self_names(t)]
        )
        after = body[raised + 1]
        self.assertTrue(
            isinstance(after, ast.Expr) and isinstance(after.value, ast.Yield),
            f"state.py:{after.lineno}: the statement after raising posting_round is not "
            "a bare `yield`, so the browser never sees the button locked",
        )

    def test_a_second_press_while_one_is_running_does_nothing(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        [handler] = [
            n
            for n in state.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_review_comment"
        ]
        # `0095`: a mixin's handler imports `SERVICE` in its body first; an import does nothing.
        first = next(s for s in handler.body
                     if not isinstance(s, (ast.Expr, ast.ImportFrom)) or isinstance(s, ast.Expr) and not isinstance(s.value, ast.Constant))
        self.assertIsInstance(first, ast.If)
        self.assertIn("posting_round", ast.unparse(first.test))
        self.assertIsInstance(first.body[0], ast.Return)


class ThePageHoldsNoRunningFlagOfItsOwn(unittest.TestCase):
    """`0034` R6, R11, R12. Which units are running is the service's list; whether a unit
    may start, or be stopped, is the service's to refuse."""

    def setUp(self):
        self.tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.state = _state_class(self.tree)
        self.methods = {
            n.name: n for n in self.state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_there_is_no_single_running_attribute(self):
        fields = {
            t.target.id for t in self.state.body
            if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)
        }
        self.assertNotIn("running", fields)
        self.assertIn("running_steps", fields)
        self.assertNotIn("is_running", self.methods)

    def test_stop_step_asks_the_service(self):
        text = ast.unparse(self.methods["stop_step"])
        self.assertIn("SERVICE.stop_step(", text)

    def test_run_step_has_no_already_running_check_of_its_own(self):
        text = ast.unparse(self.methods["run_step"])
        self.assertNotIn("already running", text)
        self.assertNotIn("self.running ", text)
        self.assertNotIn("self.running:", text)

    def test_the_list_comes_from_the_service(self):
        text = ast.unparse(self.methods["_load_running"])
        self.assertIn("SERVICE.running_steps(", text)


class TheRunButtonHoldsNoCopyOfTheLoop(unittest.TestCase):
    """`0024` R1. The stage the button offers is `cos.mjs next`'s, copied; nothing in the
    page works it out from which artifacts exist."""

    def setUp(self):
        self.tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        self.state = _state_class(self.tree)
        self.methods = {
            n.name: n for n in self.state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_the_old_rule_is_gone_by_name(self):
        names = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        names |= {n.name for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("_next_required", names)

    def test_next_stage_only_hands_back_run_stage(self):
        body = [s for s in self.methods["next_stage"].body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        self.assertEqual(len(body), 1)
        self.assertIsInstance(body[0], ast.Return)
        self.assertEqual(ast.unparse(body[0].value), "self.run_stage")

    def test_only_load_next_sets_run_stage_and_it_takes_it_from_run_target(self):
        setters = set()
        for name, fn in self.methods.items():
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and any(
                    "run_stage" in _self_names(t) for t in node.targets
                ):
                    setters.add(name)
        self.assertEqual(setters, {"load_next"})
        calls = [n for n in ast.walk(self.methods["load_next"])
                 if isinstance(n, ast.Call) and ast.unparse(n.func) == "_run_target"]
        self.assertEqual(len(calls), 1)
        self.assertIn("SERVICE.next_step", ast.unparse(calls[0]))

    def test_load_next_runs_in_the_background(self):
        decorators = [ast.unparse(d) for d in self.methods["load_next"].decorator_list]
        self.assertIn("rx.event(background=True)", decorators)

    def test_run_step_and_set_mode_run_the_stage_next_stage_names(self):
        for name in ("run_step", "set_mode"):
            with self.subTest(handler=name):
                self.assertIn("self.next_stage", ast.unparse(self.methods[name]))

    def test_opening_a_unit_and_ending_a_step_both_ask_again(self):
        # `0056`: a unit is opened by arriving at its address, so `arrive` is what asks.
        for name, said in (("arrive", "yield StudioState.load_next"),
                           ("run_step", "return StudioState.load_next")):
            with self.subTest(handler=name):
                self.assertIn(said, ast.unparse(self.methods[name]))


class ACardShowsWhatServiceRunningSaid(unittest.TestCase):
    """`0051` plan step 6: `_activities` copies, and only `poll_running` feeds it."""

    READ = {
        "running": {
            "0009_x": [{"kind": "step", "stage": "impl", "agent": {"glyph": "ᚢ", "name": "Uruz"},
                        "started": "2026-09-24T01:00:00+00:00", "turns": None, "cost_usd": None}],
            "0010_y": [{"kind": "gebo", "stage": "integrate", "agent": {"glyph": "ᚷ", "name": "Gebo"},
                        "started": "2026-09-24T02:00:00+00:00", "turns": 3, "cost_usd": 0.25}],
            "0011_z": [{"kind": "rebase", "stage": "integrate", "agent": None,
                        "started": "2026-09-24T03:00:00+00:00", "turns": None, "cost_usd": None}],
        },
        "unknown_end": {"0012_w": [{"stage": "plan", "started": "2026-09-24T00:00:00+00:00"}]},
    }

    def test_a_step(self):
        from coscc.state import _activities

        [a] = _activities("0009_x", self.READ)
        self.assertEqual((a.label, a.agent, a.stage, a.started), ("running", "ᚢ Uruz", "impl", present.when("2026-09-24T01:00:00+00:00")))

    def test_unknown_turns_and_cost_are_empty_not_zero(self):
        from coscc.state import _activities

        [a] = _activities("0009_x", self.READ)
        self.assertEqual((a.turns, a.cost), ("", ""))

    def test_gebo_with_turns_and_cost(self):
        from coscc.state import _activities

        [a] = _activities("0010_y", self.READ)
        self.assertEqual((a.label, a.agent, a.turns, a.cost), ("running", "ᚷ Gebo", "3", "$0.25"))

    def test_a_rebase_has_no_agent(self):
        from coscc.state import _activities

        [a] = _activities("0011_z", self.READ)
        self.assertEqual((a.label, a.agent, a.kind), ("rebasing", "", "rebase"))

    def test_ended_unknown(self):
        from coscc.state import _activities

        [a] = _activities("0012_w", self.READ)
        self.assertEqual((a.label, a.agent, a.stage, a.kind), ("ended, unknown", "", "plan", "unknown"))

    def test_a_unit_with_nothing_and_an_empty_read_have_no_lines(self):
        from coscc.state import _activities

        self.assertEqual(_activities("0099_q", self.READ), [])
        self.assertEqual(_activities("0009_x", {}), [])

    def test_building_live_never_reads_the_tab_running_var(self):
        """R8: `running` is this tab's own press; it must not be a second source."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        state = _state_class(tree)
        methods = {n.name: n for n in state.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        functions = {n.name: n for p in _split_from("state")
                     for n in ast.parse(p.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}
        for fn in (functions["_activities"], methods["_apply_running"], methods["poll_running"]):
            with self.subTest(fn=fn.name):
                self.assertNotIn("self.running", ast.unparse(fn))
        self.assertIn("SERVICE.running", ast.unparse(methods["poll_running"]))
        decorators = [ast.unparse(d) for d in methods["poll_running"].decorator_list]
        self.assertIn("rx.event(background=True)", decorators)


class OneLoopPerTab(unittest.TestCase):
    """`0051` plan Risk 2: three presses of *Board* leave one loop, and leaving ends it.

    Driven through Reflex's own event processor, as `scripts/verify_0024.py` does.
    """

    def test_three_navigations_one_loop(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-one-loop"
        calls: list[str] = []

        def running(cwd):
            calls.append(cwd)
            return {"running": {}, "unknown_end": {}}

        async def go():
            # `0056`: a press of *Board* is a redirect now, and what reaches the state is
            # the arrival it causes — on the same socket, so no full read.
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                await _somewhere(manager, token)
                for _ in range(3):
                    await arrive("/board?ws=somewhere", settle=0.05)
                await asyncio.sleep(0.3)
                alive = token in page._POLLING
                asked = len(calls)
                await arrive("/sessions?ws=somewhere", settle=0.05)
                await asyncio.sleep(0.3)
                return alive, asked, token in page._POLLING

        with mock.patch.object(page, "RUNNING_POLL", 0.1), mock.patch.object(page.SERVICE, "running", running):
            alive, asked, still = asyncio.run(go())
        self.assertTrue(alive)
        # One loop, asking every 0.1s over roughly 0.45s: about five asks. Three loops
        # would have asked about three times as often.
        self.assertLessEqual(asked, 8)
        self.assertGreaterEqual(asked, 2)
        self.assertFalse(still)

    def test_a_socket_drop_does_not_end_the_loop_a_closed_tab_does(self):
        """Review round 1, F2: Reflex unmaps a token on every drop and maps it back on
        reconnect, so one miss must not end the loop; `GONE_AFTER` misses in a row do."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-drop"
        gone = {"now": False}

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                await _somewhere(manager, token)
                await arrive("/board?ws=somewhere", settle=0.05)
                gone["now"] = True
                await asyncio.sleep(0.08)  # one or two misses, under GONE_AFTER
                gone["now"] = False
                await asyncio.sleep(0.3)
                after_drop = token in page._POLLING
                gone["now"] = True
                await asyncio.sleep(0.5)
                return after_drop, token in page._POLLING

        with (
            mock.patch.object(page, "RUNNING_POLL", 0.05),
            mock.patch.object(page, "GONE_AFTER", 4),
            mock.patch.object(page, "_tab_gone", lambda t: gone["now"]),
            mock.patch.object(page.SERVICE, "running", lambda cwd: {"running": {}, "unknown_end": {}}),
        ):
            after_drop, after_close = asyncio.run(go())
        self.assertTrue(after_drop)
        self.assertFalse(after_close)


class ChangingWorkspaceForgetsTheOldRead(unittest.TestCase):
    """Review round 1, F1: a unit named as one running in the workspace just left must not
    show that session in the one chosen, not even until the loop's next ask."""

    def test_same_unit_name_in_another_workspace_shows_nothing(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-switch"
        running_in = {"/a": {"running": {"0009_x": [{"kind": "step", "stage": "impl",
                                                     "agent": {"glyph": "ᚢ", "name": "Uruz"},
                                                     "started": "2026-09-24T01:00:00+00:00",
                                                     "turns": None, "cost_usd": None}]},
                             "unknown_end": {}},
                      "/b": {"running": {}, "unknown_end": {}}}
        board = {"stages": [], "recording": True, "units": [
            {"name": "0009_x", "stages": [], "phase": "impl", "next": "", "blocked": True, "problems": []},
        ]}

        async def branch_here(cwd):
            return {"branch": "main"}

        async def read_board(cwd):
            return board

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.workspaces = [page.Workspace(id="/a", name="a"),
                                         page.Workspace(id="/b", name="b")]
                    studio.cwd = studio._read_cwd = "/a"
                    studio.screen = "sessions"  # no loop: only the change of workspace reads
                    studio._running_read = running_in["/a"]
                    studio._loaded_sid = "s1"
                # `0056`: what `choose_workspace("/b")` redirects to, on the same socket.
                await arrive("/sessions?ws=b", settle=0.05)
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    return [(u.id, len(u.live)) for u in studio.cards]

        with (
            mock.patch.object(page.SERVICE, "running", lambda cwd: running_in[cwd]),
            mock.patch.object(page.SERVICE, "branch_here", branch_here),
            mock.patch.object(page.SERVICE, "board", read_board),
            mock.patch.object(page.SERVICE, "sessions_for", lambda cwd, limit: {"sessions": []}),
            mock.patch.object(page.SERVICE, "activity_and_usage",
                              mock.Mock(side_effect=page.Invalid("not here"))),
        ):
            cards = asyncio.run(go())
        self.assertEqual(cards, [("0009_x", 0)])


class AnIntegrationIsListedWithTheSteps(unittest.TestCase):
    """`0114` R1: the service lists an integration with `kind: "integration"` and `run: None`,
    and the page builds its row rather than failing on the new key or the `None`."""

    def test_both_kinds_load(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        listed = [
            {"unit": "0001_a", "stage": "plan", "started_at": "2026-09-26T13:00:00+00:00",
             "stopping": False, "run": "r1", "kind": "step"},
            {"unit": "0096_x", "stage": "integrate", "started_at": "2026-09-26T13:12:35+00:00",
             "stopping": False, "run": None, "kind": "integration"},
        ]
        token = "state-test-integration-listed"
        manager, _, _ = _processor(token)

        async def go():
            async with manager.modify_state(_key(token)) as root:
                studio = await root.get_state(page.StudioState)
                studio.cwd = "/w"
                with mock.patch.object(page.SERVICE, "running_steps", lambda cwd: listed):
                    studio._load_running()
                return [(r.unit, r.kind, r.run) for r in studio.running_steps]

        self.assertEqual(asyncio.run(go()), [("0001_a", "step", "r1"), ("0096_x", "integration", "")])


def _key(token: str):
    from reflex.istate.manager.token import BaseStateToken
    from reflex.state import State

    return BaseStateToken(ident=token, cls=State)


def _processor(token: str):
    """Reflex's own event processor over a memory state manager, and a `fire` for it."""
    import asyncio

    from reflex.event import Event
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex_base.event.processor import BaseStateEventProcessor
    from reflex_base.utils.format import format_event_handler

    from coscc import state as page

    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager)

    async def fire(handler: str, **payload):
        name = format_event_handler(page.StudioState.event_handlers[handler])
        await processor.enqueue(token, Event(name=name, payload=payload))
        await asyncio.sleep(0.05)

    return manager, processor, fire


def _arrival(manager, processor, token: str):
    """`0056`. An `arrive` the way a route's `on_load` sends it: the address and the socket's
    id in the event's `router_data`, the keys `self.router.url` and `.session` are built
    from (`reflex/istate/data.py`, `URLData.from_router_data`, `SessionData`)."""
    import asyncio

    from reflex.event import Event
    from reflex_base.constants import RouteVar
    from reflex_base.utils.format import format_event_handler

    from coscc import state as page

    name = format_event_handler(page.StudioState.event_handlers["arrive"])

    async def arrive(address: str, sid: str = "s1", settle: float = 0.15):
        async with manager.modify_state(_key(token)) as root:
            if not root.router_data:
                # A state that never saw a route is rehydrated first, and rehydrating runs
                # the app's `on_load` list — which needs a registered App this test lacks.
                root.router_data = {RouteVar.CLIENT_TOKEN: token}
        path = address.partition("?")[0]
        router_data = {
            RouteVar.PATH: path.rstrip("/") or "/",
            RouteVar.ORIGIN: address,
            RouteVar.SESSION_ID: sid,
            RouteVar.CLIENT_TOKEN: token,
            RouteVar.HEADERS: {"origin": "http://test"},
        }
        future = await processor.enqueue(
            token, Event(name=name, payload={}, router_data=router_data))
        await asyncio.sleep(settle)
        return future

    return arrive


async def _somewhere(manager, token: str) -> None:
    """One listed workspace, `/somewhere`, already read on socket `s1`."""
    from coscc import state as page

    async with manager.modify_state(_key(token)) as root:
        studio = await root.get_state(page.StudioState)
        studio.workspaces = [page.Workspace(id="/somewhere", name="somewhere")]
        studio.cwd = studio._read_cwd = "/somewhere"
        studio._loaded_sid = "s1"


async def _studio(manager, token: str):
    from coscc import state as page

    async with manager.modify_state(_key(token)) as root:
        return await root.get_state(page.StudioState)


class _Page:
    """`0056`. Every `SERVICE` call a first arrival makes, answered from memory and counted,
    and every `rx.redirect` the handlers ask for, recorded."""

    BOARD = {"stages": [], "recording": True, "units": [
        {"name": "0009_x", "stages": [], "phase": "impl", "next": "", "blocked": True,
         "problems": []},
    ]}

    def __init__(self):
        from collections import Counter

        self.calls = Counter()
        self.redirects: list[tuple[str, bool]] = []

    def patches(self):
        import contextlib
        from unittest import mock

        from coscc import state as page

        def counted(name, answer):
            def call(*args, **kwargs):
                self.calls[name] += 1
                return answer
            return call

        def acounted(name, answer):
            async def call(*args, **kwargs):
                self.calls[name] += 1
                return answer
            return call

        real = page.rx.redirect

        def redirect(path, *args, **kwargs):
            self.redirects.append((path, bool(kwargs.get("replace", False))))
            return real(path, *args, **kwargs)

        workspaces = {"working_dir": "/w", "workspaces": [
            {"path": "/a", "name": "a", "label": "", "source": "store", "missing": False},
            {"path": "/b", "name": "b", "label": "", "source": "store", "missing": False},
        ]}
        stack = contextlib.ExitStack()
        for name, value in {
            "settings": counted("settings", {"knobs": [], "grants": []}),
            "preferences": counted("preferences", {}),
            "workspaces": counted("workspaces", workspaces),
            "stage_models": acounted("stage_models", {"rows": [], "problems": []}),
            "branch_here": acounted("branch_here", {"branch": "main"}),
            "board": acounted("board", self.BOARD),
            "sessions_for": counted("sessions_for", {"sessions": []}),
            "activity_and_usage": counted("activity_and_usage", {"events": [], "total": {}}),
            "update_status": counted("update_status", {}),
            "running": counted("running", {"running": {}, "unknown_end": {}}),
            "running_steps": counted("running_steps", []),
            "timeline": counted("timeline", {"runs": []}),
            "cost": counted("cost", {"recording": False}),
            "unit_cost": counted("unit_cost", {"by_stage": [], "anomalies": [], "recording": True}),
            "artifact": counted("artifact", {"file": "impl.md", "exists": False, "text": ""}),
            "next_step": mock.AsyncMock(side_effect=page.Invalid("not asked in this test")),
        }.items():
            stack.enter_context(mock.patch.object(page.SERVICE, name, value))
        stack.enter_context(mock.patch.object(page.rx, "redirect", redirect))
        stack.enter_context(mock.patch.object(page, "RUNNING_POLL", 0.05))
        return stack


class AnArrivalReadsOnce(unittest.TestCase):
    """`0056` R15, R16, R17. Driven through Reflex's own event processor, as `verify_0024`
    drives the page; `SERVICE.board` is counted per arrival."""

    def _walk(self, steps):
        import asyncio

        fake = _Page()
        token = "state-test-arrive"
        seen = []

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                for address, sid in steps:
                    before = dict(fake.calls)
                    await arrive(address, sid)
                    studio = await _studio(manager, token)
                    seen.append({
                        "board": fake.calls["board"] - before.get("board", 0),
                        "timeline": fake.calls["timeline"] - before.get("timeline", 0),
                        "screen": studio.screen, "cwd": studio.cwd, "unit": studio.unit_id,
                        "tab": studio.detail_tab, "notice": studio.notice,
                        "redirects": list(fake.redirects),
                    })
                    fake.redirects.clear()
                await arrive("/sessions?ws=a", "s9")  # leave the Board, so the loop ends
                await asyncio.sleep(0.15)

        with fake.patches():
            asyncio.run(go())
        return seen

    def test_the_board_is_read_once_per_workspace_and_per_page(self):
        seen = self._walk([
            ("/board?ws=a", "s1"),
            ("/unit?ws=a&id=0009_x", "s1"),
            ("/unit?ws=a&id=0009_x&tab=timeline", "s1"),
            ("/board?ws=a", "s1"),  # Back
            ("/sessions?ws=a", "s1"),
            ("/board?ws=b", "s1"),
            ("/board/?ws=b", "s2"),  # reload, through the 307 to the slash
        ])
        # First: the address reached the handler. Were `router_data` ignored, every arrival
        # would read as `/` and the rest of this test would pass on nothing.
        self.assertEqual(seen[0]["screen"], "board")
        self.assertEqual([s["board"] for s in seen], [1, 0, 0, 0, 0, 1, 1])
        self.assertEqual([s["timeline"] for s in seen], [0, 1, 0, 0, 0, 0, 0])
        self.assertEqual((seen[1]["screen"], seen[1]["unit"], seen[1]["tab"]),
                         ("board", "0009_x", "overview"))
        self.assertEqual(seen[2]["tab"], "timeline")
        self.assertEqual((seen[3]["unit"], seen[4]["screen"]), ("", "sessions"))
        self.assertEqual((seen[5]["cwd"], seen[6]["cwd"]), ("/b", "/b"))
        self.assertEqual([s["redirects"] for s in seen], [[]] * 7)

    def test_an_address_without_ws_is_replaced_by_one_with_it(self):
        seen = self._walk([("/board", "s1"), ("/board?ws=a", "s1")])
        self.assertEqual(seen[0]["redirects"], [("/board?ws=a", True)])
        self.assertEqual((seen[0]["screen"], seen[0]["cwd"]), ("board", "/a"))
        self.assertEqual(seen[1]["redirects"], [])
        self.assertEqual(seen[1]["board"], 0)

    def test_a_workspace_not_on_the_list_says_so_and_is_replaced(self):
        seen = self._walk([("/board?ws=nope", "s1")])
        self.assertEqual(seen[0]["notice"], "That workspace is not on the list.")
        self.assertEqual(seen[0]["redirects"], [("/board?ws=a", True)])
        self.assertEqual(seen[0]["cwd"], "/a")

    def test_a_unit_address_without_an_id_is_the_board(self):
        seen = self._walk([("/unit?ws=a", "s1")])
        self.assertEqual(seen[0]["redirects"], [("/board?ws=a", True)])
        self.assertEqual((seen[0]["screen"], seen[0]["unit"]), ("board", ""))

    def test_a_bogus_tab_opens_overview_and_is_replaced(self):
        seen = self._walk([("/unit?ws=a&id=0009_x&tab=bogus", "s1")])
        self.assertEqual(seen[0]["tab"], "overview")
        self.assertEqual(seen[0]["redirects"], [("/unit?ws=a&id=0009_x", True)])

    def test_a_reload_at_a_unit_reads_the_unit_again(self):
        seen = self._walk([("/unit?ws=a&id=0009_x", "s1"), ("/unit/?ws=a&id=0009_x", "s2")])
        self.assertEqual([s["board"] for s in seen], [1, 1])
        self.assertEqual([s["timeline"] for s in seen], [1, 1])

    def test_an_arrival_cut_short_is_read_by_the_next(self):
        """A newer navigation cancels an unfinished `on_load` chain (Reflex's
        `on_load_internal` supersedes). Left as a cancelled change of workspace leaves it —
        `cwd` moved, its board never read — the next arrival there reads it."""
        import asyncio

        from coscc import state as page

        fake = _Page()
        token = "state-test-cut-short"

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.workspaces = [page.Workspace(id="/a", name="a"),
                                         page.Workspace(id="/b", name="b")]
                    studio.cwd, studio._read_cwd, studio._loaded_sid = "/b", "/a", "s1"
                    studio.screen = "board"
                await arrive("/board?ws=b", "s1")
                read = fake.calls["board"]
                await arrive("/sessions?ws=b", "s1")
                return read

        with fake.patches():
            self.assertEqual(asyncio.run(go()), 1)

    def test_no_unit_is_open_while_another_workspace_s_board_is_read(self):
        """`0056` review round 1, F1. Back from `/board?ws=b` to a unit of `a`: while `a`'s
        board is read, `units` is still `b`'s, so no dialog may be open over it."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        fake = _Page()
        token = "state-test-back-to-a-unit"
        during: list[tuple[str, str]] = []
        real = page.StudioState._load_board

        async def spy(self):
            during.append((self.cwd, self.unit_id))
            return await real(self)

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                await arrive("/unit?ws=a&id=0009_x", "s1")
                await arrive("/board?ws=b", "s1")
                await arrive("/unit?ws=a&id=0009_x", "s1")  # Back
                studio = await _studio(manager, token)
                after = (studio.cwd, studio.unit_id)
                await arrive("/sessions?ws=a", "s9")
                await asyncio.sleep(0.15)
                return after

        with fake.patches(), mock.patch.object(page.StudioState, "_load_board", spy):
            after = asyncio.run(go())
        self.assertEqual(during, [("/a", ""), ("/b", ""), ("/a", ""), ("/a", "")])
        self.assertEqual(after, ("/a", "0009_x"))

    def test_a_change_of_workspace_cancelled_mid_read_is_read_again_on_the_way_back(self):
        """`0056` review round 2, F5. `_load_board` empties `units` before its first await;
        cancelled there — a newer navigation supersedes it — and followed back to the
        workspace last read, that workspace's board is read again, not left empty."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        def walk(start: str) -> tuple[list[str], int, str, bool]:
            fake = _Page()
            token = f"state-test-cancelled-{start}"
            read: list[str] = []

            async def board(cwd):
                read.append(cwd)
                if cwd == "/b":
                    await asyncio.Event().wait()  # a `gh` that has not answered yet
                return _Page.BOARD

            async def go():
                manager, processor, _ = _processor(token)
                arrive = _arrival(manager, processor, token)
                async with processor:
                    await arrive(start, "s1")
                    pending = await arrive("/board?ws=b", "s1")
                    self.assertEqual(read[-1], "/b")  # the read of `b` is under way
                    pending.cancel()  # what `_supersede_previous` does to it
                    await asyncio.sleep(0.05)
                    await arrive(start, "s1")  # Back
                    studio = await _studio(manager, token)
                    got = (list(read), len(studio.cards), studio.unit_id, studio.unit_missing)
                    await arrive("/sessions?ws=a", "s9")
                    await asyncio.sleep(0.15)
                    return got

            with fake.patches(), mock.patch.object(page.SERVICE, "board", board):
                return asyncio.run(go())

        self.assertEqual(walk("/unit?ws=a&id=0009_x"), (["/a", "/b", "/a"], 1, "0009_x", False))
        self.assertEqual(walk("/board?ws=a"), (["/a", "/b", "/a"], 1, "", False))

    def test_a_tab_pressed_while_asking_waits_for_the_same_ask(self):
        """`0056` review round 1, F2. Each arrival at an unanswered unit chains `load_next`;
        the second waits for the ask the first began, rather than start one beside it."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        fake = _Page()
        token = "state-test-ask-once"
        asked = []

        async def go():
            gate = asyncio.Event()

            async def next_step(cwd, unit):
                asked.append((cwd, unit))
                await gate.wait()
                return {"stage": "impl", "action": "run impl", "blocked": False}

            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            with mock.patch.object(page.SERVICE, "next_step", next_step):
                async with processor:
                    await arrive("/unit?ws=a&id=0009_x", "s1")
                    await arrive("/unit?ws=a&id=0009_x&tab=timeline", "s1")
                    gate.set()
                    await asyncio.sleep(0.15)
                    studio = await _studio(manager, token)
                    got = (studio.run_stage, studio._asked, dict(page._ASKING))
                    await arrive("/sessions?ws=a", "s9")
                    await asyncio.sleep(0.15)
                    return got

        with fake.patches():
            got = asyncio.run(go())
        self.assertEqual(asked, [("/a", "0009_x")])
        self.assertEqual(got, ("impl", "0009_x", {}))

    def test_a_link_to_another_workspace_s_unit_opens_it(self):
        seen = self._walk([("/board?ws=a", "s1"), ("/unit?ws=b&id=0009_x&tab=questions", "s1")])
        self.assertEqual((seen[1]["cwd"], seen[1]["unit"], seen[1]["tab"]),
                         ("/b", "0009_x", "questions"))
        self.assertEqual((seen[1]["board"], seen[1]["timeline"]), (1, 1))


class ANavigationIsOnlyARedirect(unittest.TestCase):
    """`0056` R7, R12, R13. Each handler returns the address of where it goes, adding to the
    history or replacing in it, and sets none of `screen`, `cwd`, `unit_id`, `detail_tab`."""

    def test_each_handler(self):
        import asyncio

        fake = _Page()
        token = "state-test-navigate"
        got = {}

        async def go():
            manager, processor, fire = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                await arrive("/unit?ws=a&id=0009_x&tab=questions", "s1")
                for handler, payload in [
                    ("navigate", {"screen": "sessions"}),
                    ("open_unit", {"unit": "0010_y"}),
                    ("choose_workspace", {"path": "/b"}),
                    ("open_workspace", {"path": "/b"}),
                    ("toggle_detail", {"value": False}),
                    ("set_detail_tab", {"value": "timeline"}),
                ]:
                    fake.redirects.clear()
                    await fire(handler, **payload)
                    studio = await _studio(manager, token)
                    got[handler] = (
                        list(fake.redirects),
                        (studio.screen, studio.cwd, studio.unit_id, studio.detail_tab),
                    )
                await arrive("/sessions?ws=a", "s1")

        with fake.patches():
            asyncio.run(go())
        unmoved = ("board", "/a", "0009_x", "questions")
        self.assertEqual(got, {
            "navigate": ([("/sessions?ws=a", False)], unmoved),
            "open_unit": ([("/unit?ws=a&id=0010_y", False)], unmoved),
            # From a unit, a change of workspace goes to that workspace's Board (R13).
            "choose_workspace": ([("/board?ws=b", False)], unmoved),
            "open_workspace": ([("/board?ws=b", False)], unmoved),
            "toggle_detail": ([("/board?ws=a", False)], unmoved),
            "set_detail_tab": ([("/unit?ws=a&id=0009_x&tab=timeline", True)], unmoved),
        })


class TheUnitDialogKnowsMissingAndDropped(unittest.TestCase):
    """`0056` R9, R11: read from `units` and `hold_state`, no service call of their own."""

    def test_the_three_vars(self):
        import asyncio

        from coscc import state as page

        token = "state-test-missing"

        async def go():
            manager, _, _ = _processor(token)
            out = []
            async with manager.modify_state(_key(token)) as root:
                studio = await root.get_state(page.StudioState)
                studio.workspaces = [page.Workspace(id="/p", name="proj")]
                studio.cwd = "/p"
                # `0053`: the board read as `_load_board` leaves it, and `current_unit` set
                # wherever `unit_id` moves, as `arrive` does.
                full = [page.Unit(id="0001_alpha"), page.Unit(id="0002_gone", hold_state="dropped")]
                studio._full = {u.id: u for u in full}
                studio.cards = [page._card(u) for u in full]
                for unit, loading in [("", False), ("0001_alpha", False), ("0002_gone", False),
                                      ("9999_nope", False), ("9999_nope", True)]:
                    studio.unit_id, studio.loading = unit, loading
                    studio._set_current()
                    out.append((studio.unit_missing, studio.unit_dropped))
                out.append(studio.board_href)
            return out

        got = asyncio.run(go())
        self.assertEqual(got, [(False, False), (False, False), (False, True),
                               (True, False), (False, False), "/board?ws=proj"])


class ADroppedUnitsDialogOffersNothingThatWrites(unittest.TestCase):
    """`0056` R11, review round 1, F3. `verify_0056` sees the run block, Integrate and Outcome
    absent for its dropped unit, but `cos.mjs` would hide them there anyway; this reads the
    dialog itself: every control that writes sits in the true branch of a
    `rx.cond(~P.unit_dropped, …)`, and the hold panel's does not."""

    WRITES = {"set_mode", "run_step", "start_branch", "integrate", "record_outcome",
              "edit_answer", "answer_question", "post_review_comment"}

    def _handlers(self):
        import re

        from coscc import screens

        found: dict[str, set[bool]] = {}

        def walk(c, guarded):
            for chain in (getattr(c, "event_triggers", None) or {}).values():
                for name in re.findall(r"StudioState\.(\w+) at", str(chain)):
                    found.setdefault(name, set()).add(guarded)
            children = list(getattr(c, "children", None) or [])
            cond = str(getattr(c, "cond", "")) if type(c).__name__ == "Cond" else ""
            if "!(" in cond and "unit_dropped" in cond.partition("!(")[2].partition(")")[0]:
                walk(children[0], True)
                children = children[1:]
            for child in children:
                walk(child, guarded)

        walk(screens._detail_dialog(), False)
        return found

    def test_every_control_that_writes_is_behind_not_dropped(self):
        found = self._handlers()
        self.assertEqual({n: found.get(n) for n in self.WRITES}, {n: {True} for n in self.WRITES})

    def test_the_way_back_is_not(self):
        self.assertEqual(self._handlers().get("set_hold"), {False})


class TheRoutesAreTheNavigation(unittest.TestCase):
    """`0056`: `coscc/place.py` may not import the state, so its screen list is a copy."""

    def test_screens_match(self):
        from coscc import place
        from coscc.state import NAVIGATION

        self.assertEqual(place.SCREENS, tuple(key for key, _, _ in NAVIGATION))


def _self_names(target: ast.expr) -> list[str]:
    """Every `self.X` being assigned by one target, tuple unpacking included."""
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return [target.attr] if target.value.id == "self" else []
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _self_names(item)]
    return []


class TheDialogDrawsTheMessagesToo(unittest.TestCase):
    """`0071` R2, R7, R8, by structure only. Whether the dialog's copy is really in view is
    `scripts/verify_0071.py`'s to measure; this only keeps both copies from disappearing."""

    def test_the_dialog_carries_its_own_sticky_copy(self):
        from coscc import screens

        drawn = str(screens._detail_dialog())
        for part in ("detail-messages", "detail-notice", "detail-error", "sticky"):
            self.assertIn(part, drawn)

    def test_the_page_keeps_the_ids_older_proofs_read(self):
        from coscc import screens

        drawn = str(screens._banners())
        self.assertIn("page-notice", drawn)
        self.assertIn("page-error", drawn)
        self.assertNotIn("detail-", drawn)


def _sample_board() -> dict:
    """`0053`. A `Service.board` answer with a unit in each lane and each badge a card draws.
    `0100`: each unit's `at` and `why` as `cos.mjs` would send them, and its `state` as
    `Service.board` decides it."""
    from coscc.service import unit_state

    def rows(statuses, auto: str = ""):
        return [{"stage": n, "status": st, "mode": "autonomous" if n == auto else "manual",
                 "grants": ["Read"] if n == "impl" else [], "warning": "w" if n == "impl" else ""}
                for n, st in zip(("intent", "spec", "plan", "impl"), statuses)]

    board = {"stages": ["intent", "spec", "plan", "impl"], "recording": True, "units": [
        {"name": "0001_planned", "stages": rows(["not started"] * 4), "next": "write-intent",
         "problems": []},
        {"name": "0002_going", "stages": rows(["accepted", "accepted", "not started", "not started"]),
         "next": "write-plan", "next_stage": "plan", "problems": [],
         "cost": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.5}, "open": 1,
         "questions": [{"artifact": "spec.md", "n": 1, "text": "Câu hỏi?", "answered": False,
                        "counted": True}],
         "backlog": {"rank": 2, "relations": [{"type": "trùng", "other": "0003_review",
                                               "direction": "out"}]}},
        {"name": "0003_review", "stages": rows(["accepted", "draft", "not started", "not started"]),
         "next": "accept spec", "problems": ["no plan.md"],
         "pr": {"url": "https://github.com/o/r/pull/1"},
         "rounds": [{"n": 1, "verdict": "accepted", "comment": {"posted": True, "url": "u"}}],
         "integration": {"state": "behind", "behind": 2, "button": True, "warnings": ["w"]}},
        {"name": "0004_done", "stages": rows(["accepted"] * 4), "next": "finished", "problems": [],
         "cost": {"input_tokens": 30, "cost_usd": 1.5},
         "outcome_label": {"text": "đạt", "color": "grass", "source": "verify"},
         "worktree": {"path": "/t/0004", "branch": "feat/x", "prepare": {"ok": True}}},
        {"name": "0005_gone", "stages": rows(["accepted", "not started", "not started", "not started"]),
         "next": "write-spec", "problems": [],
         "hold": {"state": "dropped", "reason": "r", "by": "b", "date": "d"}, "hold_moves": ["paused"]},
        {"name": "0006_auto", "stages": rows(["accepted", "not started", "not started", "not started"],
                                             auto="spec"),
         "next": "write-spec", "next_stage": "spec", "problems": []},
    ]}
    said = {"0001_planned": ("intent", "missing"), "0002_going": ("plan", "missing"),
            "0003_review": ("spec", "draft"), "0004_done": ("impl", "finished"),
            "0005_gone": ("spec", "dropped"), "0006_auto": ("spec", "missing")}
    for u in board["units"]:
        u["at"], u["why"] = said[u["name"]]
        u["state"] = unit_state(u, None, None)
    return board


_LIVE = {"running": {"0002_going": [{"kind": "step", "stage": "plan",
                                     "agent": {"glyph": "ᚱ", "name": "Raidho"},
                                     "started": "2026-09-25T01:00:00+00:00",
                                     "turns": None, "cost_usd": None}]},
         "unknown_end": {}}


class _Sample(_Page):
    BOARD = _sample_board()


def _sample_read(token: str, then=None, fake: _Page | None = None):
    """The sample read by a first arrival at the Board, a poll applying `_LIVE`, then
    `then(arrive, manager)` if given. Returns `(_full, cards, what then returned)`. `fake`
    answers in place of `_Sample` when given."""
    import asyncio
    from unittest import mock

    from coscc import state as page

    fake = fake or _Sample()

    async def go():
        manager, processor, _ = _processor(token)
        arrive = _arrival(manager, processor, token)
        async with processor:
            await arrive("/board?ws=a", "s1")
            await asyncio.sleep(0.2)
            studio = await _studio(manager, token)
            full, cards = dict(studio.get_value("_full")), list(studio.get_value("cards"))
            more = await then(arrive, manager) if then else None
            await arrive("/sessions?ws=a", "s9")  # leave the Board, so the loop ends
            await asyncio.sleep(0.15)
            return full, cards, more

    with fake.patches(), mock.patch.object(page.SERVICE, "running", lambda cwd: _LIVE):
        return asyncio.run(go())


class TheBoardIsDrawnFromTheStages(unittest.TestCase):
    """`0100` R1. The columns are the stages the board read returned: another list of stages
    is other columns, with no line of coscc changed."""

    def test_two_made_up_stages_are_two_columns(self):
        from types import SimpleNamespace

        from coscc.service import unit_state
        from coscc.state import StudioState

        class Other(_Page):
            BOARD = {"stages": ["alpha", "beta"], "recording": True, "units": [
                {"name": f"000{i}_{at}", "stages": [], "next": "x", "problems": [], "at": at, "why": "missing"}
                for i, at in enumerate(["alpha", "beta", "beta"], start=1)
            ]}

        for u in Other.BOARD["units"]:
            u["state"] = unit_state(u, None, None)

        async def ask(arrive, manager):
            studio = await _studio(manager, "state-test-0100-stages")
            return list(studio.stages)

        full, cards, stages = _sample_read("state-test-0100-stages", ask, Other())
        self.assertEqual(stages, ["alpha", "beta"])
        page = SimpleNamespace(cards=cards, query="", focus="All work", stages=stages)
        cv = StudioState.computed_vars
        page.shown_ids = cv["shown_ids"].fget(page)
        page.board_ids = cv["board_ids"].fget(page)
        counts = cv["stage_counts"].fget(page)
        self.assertEqual(list(counts), ["alpha", "beta"])
        self.assertEqual(counts, {"alpha": 1, "beta": 2})
        self.assertEqual({c.id: c.at for c in cards}, {u["name"]: u["at"] for u in Other.BOARD["units"]})


class NoCardLosesWhatItShowed(unittest.TestCase):
    """`0053` R13: a card carries every field its card draws with the value the whole unit
    has; the dialog of each unit is the whole unit `_load_board` built."""

    def test_every_field_a_card_draws_is_the_unit_s(self):
        import dataclasses
        import inspect
        import re

        from coscc import screens
        from coscc.state import Card

        full, cards, _ = _sample_read("state-test-r13-cards")
        self.assertEqual([c.id for c in cards], list(full))
        self.assertEqual(len(cards), 6)
        fields = {f.name for f in dataclasses.fields(Card)}
        drawn = set(re.findall(r"\bunit\.(\w+)", inspect.getsource(screens._unit_card)))
        self.assertLessEqual(drawn, fields, "`_unit_card` draws a field a card does not carry")
        for card in cards:
            whole = full[card.id]
            for name in fields - {"has_problem"}:
                self.assertEqual(getattr(card, name), getattr(whole, name), f"{card.id}.{name}")
            self.assertEqual(card.has_problem, whole.problems != "", card.id)
        # The sample reaches every badge: a problem, a question, integration, outcome, hold,
        # a rank, a relation, a cost and a session running.
        self.assertTrue(all(any(getattr(c, n) for c in cards) for n in (
            "has_problem", "open_questions", "integration_state", "outcome_text", "hold_state",
            "shortlist_rank", "relations_text", "token_count", "live")))

    def test_each_dialog_is_the_whole_unit(self):
        async def open_each(arrive, manager):
            got = {}
            for uid in [u["name"] for u in _Sample.BOARD["units"]]:
                await arrive(f"/unit?ws=a&id={uid}", "s1")
                studio = await _studio(manager, "state-test-r13-dialog")
                got[uid] = (studio.get_value("current_unit"), studio.unit_tree)
            return got

        full, _, opened = _sample_read("state-test-r13-dialog", open_each)
        self.assertEqual(set(opened), set(full))
        shared = set()
        for uid, (current, tree) in opened.items():
            self.assertEqual(current, full[uid], uid)
            self.assertIsNot(current, full[uid], uid)
            # Review round 1, F1: the lists are the dialog's own too, not `_full`'s.
            for name in ("cells", "questions", "rounds", "live"):
                if getattr(full[uid], name):
                    shared.add(name)
                    self.assertIsNot(getattr(current, name), getattr(full[uid], name),
                                     f"{uid}.{name}")
        self.assertEqual(shared, {"cells", "questions", "rounds", "live"})
        self.assertEqual(opened["0004_done"][1], "Worktree: /t/0004 on feat/x · prepared")
        self.assertEqual(opened["0003_review"][0].pr_url, "https://github.com/o/r/pull/1")


class TheIdListsAnswerAsTheCardListsDid(unittest.TestCase):
    """`0053` plan step 2: `shown_ids`, `stage_counts`, `resume_id` and `command_ids` against
    the rules they answer to, written out over the whole units, on the same sample. `0100`
    replaced the four lanes with the stage columns and the states."""

    def test_same_answers(self):
        from types import SimpleNamespace

        from coscc.state import StudioState

        full, cards, _ = _sample_read("state-test-id-lists")
        units = list(full.values())
        stages = _Sample.BOARD["stages"]
        grouped = ("done", "paused", "dropped")

        def visible(query, focus):
            q = query.strip().lower()
            rows = list(units)
            if q:
                rows = [u for u in rows if q in u.id.lower() or q in u.title.lower()]
            if focus == "Autonomous":
                rows = [u for u in rows if u.mode == "autonomous"]
            elif focus == "Needs you":
                rows = [u for u in rows if u.state == "needs-you"]
            return rows

        def command(query):  # `command_units` before `0053`
            q = query.strip().lower()
            if not q:
                return units[:5]
            return [u for u in units if q in u.title.lower() or q in u.id.lower()][:6]

        cv = StudioState.computed_vars
        self.assertEqual({u.id: (u.at, u.state) for u in units}, {
            "0001_planned": ("intent", "ready"), "0002_going": ("plan", "running"),
            "0003_review": ("spec", "error"), "0004_done": ("impl", "done"),
            "0005_gone": ("spec", "dropped"), "0006_auto": ("spec", "ready"),
        })
        for query, focus in [("", "All work"), ("going", "All work"), ("", "Autonomous"),
                             ("", "Needs you"), ("000", "Needs you"), ("nothing", "All work")]:
            page = SimpleNamespace(cards=cards, query=query, focus=focus, command_query=query, stages=stages)
            page.shown_ids = cv["shown_ids"].fget(page)
            old = visible(query, focus)
            self.assertEqual(page.shown_ids, [u.id for u in old], (query, focus))
            page.board_ids = cv["board_ids"].fget(page)
            board = [u for u in old if u.state not in grouped]
            self.assertEqual(page.board_ids, [u.id for u in board])
            self.assertEqual(cv["stage_counts"].fget(page),
                             {n: len([u for u in board if u.at == n]) for n in stages})
            going = sorted((stages.index(u.at), i, u.id) for i, u in enumerate(board)
                           if u.state in ("running", "ready"))
            self.assertEqual(cv["resume_id"].fget(page), going[0][2] if going else "")
            self.assertEqual(cv["command_ids"].fget(page), [u.id for u in command(query)])


class ARunningAskSendsTheCardsOnlyWhenOneChanged(unittest.TestCase):
    """`0053` C4, plan step 4: `_apply_running` sets `cards` only when a `live` changed, and
    `current_unit` only when the open unit's did."""

    def test_the_three_asks(self):
        import asyncio

        from coscc import state as page

        token = "state-test-apply-running"
        other = {"running": {"0003_review": _LIVE["running"]["0002_going"]}, "unknown_end": {}}

        async def go():
            manager, _, _ = _processor(token)
            out = []
            async with manager.modify_state(_key(token)) as root:
                studio = await root.get_state(page.StudioState)
                units = [page.Unit(id="0002_going"), page.Unit(id="0003_review")]
                studio._full = {u.id: u for u in units}
                studio.cards = [page._card(u) for u in units]
                studio.unit_id = "0002_going"
                studio._set_current()
                for read in (_LIVE, _LIVE, other):
                    studio._clean()
                    studio._apply_running(read)
                    out.append(({"cards", "current_unit"} & set(studio.dirty_vars),
                                [len(c.live) for c in studio.cards], len(studio.current_unit.live)))
            return out

        self.assertEqual(asyncio.run(go()), [
            ({"cards", "current_unit"}, [1, 0], 1),
            (set(), [1, 0], 1),
            ({"cards", "current_unit"}, [0, 1], 0),
        ])

    def test_the_source_never_changes_full_in_place(self):
        self.assertNotIn("self._full[", _source_text())


class UsageIsSentOnlyOnItsScreen(unittest.TestCase):
    """`0053` R11."""

    def test_rows_only_on_activity(self):
        from types import SimpleNamespace

        from coscc.state import Card, StudioState, UsageRow

        cards = [Card(id="a", title="A", tokens="10", usd="$0.50", token_count=10, at="intent"),
                 Card(id="b", title="B")]
        rows = StudioState.computed_vars["usage_rows"].fget
        self.assertEqual(rows(SimpleNamespace(screen="board", cards=cards)), [])
        self.assertEqual(rows(SimpleNamespace(screen="activity", cards=cards)),
                         [UsageRow(id="a", title="A", tokens="10", usd="$0.50", token_count=10)])

    def test_0092_a_unit_whose_every_run_died_costless_still_has_a_row(self):
        """R8 c. No token and no cost is still a cost nobody knows, not nothing."""
        from types import SimpleNamespace

        from coscc.state import Card, StudioState, UsageRow, _tokens, _usd

        count, shown = _tokens({"unknown": 2})
        cards = [Card(id="d", title="D", tokens=shown, usd=_usd({"unknown": 2}), token_count=count),
                 Card(id="e", title="E", tokens="—", usd=_usd({}), token_count=0)]
        rows = StudioState.computed_vars["usage_rows"].fget
        self.assertEqual(rows(SimpleNamespace(screen="activity", cards=cards)),
                         [UsageRow(id="d", title="D", tokens="—", usd="unknown", token_count=0)])


class ALongMessageIsCutAndOpensWhole(unittest.TestCase):
    """`0053` R10, R13 point 3."""

    def test_cut_then_opened_byte_for_byte(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-long-message"
        long = "Bước một: đọc kỹ. " * 4148  # 74664 characters, the size `idea.md` found
        read = {"messages": [{"role": "user", "text": "ngắn"}, {"role": "user", "text": long},
                             {"role": "assistant", "text": "Đã đọc."}]}

        async def go():
            manager, _, _ = _processor(token)
            async with manager.modify_state(_key(token)) as root:
                studio = await root.get_state(page.StudioState)
                studio.cwd, studio.session_id = "/a", "s"
                studio._load_history()
                cut = [(m.text, m.cut) for m in studio.messages]
                page.StudioState.open_message.fn(studio, 9)  # out of range: nothing moves
                page.StudioState.open_message.fn(studio, 1)
                opened = [(m.text, m.cut) for m in studio.messages]
            return cut, opened

        with mock.patch.object(page.SERVICE, "history", lambda cwd, sid: read):
            cut, opened = asyncio.run(go())
        self.assertEqual(cut[0], ("ngắn", 0))
        self.assertEqual(cut[1], (long[:page.MESSAGE_CUT], len(long) - page.MESSAGE_CUT))
        self.assertEqual(cut[2], ("Đã đọc.", 0))
        self.assertEqual(opened[1][0].encode(), long.encode())
        self.assertEqual((opened[0], opened[1][1], opened[2]), (cut[0], 0, cut[2]))

    def test_what_send_showed_whole_stays_whole(self):
        """Review round 1, F3: the read at the end of `send` cuts neither the reply that
        just streamed nor a message opened before it; one never opened stays cut."""
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-send-keeps-whole"
        opened, closed, reply = "Mở. " * 2000, "Đóng. " * 1500, "Trả lời. " * 1000
        said = [{"role": "user", "text": "hỏi"}, {"role": "assistant", "text": opened},
                {"role": "assistant", "text": closed}]
        reads = iter([{"messages": said}, {"messages": [
            *said, {"role": "user", "text": "nữa"}, {"role": "assistant", "text": reply}]}])

        async def stream(cwd, text, session_id):
            for i in range(0, len(reply), 1000):
                yield "chunk", reply[i:i + 1000]
            yield "done", {"session_id": "s"}

        async def go():
            manager, _, _ = _processor(token)
            async with manager.modify_state(_key(token)) as root:
                studio = await root.get_state(page.StudioState)
                studio.cwd, studio.session_id = "/a", "s"
                studio._load_history()
                page.StudioState.open_message.fn(studio, 1)
                studio.prompt = "nữa"
                async for _ in page.StudioState.send.fn(studio):
                    pass
                return [(m.text, m.cut) for m in studio.messages], studio.error

        with mock.patch.multiple(
            page.SERVICE,
            history=lambda cwd, sid: next(reads),
            check_send=lambda cwd, text: None,
            stream=stream,
            sessions_for=lambda cwd, limit: {"sessions": [{"session_id": "s"}]},
            activity_and_usage=lambda cwd, limit: {"events": [], "total": {}},
        ):
            shown, error = asyncio.run(go())
        self.assertEqual(error, "")
        self.assertEqual(len(shown), 5)
        self.assertEqual(shown[1], (opened, 0))
        self.assertEqual(shown[2], (closed[:page.MESSAGE_CUT], len(closed) - page.MESSAGE_CUT))
        self.assertEqual(shown[4], (reply, 0))


class SessionsAreReadWhereTheyAreShown(unittest.TestCase):
    """`0053` R9. `SERVICE.sessions_for` is asked on arriving at Sessions in a workspace it
    was not read for, and nowhere else."""

    def test_the_count_per_arrival(self):
        import asyncio

        fake = _Page()
        token = "state-test-sessions-read"
        steps = [("/board?ws=a", "s1"), ("/board?ws=b", "s1"), ("/sessions?ws=b", "s1"),
                 ("/board?ws=b", "s1"), ("/sessions?ws=b", "s1"), ("/sessions?ws=a", "s1"),
                 ("/sessions/?ws=a", "s2")]  # a reload reads again
        seen = []

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                for address, sid in steps:
                    await arrive(address, sid)
                    seen.append(fake.calls["sessions_for"])
                await asyncio.sleep(0.15)

        with fake.patches():
            asyncio.run(go())
        self.assertEqual(seen, [0, 0, 1, 1, 1, 2, 3])


class CostIsReadWhereItIsShown(unittest.TestCase):
    """`0093` R12. `SERVICE.cost` is asked once per arrival at *Cost* and on no other screen;
    `SERVICE.unit_cost` once per unit opened (R11)."""

    def test_the_count_per_arrival(self):
        import asyncio

        fake = _Page()
        token = "state-test-cost-read"
        steps = [("/board?ws=a", "s1"), ("/activity?ws=a", "s1"), ("/cost?ws=a", "s1"),
                 ("/board?ws=a", "s1"), ("/cost?ws=a", "s1"), ("/unit?ws=a&id=0009_x", "s1"),
                 ("/cost/?ws=a", "s2")]  # a reload reads again
        seen = []

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                for address, sid in steps:
                    await arrive(address, sid)
                    seen.append((fake.calls["cost"], fake.calls["unit_cost"]))
                await arrive("/sessions?ws=a", "s9")
                await asyncio.sleep(0.15)

        with fake.patches():
            asyncio.run(go())
        self.assertEqual([c for c, _ in seen], [0, 0, 1, 1, 2, 2, 3])
        self.assertEqual([u for _, u in seen], [0, 0, 0, 0, 0, 1, 1])


class CostRowsAreCopiedAndLabelled(unittest.TestCase):
    """`0093` R4, R5, R10: money through `present.money`, unknown steps beside it, the
    threshold in the measured cell, `No unit` for the empty key."""

    def test_the_screen_reads_what_the_service_returned(self):
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        data = {
            "recording": True, "offset": "UTC+07:00",
            "total": {"usd": 18.4, "steps": 5, "unknown": 2},
            "by_unit": [{"key": "0001_a", "usd": 18.4, "steps": 3, "unknown": 0, "over": True},
                        {"key": "", "usd": None, "steps": 2, "unknown": 2, "over": False}],
            "by_stage": [], "by_day": [{"key": "2026-09-25", "usd": 0.00456, "steps": 5, "unknown": 2}],
            "tokens": {"workspace": {"input_tokens": 25, "output_tokens": 75, "cache_read_tokens": 0,
                                     "cache_creation_tokens": 0, "total": 100}, "by_stage": []},
            "waste": [{"kind": "changes-requested", "count": 3, "usd": 2.0, "unknown": 0, "note": 1}],
            "anomalies": [
                {"kind": "over-budget", "unit": "0001_a", "stage": "", "ended": None, "value": 18.4, "limit": 15.0, "usd": 18.4},
                {"kind": "reruns", "unit": "0001_a", "stage": "review", "ended": None, "value": 4, "limit": 3, "usd": 1.0},
                {"kind": "tokens-per-turn", "unit": "", "stage": "impl", "ended": None, "value": 41200.0, "limit": 30000.0, "usd": None},
                {"kind": "failed", "unit": "0001_a", "stage": "spec", "ended": None, "value": "exhausted", "limit": None, "usd": None},
            ],
        }
        seen = {}

        def cost(cwd, rounds):
            seen["rounds"] = rounds
            return data

        page = SimpleNamespace(
            cwd="/w", get_value=lambda name: {"0001_a": state.Unit(
                id="0001_a", rounds=[state.Round(number=1, verdict="changes-requested")])},
            _fail=lambda e: None)
        with mock.patch.object(state, "SERVICE", SimpleNamespace(cost=cost)):
            state.StudioState._load_cost(page)
        self.assertEqual(seen["rounds"], {"0001_a": ["changes-requested"]})
        self.assertEqual((page.cost_total_usd, page.cost_total_unknown, page.cost_offset),
                         ("$18.40", "2 unknown", "UTC+07:00"))
        self.assertEqual([(r.key, r.usd, r.unknown, r.over) for r in page.cost_units],
                         [("0001_a", "$18.40", "", True), ("No unit", "—", "2 unknown", False)])
        self.assertEqual(page.cost_days[0].usd, "$0.00456")
        self.assertEqual((page.cost_tokens[0].input, page.cost_tokens[0].output), ("25 (25%)", "75 (75%)"))
        self.assertEqual([(w.label, w.count, w.usd, w.sub) for w in page.cost_waste],
                         [("Changes-requested rounds", "3", "$2.00", False), ("not recorded", "1", "not recorded", True)])
        self.assertEqual([a.measured for a in page.cost_anomalies],
                         ["$18.40 > $15", "4 runs > 3", "41,200 per turn > 3 × 10,000", "exhausted"])
        self.assertEqual(page.cost_anomalies[2].unit, "No unit")


class _ReadOnly(_Page):
    BOARD = {"stages": [], "recording": False, "units": [],
             "read_only_because": "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR",
             "empty_because": "this workspace has no .cos/ — nothing here runs the loop yet"}


class TheBoardNoteNamesNoVariable(unittest.TestCase):
    """`0089` R11 (D55): the page's own sentence in place of `read_only_because`."""

    def test_a_read_only_empty_board(self):
        import asyncio

        from coscc import state as page

        token = "state-test-0089-read-only"

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                await arrive("/board?ws=a", "s1")
                await asyncio.sleep(0.2)
                note = (await _studio(manager, token)).board_note
                await arrive("/sessions?ws=a", "s9")
                await asyncio.sleep(0.15)
                return note

        with _ReadOnly().patches():
            note = asyncio.run(go())
        self.assertEqual(note, page.READ_ONLY_NOTE)
        self.assertNotIn("COS_", note)


ISO = re.compile(r"\d{4}-\d{2}-\d{2}T")


class TimesReadForAReader(unittest.TestCase):
    """`0089` R6, R12 (D52, D60): no raw time reaches `/activity` or the Timeline."""

    def test_an_activity_row_has_a_readers_time(self):
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        row = {"kind": "end", "stage": "plan", "mode": "manual", "outcome": "done", "unit": "0001_x",
               "denials": 0, "artifact": "", "at": "2026-09-25T04:13:29+00:00"}
        feed = {"events": [row], "total": {}}
        page = SimpleNamespace(cwd="/w", events=[], usage_total_tokens="", usage_total_usd="")
        service = SimpleNamespace(activity_and_usage=lambda cwd, limit: feed)
        with mock.patch.object(state, "SERVICE", service):
            state.StudioState._load_activity(page)
        [event] = page.events
        self.assertTrue(event.time)
        self.assertIsNone(ISO.search(event.time), event.time)

    def test_a_timeline_row_has_readers_times_and_its_own_key(self):
        from types import SimpleNamespace
        from unittest import mock

        from coscc import state

        runs = [
            {"stage": "plan", "mode": "manual", "started": "2026-09-25T04:00:00+00:00",
             "ended": "2026-09-25T04:10:00+00:00", "outcome": "done", "session_id": "abc", "run": "r1"},
            {"stage": "impl", "mode": "manual", "started": "2026-09-25T05:00:00+00:00", "ended": None,
             "outcome": None, "session_id": None, "run": ""},
        ]
        page = SimpleNamespace(cwd="/w", unit_id="0001_x", runs=[])
        service = SimpleNamespace(timeline=lambda cwd, unit: {"runs": runs})
        with mock.patch.object(state, "SERVICE", service):
            state.StudioState._load_timeline(page)
        done, running = page.runs
        for field in (done.started, done.ended, running.started):
            self.assertTrue(field)
            self.assertIsNone(ISO.search(field), field)
        self.assertEqual(running.ended, "")
        self.assertFalse(any("still running" in f"{r.started}{r.ended}" for r in page.runs))
        self.assertNotEqual(done.key, running.key)
        self.assertEqual((done.key, running.key), ("r1", "impl-1"))


if __name__ == "__main__":
    unittest.main()
