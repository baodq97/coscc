"""Tests for the read layer and the guards on the session layer.

Nothing here creates a session. Creating one spends account quota (`spec.md` C4), so the
suite stays free to run in a loop; what needs a real session is the proof command, which
is run deliberately.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from coscc import frontend, sessions
from coscc.config import PROTECTED_DB_VAR, Config
from coscc.data import Data
from coscc.sessions import (
    Live,
    Refused,
    Sessions,
    _text_of,
    history,
    list_for_directory,
)


def _options(*args, **kw):
    """`sessions._options` with the `data_dir` every caller must give since `0076`."""
    kw.setdefault("data_dir", tempfile.gettempdir())
    return sessions._options(*args, **kw)


def _child_env(cwd, workspace=None):
    """`sessions.child_env` with the two arguments every caller must give since `0076`."""
    return sessions.child_env(
        cwd, workspace, data_dir=tempfile.gettempdir(), app_db=Data().db_path
    )


def _info(session_id="s1", cwd="/p", summary="sum", **kw):
    return sdk.SDKSessionInfo(
        session_id=session_id,
        summary=summary,
        last_modified=kw.pop("last_modified", 1),
        file_size=kw.pop("file_size", 10),
        custom_title=kw.pop("custom_title", None),
        first_prompt=kw.pop("first_prompt", None),
        git_branch=kw.pop("git_branch", None),
        cwd=cwd,
        tag=None,
        created_at=kw.pop("created_at", 1),
    )


def _msg(type_="user", text="hi", uuid="u1", parent_tool_use_id=None, parent_agent_id=None):
    return sdk.SessionMessage(
        type=type_,
        uuid=uuid,
        session_id="s1",
        message={"role": type_, "content": text},
        parent_tool_use_id=parent_tool_use_id,
        parent_agent_id=parent_agent_id,
    )


class FlatteningStoredContent(unittest.TestCase):
    def test_a_bare_string_is_the_text(self):
        self.assertEqual(_text_of("hello"), "hello")

    def test_blocks_are_joined(self):
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        self.assertEqual(_text_of(blocks), "ab")

    def test_non_text_blocks_are_dropped_not_stringified(self):
        blocks = [{"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "ok"}]
        self.assertEqual(_text_of(blocks), "ok")

    def test_unknown_shapes_are_empty_rather_than_an_error(self):
        self.assertEqual(_text_of(None), "")
        self.assertEqual(_text_of({"role": "user"}), "")


class ListingIsPerProject(unittest.TestCase):
    def test_every_entry_carries_its_cwd(self):
        # R1 is checkable only if the caller can see which project each entry came from.
        with mock.patch.object(sdk, "list_sessions", return_value=[_info(cwd="/p")]):
            rows = list_for_directory("/p")
        self.assertEqual(rows[0]["cwd"], "/p")

    def test_worktrees_are_excluded_so_one_project_is_one_list(self):
        # R1 says no entry of one project may leak into another's list. A worktree of the
        # same repo has a different cwd, so including them would break exactly that.
        with mock.patch.object(sdk, "list_sessions", return_value=[]) as m:
            list_for_directory("/p")
        self.assertFalse(m.call_args.kwargs["include_worktrees"])

    def test_a_custom_title_wins_over_the_generated_summary(self):
        info = _info(summary="generated", custom_title="mine")
        with mock.patch.object(sdk, "list_sessions", return_value=[info]):
            self.assertEqual(list_for_directory("/p")[0]["summary"], "mine")

    def test_a_session_with_no_summary_still_lists(self):
        info = _info(summary="", custom_title=None, first_prompt=None)
        with mock.patch.object(sdk, "list_sessions", return_value=[info]):
            self.assertEqual(list_for_directory("/p")[0]["summary"], "(no summary)")


class HistoryComesFromTheSessionStore(unittest.TestCase):
    def test_messages_keep_their_order_and_role(self):
        msgs = [_msg("user", "q", "u1"), _msg("assistant", "a", "u2")]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            h = history("s1", "/p")
        self.assertEqual([(m["role"], m["text"]) for m in h], [("user", "q"), ("assistant", "a")])

    def test_subagent_traffic_is_not_part_of_the_conversation(self):
        msgs = [_msg("user", "q", "u1"), _msg("assistant", "inner", "u2", parent_tool_use_id="t1")]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            self.assertEqual(len(history("s1", "/p")), 1)

    def test_empty_messages_are_dropped(self):
        msgs = [_msg("user", "   ", "u1"), _msg("user", "real", "u2")]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            self.assertEqual(len(history("s1", "/p")), 1)


def _raw_msg(type_, content, uuid="u", parent_tool_use_id=None, parent_agent_id=None):
    return sdk.SessionMessage(
        type=type_,
        uuid=uuid,
        session_id="s1",
        message={"role": type_, "content": content},
        parent_tool_use_id=parent_tool_use_id,
        parent_agent_id=parent_agent_id,
    )


class TranscriptExcerptIsWhatTheSessionDid(unittest.TestCase):
    """`0019` plan step 2: `transcript_excerpt` never the prompt, always the result."""

    def test_a_tool_results_text_is_in_the_excerpt(self):
        msgs = [
            _raw_msg("user", "do the thing", "u1"),  # the prompt: must not appear
            _raw_msg("assistant", [{"type": "text", "text": "on it"}], "u2"),
            _raw_msg(
                "user",
                [{"type": "tool_result", "content": [{"type": "text", "text": "ran ok"}]}],
                "u3",
            ),
        ]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            excerpt, total = sessions.transcript_excerpt("s1", "/p", 8000)
        self.assertIn("ran ok", excerpt)
        self.assertIn("on it", excerpt)
        self.assertNotIn("do the thing", excerpt)
        self.assertEqual(total, len(excerpt))

    def test_a_bare_string_tool_result_is_kept(self):
        msgs = [_raw_msg("user", [{"type": "tool_result", "content": "plain string"}], "u1")]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            excerpt, _ = sessions.transcript_excerpt("s1", "/p", 8000)
        self.assertIn("plain string", excerpt)

    def test_subagent_traffic_is_excluded(self):
        msgs = [
            _raw_msg("assistant", [{"type": "text", "text": "outer"}], "u1"),
            _raw_msg(
                "assistant", [{"type": "text", "text": "inner"}], "u2", parent_tool_use_id="t1"
            ),
        ]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            excerpt, _ = sessions.transcript_excerpt("s1", "/p", 8000)
        self.assertNotIn("inner", excerpt)

    def test_the_excerpt_is_a_suffix_of_the_full_transcript_and_total_chars_is_exact(self):
        msgs = [
            _raw_msg("assistant", [{"type": "text", "text": "a" * 50}], "u1"),
            _raw_msg(
                "user",
                [{"type": "tool_result", "content": [{"type": "text", "text": "b" * 50}]}],
                "u2",
            ),
        ]
        with mock.patch.object(sdk, "get_session_messages", return_value=msgs):
            full, total = sessions.transcript_excerpt("s1", "/p", 10 ** 9)
            excerpt, total_again = sessions.transcript_excerpt("s1", "/p", 20)
        self.assertTrue(full.endswith(excerpt))
        self.assertEqual(len(excerpt), 20)
        self.assertEqual(total, len(full))
        self.assertEqual(total_again, total)

    def test_an_empty_session_id_is_refused(self):
        with self.assertRaises(ValueError):
            sessions.transcript_excerpt("", "/p", 8000)


class OptionsCarryTheKnobs(unittest.TestCase):
    def test_resume_never_forks(self):
        # spec.md C7. The fork branch returns a new id, everything keeps working, and R3
        # is wrong without a single error. This assertion is the tripwire.
        self.assertFalse(_options(Config(), "/p", resume="s1").fork_session)

    def test_chat_only_reaches_the_sdk_as_an_empty_tool_list(self):
        self.assertEqual(_options(Config(), "/p", None).tools, [])

    def test_permission_mode_follows_knob_3(self):
        self.assertEqual(_options(Config(), "/p", None).permission_mode, "default")
        loose = Config(bypass_permissions=True)
        self.assertEqual(_options(loose, "/p", None).permission_mode, "bypassPermissions")

    def test_project_settings_cannot_widen_the_tool_list(self):
        # A repo's own .claude/settings.json must not be able to grant a tool the four
        # knobs did not. C2b is about the app deciding, not the directory it visits.
        self.assertIsNone(_options(Config(), "/p", None).setting_sources)

    def test_the_prompt_reaches_the_model_as_written(self):
        """No `@path` expansion and no slash-command dispatch, for every session.

        Measured 2026-09-23: with this off, a session holding no tools at all was sent
        `@/tmp/canary.txt` and repeated the word inside the file. Since `0016` a prompt can
        carry text from `POST /api/units/answer`, which anyone who reaches the port can
        send, so this is what stands between that route and any file this user can read.
        """
        self.assertTrue(_options(Config(), "/p", None).verbatim_prompts)
        # A board step's options are built by the same function; asserting it here too
        # keeps a future special case for steps from quietly turning it back off.
        step = _options(Config(), "/p", None, max_turns=50, tools=["Read"])
        self.assertTrue(step.verbatim_prompts)

    def test_write_tools_are_stripped_on_the_way_to_the_sdk(self):
        c = Config(tools=("Read", "Bash"))
        self.assertEqual(_options(c, "/p", None).tools, ["Read"])

    def test_a_resolved_model_wins_over_cos_model(self):
        # `0004_no-setting-says-which-model-runs-a-stage`: the stage's model is passed in.
        c = Config(model="from-env")
        self.assertEqual(_options(c, "/p", None, model="x").model, "x")

    def test_no_resolved_model_falls_back_to_cos_model(self):
        c = Config(model="from-env")
        self.assertEqual(_options(c, "/p", None).model, "from-env")
        self.assertIsNone(_options(Config(), "/p", None).model)

    # `0037`. A board step with tools runs on Claude Code's own system prompt; nothing
    # else does, and the preset must not move any knob that decides what a step may do.

    def test_no_system_prompt_is_set_unless_asked(self):
        # R2: chat and tool-less steps keep the SDK's default, as before.
        self.assertIsNone(_options(Config(), "/p", None).system_prompt)

    def test_a_preset_reaches_the_options_as_given(self):
        # R1 at the options layer. No `append`: the spec keeps the preset bare.
        from coscc.runner import CLAUDE_CODE_PRESET

        got = _options(Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET).system_prompt
        self.assertEqual(got, {"type": "preset", "preset": "claude_code"})
        self.assertNotIn("append", got)
        # A copy, so nothing downstream can edit the module's constant through it.
        self.assertIsNot(got, CLAUDE_CODE_PRESET)

    def test_a_preset_changes_nothing_else(self):
        # R3: the grant is `tools` + `can_use_tool` + `permission_mode`, and project
        # settings stay out. The preset may move none of them, in either permission mode.
        from coscc import policy
        from coscc.runner import CLAUDE_CODE_PRESET

        def gate(name, data, ctx):  # never called; compared by identity
            raise AssertionError("not called")

        for config in (Config(), Config(bypass_permissions=True)):
            common = dict(max_turns=40, tools=list(policy.READ_TOOLS), can_use_tool=gate)
            bare = _options(config, "/p", None, **common)
            preset = _options(config, "/p", None, system_prompt=CLAUDE_CODE_PRESET, **common)
            self.assertEqual(preset.tools, bare.tools)
            self.assertIs(preset.can_use_tool, gate)
            self.assertIs(bare.can_use_tool, gate)
            self.assertIsNone(preset.setting_sources)
            self.assertTrue(preset.verbatim_prompts)
            self.assertEqual(preset.permission_mode, bare.permission_mode)
            self.assertEqual(preset.max_turns, bare.max_turns)

    # `0033`. Effort is chosen per stage and label; `_options` only carries it.

    def test_the_installed_sdk_has_an_effort_field(self):
        # spec.md C6: the field is known only from a file outside this repository, so the
        # installed SDK is asked before anything is built on it.
        import dataclasses

        self.assertIn("effort", {f.name for f in dataclasses.fields(sdk.ClaudeAgentOptions)})

    def test_an_effort_reaches_the_options(self):
        self.assertEqual(_options(Config(), "/p", None, effort="high").effort, "high")

    def test_no_effort_leaves_the_sdk_default(self):
        default = sdk.ClaudeAgentOptions().effort
        self.assertEqual(_options(Config(), "/p", None).effort, default)


class GuardsRefuseBeforeSpendingQuota(unittest.IsolatedAsyncioTestCase):
    async def test_a_directory_outside_the_workspaces_is_refused(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        with self.assertRaises(Refused) as e:
            await s.send("/etc", "hi")
        self.assertIn("workspace", str(e.exception))

    async def test_a_foreign_session_is_refused_while_knob_4_is_off(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        with self.assertRaises(Refused) as e:
            await s.send("/tmp", "hi", session_id="not-ours")
        self.assertIn("not created by this app", str(e.exception))

    async def test_adopting_a_session_makes_it_resumable(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        self.assertFalse(s.created_here("x"))
        s.adopt("x")
        self.assertTrue(s.created_here("x"))

    async def test_knob_4_on_allows_a_foreign_session(self):
        s = Sessions(Config(workspaces=("/tmp",), resume_foreign_sessions=True))
        # Gets past the guard and fails later, at connect — which is the point: the
        # refusal is no longer what stops it.
        with mock.patch("coscc.sessions.ClaudeSDKClient", side_effect=RuntimeError("connect")):
            with self.assertRaises(RuntimeError):
                await s.send("/tmp", "hi", session_id="not-ours")

    async def test_closing_nothing_is_not_an_error(self):
        await Sessions(Config()).close_all()


class _FakeClient:
    """A `ClaudeSDKClient` stand-in: replays a fixed message list, spends nothing."""

    messages: list = []

    def __init__(self, options=None):
        pass

    async def connect(self):
        pass

    async def query(self, text):
        pass

    async def receive_response(self):
        for m in self.messages:
            yield m

    async def disconnect(self):
        pass


def _assistant(text, session_id):
    return sdk.AssistantMessage(
        content=[sdk.TextBlock(text=text)], model="m", session_id=session_id
    )


def _result(session_id, turns=3, cost=0.5):
    return sdk.ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=False,
        num_turns=turns, session_id=session_id, total_cost_usd=cost,
    )


class TheSessionIdIsToldBeforeTheStepIsOver(unittest.IsolatedAsyncioTestCase):
    """`0019` plan step 2: `("session", id)` once, as soon as it is known, and the
    accounting of the `ResultMessage` is untouched by it."""

    async def _run(self, messages, session_id=None, adopt=None):
        s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(s.close_all)  # a chat keeps its data root until closed (`0076`)
        if adopt:
            s.adopt(adopt)
        _FakeClient.messages = messages
        with mock.patch("coscc.sessions.ClaudeSDKClient", _FakeClient):
            return [item async for item in s.stream("/tmp", "hi", session_id=session_id)]

    async def test_a_new_session_says_its_id_once_before_done(self):
        items = await self._run([_assistant("a", "sid-1"), _assistant("b", "sid-1"), _result("sid-1")])
        kinds = [k for k, _ in items]
        self.assertEqual(kinds.count("session"), 1)
        self.assertLess(kinds.index("session"), kinds.index("done"))
        self.assertEqual(dict(items)["session"], "sid-1")

    async def test_the_result_is_still_accounted_after_the_id_was_told(self):
        items = await self._run([_assistant("a", "sid-2"), _result("sid-2", turns=7)])
        done = dict(items)["done"]
        self.assertEqual(done["cost"]["turns"], 7)
        self.assertEqual(done["cost"]["cost_usd"], 0.5)

    async def test_a_resumed_session_is_told_first_and_still_accounted(self):
        items = await self._run([_result("sid-3", turns=4)], session_id="sid-3", adopt="sid-3")
        self.assertEqual(items[0], ("session", "sid-3"))
        self.assertEqual([k for k, _ in items].count("session"), 1)
        self.assertEqual(dict(items)["done"]["cost"]["turns"], 4)


class _CountingClient(_FakeClient):
    """Counts `disconnect`, and can hold `receive_response` open until released."""

    made: list = []
    hold = None  # an asyncio.Event to wait on after the first message, or None
    fail = False

    def __init__(self, options=None):
        self.disconnects = 0
        _CountingClient.made.append(self)

    async def receive_response(self):
        for i, m in enumerate(self.messages):
            if i == 1 and self.hold is not None:
                await self.hold.wait()
            if i == 1 and self.fail:
                raise RuntimeError("the CLI died")
            yield m

    async def disconnect(self):
        self.disconnects += 1


class WhichChatTurnsAreAnswering(unittest.IsolatedAsyncioTestCase):
    """`0068` R8. `in_flight` names each chat turn answering now; a board step never."""

    def setUp(self):
        self.s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(self.s.close_all)  # a chat keeps its data root until closed
        self.ended = 0

        def ended():
            self.ended += 1

        self.s.on_turn_end = ended
        _CountingClient.made = []
        _CountingClient.hold = asyncio.Event()
        _CountingClient.fail = False
        _CountingClient.messages = [_assistant("a", "sid-t"), _result("sid-t")]
        patcher = mock.patch("coscc.sessions.ClaudeSDKClient", _CountingClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _reader(self, **kw):
        return [item async for item in self.s.stream("/tmp", "hi", **kw)]

    async def _until_told(self):
        for _ in range(100):
            turns = self.s.in_flight()
            if turns and turns[0]["session_id"]:
                return turns
            await asyncio.sleep(0.01)
        self.fail("the turn never told its session id")

    async def test_a_turn_is_listed_while_it_answers_and_gone_when_done(self):
        task = asyncio.create_task(self._reader())
        turns = await self._until_told()
        self.assertEqual(len(turns), 1)
        self.assertEqual((turns[0]["session_id"], turns[0]["workspace"]), ("sid-t", "/tmp"))
        self.assertTrue(turns[0]["started"])
        _CountingClient.hold.set()
        await task
        self.assertEqual((self.s.in_flight(), self.ended), ([], 1))

    async def test_a_turn_that_raises_is_gone(self):
        _CountingClient.fail = True
        task = asyncio.create_task(self._reader())
        await self._until_told()
        _CountingClient.hold.set()
        with self.assertRaises(RuntimeError):
            await task
        self.assertEqual(self.s.in_flight(), [])

    async def test_a_cut_turn_is_gone_and_its_reader_ends(self):
        task = asyncio.create_task(self._reader())
        turns = await self._until_told()
        self.assertTrue(await self.s.cut_turn(turns[0]["id"]))
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.s.in_flight(), [])
        self.assertFalse(await self.s.cut_turn(turns[0]["id"]))

    async def test_a_board_step_is_never_a_chat_turn(self):
        seen = []
        _CountingClient.hold = None

        async def watch():
            async for _ in self.s.stream("/tmp", "hi", step=sessions.StepHandle()):
                seen.append(self.s.in_flight())

        await watch()
        self.assertTrue(seen)
        self.assertTrue(all(turns == [] for turns in seen))


class _Transport:
    def __init__(self):
        self.closes = 0

    async def close(self):
        self.closes += 1


class _StartingClient(_CountingClient):
    """Shaped like the SDK's own around `connect`: the transport (the CLI process) exists
    from the start of `connect`, the control protocol only once it returns, and
    `disconnect` closes nothing without the latter -- it only drops the transport."""

    made: list = []
    spawned: asyncio.Event
    go: asyncio.Event

    def __init__(self, options=None):
        self.transport = _Transport()
        self._transport = None
        self._query = None
        self.closed_connected = 0
        self.queries = 0
        _StartingClient.made.append(self)

    async def connect(self):
        self._transport = self.transport
        _StartingClient.spawned.set()
        await _StartingClient.go.wait()
        self._query = object()

    async def query(self, text):
        self.queries += 1

    async def disconnect(self):
        if self._query is not None:
            self.closed_connected += 1
            await self._transport.close()
            self._query = None
        self._transport = None


class _SlowClient(_CountingClient):
    """A client whose `disconnect` holds until released, and says if it was cut short."""

    def __init__(self, options=None):
        super().__init__(options)
        self.messages = [_assistant("a", "sid-s"), _result("sid-s")]
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def disconnect(self):
        self.disconnects += 1
        self.closing.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _Process:
    """Shaped like the process an SDK transport holds: `terminate`, `kill`, `returncode`
    and `wait`. `obeys` is whether SIGTERM ends it."""

    def __init__(self, obeys=True):
        self.obeys = obeys
        self.returncode = None
        self.signals = []
        self._gone = asyncio.Event()

    def terminate(self):
        self.signals.append("TERM")
        if self.obeys:
            self._exit(-15)

    def kill(self):
        self.signals.append("KILL")
        self._exit(-9)

    def _exit(self, code):
        self.returncode = code
        self._gone.set()

    async def wait(self):
        await self._gone.wait()
        return self.returncode


class _StubbornClient(_CountingClient):
    """Shaped like the SDK's close around a CLI that does not exit on stdin EOF: its
    `disconnect` waits on the process for as long as it takes."""

    def __init__(self, process):
        super().__init__()
        self._transport = mock.Mock(_process=process)
        self._query = object()
        self.finished = False
        self.cancelled = False

    async def disconnect(self):
        self.disconnects += 1
        try:
            await self._transport._process.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True


class ACliThatOutlastsTheSdksCloseIsStillEnded(unittest.IsolatedAsyncioTestCase):
    """`0034` review round 2, F3. The app's own timeout used to cancel the SDK's close
    before its SIGTERM/SIGKILL, so a CLI that ignored stdin EOF was never signalled."""

    def setUp(self):
        for name, value in (("DISCONNECT_TIMEOUT", 0.05), ("KILL_AFTER", 0.05)):
            patcher = mock.patch.object(sessions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_it_is_terminated_and_the_sdks_close_is_not_cut_short(self):
        process = _Process(obeys=True)
        client = _StubbornClient(process)
        h = sessions.StepHandle(client=client)
        await h.close()
        self.assertEqual(process.signals, ["TERM"])
        await asyncio.sleep(0)
        self.assertTrue(client.finished)
        self.assertFalse(client.cancelled)

    async def test_one_that_ignores_sigterm_is_killed(self):
        process = _Process(obeys=False)
        h = sessions.StepHandle(client=_StubbornClient(process))
        await h.close()
        self.assertEqual(process.signals, ["TERM", "KILL"])
        self.assertEqual(process.returncode, -9)

    async def test_one_that_exits_in_time_is_not_signalled(self):
        process = _Process()
        process._exit(0)
        h = sessions.StepHandle(client=_StubbornClient(process))
        await h.close()
        self.assertEqual(process.signals, [])

    async def test_cancelling_the_caller_does_not_cancel_the_closing(self):
        process = _Process(obeys=False)
        client = _StubbornClient(process)
        h = sessions.StepHandle(client=client)
        caller = asyncio.create_task(h.close())
        await asyncio.sleep(0.01)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        await h.close()
        self.assertEqual(process.signals, ["TERM", "KILL"])
        self.assertFalse(client.cancelled)

    async def test_the_installed_sdks_transport_is_ended(self):
        """The SDK's real transport and a real process that ignores both stdin EOF and
        SIGTERM: this is what reads `_transport` and `_process` against the installed SDK."""
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "claude"
            cli.write_text(
                f"#!{sys.executable}\n"
                "import signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(60)\n"
            )
            cli.chmod(0o755)
            transport = SubprocessCLITransport(
                prompt="", options=sdk.ClaudeAgentOptions(cli_path=str(cli), cwd=tmp)
            )
            with mock.patch.dict(os.environ, {"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1"}):
                await transport.connect()
            process = transport._process
            self.assertIsNone(process.returncode)

            class Client:
                _transport = transport
                _query = object()

                async def disconnect(self):
                    await self._transport.close()

            await asyncio.sleep(0.2)  # let the script install its handler
            await sessions.StepHandle(client=Client()).close()
            await asyncio.wait_for(process.wait(), 2)
            self.assertEqual(process.returncode, -9)


class AStepsClientIsClosedWhenTheStepEnds(unittest.IsolatedAsyncioTestCase):
    """`0034`. A board step's client is closed however the step ends, exactly once, and
    never kept in `_live` for resuming. Chat's clients still are."""

    def setUp(self):
        _CountingClient.made = []
        _CountingClient.hold = None
        _CountingClient.fail = False
        _CountingClient.messages = [_assistant("a", "sid-s"), _result("sid-s")]
        _StartingClient.made = []
        _StartingClient.spawned = asyncio.Event()
        _StartingClient.go = asyncio.Event()
        patcher = mock.patch("coscc.sessions.ClaudeSDKClient", _CountingClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(self.s.close_all)  # a chat keeps its data root until closed

    async def test_a_step_that_finishes_is_closed_once_and_not_kept(self):
        h = sessions.StepHandle()
        items = [i async for i in self.s.stream("/tmp", "hi", step=h)]
        self.assertEqual(items[-1][0], "done")
        [client] = _CountingClient.made
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(self.s._live, {})
        self.assertEqual(self.s._steps, set())
        self.assertTrue(self.s.created_here("sid-s"))

    async def test_a_step_that_raises_is_closed_once(self):
        _CountingClient.fail = True
        with self.assertRaises(RuntimeError):
            async for _ in self.s.stream("/tmp", "hi", step=sessions.StepHandle()):
                pass
        [client] = _CountingClient.made
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(self.s._live, {})

    async def test_a_step_abandoned_by_its_reader_is_closed_once(self):
        _CountingClient.hold = asyncio.Event()
        agen = self.s.stream("/tmp", "hi", step=sessions.StepHandle())
        async for kind, _ in agen:
            if kind == "chunk":
                break
        await agen.aclose()
        [client] = _CountingClient.made
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(self.s._steps, set())

    async def test_a_cancelled_step_is_closed_once(self):
        _CountingClient.hold = asyncio.Event()
        h = sessions.StepHandle()
        started = asyncio.Event()

        async def run():
            async for kind, _ in self.s.stream("/tmp", "hi", step=h):
                started.set()

        task = asyncio.create_task(run())
        await started.wait()
        self.assertEqual(self.s.live_in("/tmp"), ["(running step)"])
        await h.close()
        await h.close()  # a second Stop is the same Stop
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        [client] = _CountingClient.made
        self.assertEqual(client.disconnects, 1)
        self.assertEqual(self.s.live_in("/tmp"), [])

    async def test_a_handle_closed_before_connect_sends_no_prompt(self):
        h = sessions.StepHandle()
        await h.close()
        with self.assertRaises(Refused):
            async for _ in self.s.stream("/tmp", "hi", step=h):
                pass
        [client] = _CountingClient.made
        self.assertEqual(client.disconnects, 1)

    async def test_a_stop_while_the_cli_starts_still_closes_it_once_connected(self):
        """Review round 1, F1: a `disconnect` during `connect` is empty in the SDK, so a
        Stop there must not count as the close."""
        h = sessions.StepHandle()
        with mock.patch("coscc.sessions.ClaudeSDKClient", _StartingClient):
            task = asyncio.create_task(self._drain(h))
            await _StartingClient.spawned.wait()
            await h.close()
            _StartingClient.go.set()
            with self.assertRaises(Refused):
                await task
        [client] = _StartingClient.made
        self.assertEqual(client.closed_connected, 1)
        self.assertEqual(client.queries, 0)
        self.assertEqual(self.s._steps, set())

    async def test_a_cancel_while_the_cli_starts_closes_what_was_spawned(self):
        h = sessions.StepHandle()
        with mock.patch("coscc.sessions.ClaudeSDKClient", _StartingClient):
            task = asyncio.create_task(self._drain(h))
            await _StartingClient.spawned.wait()
            await h.close()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        [client] = _StartingClient.made
        self.assertEqual(client.transport.closes, 1)
        self.assertEqual(client.queries, 0)
        self.assertEqual(self.s._steps, set())

    async def _drain(self, h):
        async for _ in self.s.stream("/tmp", "hi", step=h):
            pass

    async def test_a_stop_that_cancels_the_closing_still_removes_the_step(self):
        """Review round 2, F4: `task.cancel()` landing on `stream`'s own close left the
        handle in `_steps`, and `live_in` saying so until a restart."""
        h = sessions.StepHandle()
        client = _SlowClient()
        with mock.patch("coscc.sessions.ClaudeSDKClient", lambda options=None: client):
            task = asyncio.create_task(self._drain(h))
            await client.closing.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.s._steps, set())
        self.assertEqual(self.s.live_in("/tmp"), [])
        client.release.set()
        await h.close()  # the closing the cancel did not reach
        self.assertEqual(client.disconnects, 1)
        self.assertFalse(client.cancelled)


    async def test_chat_still_keeps_its_client_for_resuming(self):
        [_ async for _ in self.s.stream("/tmp", "hi")]
        self.assertIn("sid-s", self.s._live)
        self.assertEqual(_CountingClient.made[0].disconnects, 0)

    async def test_close_all_closes_a_step_in_flight(self):
        _CountingClient.hold = asyncio.Event()
        started = asyncio.Event()

        async def run():
            async for kind, _ in self.s.stream("/tmp", "hi", step=sessions.StepHandle()):
                started.set()

        task = asyncio.create_task(run())
        await started.wait()
        await self.s.close_all()
        self.assertEqual(_CountingClient.made[0].disconnects, 1)
        self.assertEqual(self.s._steps, set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(_CountingClient.made[0].disconnects, 1)


class WhichWorkspacesHaveSomeoneInThem(unittest.TestCase):
    """R6. The question `pull` has to ask before it touches a workspace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.a = self.root / "a"
        self.b = self.root / "b"
        self.a.mkdir()
        self.b.mkdir()
        self.s = Sessions(Config())

    def _live(self, session_id: str, cwd: Path) -> None:
        # The client is never touched by `live_in`, so a stand-in is enough here. A real
        # one would spend quota to test a dict lookup.
        self.s._live[session_id] = Live(client=object(), session_id=session_id, cwd=str(cwd))

    def test_an_empty_list_when_nothing_is_open(self):
        self.assertEqual(self.s.live_in(str(self.a)), [])

    def test_the_session_in_that_directory_and_only_that_one(self):
        self._live("in-a", self.a)
        self._live("in-b", self.b)
        self.assertEqual(self.s.live_in(str(self.a)), ["in-a"])
        self.assertEqual(self.s.live_in(str(self.b)), ["in-b"])

    def test_every_session_in_the_directory_not_just_the_first(self):
        self._live("one", self.a)
        self._live("two", self.a)
        self.assertEqual(sorted(self.s.live_in(str(self.a))), ["one", "two"])

    def test_the_same_directory_written_differently_is_the_same_directory(self):
        """A string compare would let `pull` through on `a/../a` — R6 by accident."""
        self._live("in-a", self.a)
        self.assertEqual(self.s.live_in(f"{self.a}/../a"), ["in-a"])
        self.assertEqual(self.s.live_in(f"{self.a}/"), ["in-a"])

    def test_a_parent_directory_does_not_count_as_that_session(self):
        """`pull` on the working folder must not be refused by a session one level down."""
        self._live("in-a", self.a)
        self.assertEqual(self.s.live_in(str(self.root)), [])

    def test_an_unresolvable_directory_is_no_sessions_rather_than_a_crash(self):
        self._live("in-a", self.a)
        self.assertEqual(self.s.live_in("\x00"), [])

    def test_closing_the_session_empties_it(self):
        self._live("in-a", self.a)
        self.s._live.pop("in-a")
        self.assertEqual(self.s.live_in(str(self.a)), [])


if __name__ == "__main__":
    unittest.main()


class TheAppDoesNotHandItsOwnEnvironmentToASession(unittest.TestCase):
    """`REFLEX_WEB_WORKDIR` reaching a session is what destroyed a served bundle, twice.

    Measured 2026-09-23: `coscc/run.py` sets it process-wide, a board step inherited it,
    ran a build, and Reflex compiled into the installed package instead of the workspace.
    The page answered 404 while the API stayed healthy.

    **These assertions are about the value the child would read, not about this dict.**
    The first version of this class asserted the key was absent from `child_env` and
    passed while the bug shipped: `claude_agent_sdk` inherits `os.environ` and lays
    `options.env` on top, so a key left out is a key inherited. A test for a boundary has
    to be written in the terms of the far side of it.
    """

    def child(self, cwd="/w"):
        """What the session process would actually see, composed the way the SDK does."""
        inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        inherited.update(_child_env(cwd))
        return inherited

    def test_the_web_workdir_the_child_reads_is_the_workspace_not_this_app(self):
        with mock.patch.dict(os.environ, {frontend.WEB_WORKDIR_VAR: "/installed/_web"}):
            self.assertEqual(
                self.child("/w")[frontend.WEB_WORKDIR_VAR], str(Path("/w") / ".web")
            )

    def test_no_setting_of_this_app_reaches_the_child_with_a_value(self):
        """Every `COS_*` is blank but one: `COS_DATA_DIR` is the session's own (`0076`)."""
        with mock.patch.dict(os.environ, {"COS_DATA_DIR": "/d", "COS_PORT": "1"}):
            child = self.child()
            self.assertEqual(
                [k for k, v in child.items() if k.startswith("COS_") and v], ["COS_DATA_DIR"]
            )
            self.assertEqual(child["COS_DATA_DIR"], tempfile.gettempdir())

    def test_the_settings_the_child_reads_still_load(self):
        """`0017` review F1: the child reads `COS_PORT=""`, and `config.from_env` must
        take that as unset. A worktree's own `npm test` loads the config, and `int("")`
        errored seven of its tests whenever the app had been given a port."""
        from coscc import config
        with mock.patch.dict(os.environ, {"COS_HOST": "127.0.0.1", "COS_PORT": "9999"}):
            c = config.from_env(self.child())
            self.assertEqual((c.host, c.port), ("0.0.0.0", 8790))

    def test_everything_else_is_left_alone(self):
        """Overridden, not replaced. A session that loses `HOME` cannot sign in."""
        with mock.patch.dict(os.environ, {"HOME": "/home/someone", "PATH": "/bin"}):
            child = self.child()
            self.assertEqual(child["HOME"], "/home/someone")
            self.assertEqual(child["PATH"], "/bin")

    # -- `0017` R7: a unit's session reads its own tree, not the workspace's --------

    def unit_child(self, cwd, workspace):
        inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        inherited.update(_child_env(cwd, workspace))
        return inherited

    def test_the_virtualenv_the_child_reads_is_the_worktrees(self):
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/ws/.venv"}):
            self.assertEqual(self.unit_child("/wt", "/ws")["VIRTUAL_ENV"], str(Path("/wt") / ".venv"))

    def test_no_path_entry_under_the_workspace_or_the_package_reaches_the_child(self):
        import coscc
        pkg = str(Path(coscc.__file__).resolve().parent / "bin")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(["/ws/.venv/bin", pkg, "/usr/bin"])}):
            self.assertEqual(self.unit_child("/wt", "/ws")["PATH"], "/usr/bin")

    def test_no_reflex_flag_of_this_process_reaches_the_child_with_a_value(self):
        with mock.patch.dict(os.environ, {
            "__REFLEX_SKIP_COMPILE": "1", "__REFLEX_MOUNT_FRONTEND_COMPILED_APP": "1",
        }):
            child = self.unit_child("/wt", "/ws")
            self.assertEqual([k for k, v in child.items() if k.startswith("__REFLEX_") and v], [])
            self.assertEqual(child[frontend.WEB_WORKDIR_VAR], str(Path("/wt") / ".web"))


class _EnvClient(_CountingClient):
    """Records the environment each client was built with, and whether its data root
    existed at that moment. `boom` makes the constructor itself raise, after recording."""

    envs: list = []
    boom = False

    def __init__(self, options=None):
        env = dict(options.env)
        env["_existed"] = Path(env["COS_DATA_DIR"]).is_dir()
        _EnvClient.envs.append(env)
        if _EnvClient.boom:
            raise RuntimeError("the client could not be built")
        super().__init__(options)


class EverySessionGetsADataRootOfItsOwn(unittest.IsolatedAsyncioTestCase):
    """`0076` R1-R4, read off what the client was built with."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = self.tmp / "app"
        self.s = Sessions(Config(workspaces=("/tmp",), data_dir=str(self.app)))
        self.addAsyncCleanup(self.s.close_all)
        _EnvClient.envs = []
        _EnvClient.boom = False
        _CountingClient.made = []
        _CountingClient.hold = None
        _CountingClient.fail = False
        _CountingClient.messages = [_assistant("a", "sid-d"), _result("sid-d")]
        patcher = mock.patch("coscc.sessions.ClaudeSDKClient", _EnvClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _step(self, h=None):
        return [i async for i in self.s.stream("/tmp", "hi", step=h or sessions.StepHandle())]

    def _root(self, env):
        return Path(env["COS_DATA_DIR"])

    async def test_r1_an_absolute_existing_directory_apart_from_the_apps(self):
        await self._step()
        [env] = _EnvClient.envs
        root = self._root(env)
        self.assertTrue(env["COS_DATA_DIR"])
        self.assertTrue(root.is_absolute())
        self.assertTrue(env["_existed"])
        app, mine = self.app.resolve(), root.resolve()
        self.assertNotEqual(mine, app)
        self.assertNotIn(app, mine.parents)
        self.assertNotIn(mine, app.parents)

    async def test_r2_two_steps_get_two_directories(self):
        await self._step()
        await self._step()
        first, second = (self._root(e) for e in _EnvClient.envs)
        self.assertNotEqual(first, second)

    async def test_r3_gone_after_a_step_that_finishes(self):
        await self._step()
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_r3_gone_after_a_step_that_raises(self):
        _CountingClient.fail = True
        with self.assertRaises(RuntimeError):
            await self._step()
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_r3_gone_after_a_step_stopped_by_its_ceiling(self):
        capped = sdk.ResultMessage(
            subtype="error_max_budget_usd", duration_ms=1, duration_api_ms=1, is_error=True,
            num_turns=9, session_id="sid-d", total_cost_usd=8.0,
        )
        _CountingClient.messages = [_assistant("a", "sid-d"), capped]
        items = await self._step()
        self.assertEqual(dict(items)["done"]["terminal_reason"], "error_max_budget_usd")
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_r3_gone_after_a_step_is_stopped(self):
        _CountingClient.hold = asyncio.Event()
        h = sessions.StepHandle()
        started = asyncio.Event()

        async def run():
            async for _ in self.s.stream("/tmp", "hi", step=h):
                started.set()

        task = asyncio.create_task(run())
        await started.wait()
        self.assertTrue(self._root(_EnvClient.envs[0]).is_dir())
        await h.close()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_r3_a_stop_that_cancels_the_close_waits_for_the_closing(self):
        """Review round 1, F1: the directory went while the CLI the cancelled close was
        still ending could open a `Data` and make it again."""
        h = sessions.StepHandle()
        client = _SlowClient()
        with mock.patch("coscc.sessions.ClaudeSDKClient", lambda options=None: client):
            task = asyncio.create_task(self._step(h))
            await client.closing.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(h.scratch.is_dir(), "the CLI may still be running")
        client.release.set()
        await h.close()  # the closing the cancel did not reach
        await asyncio.sleep(0)
        self.assertFalse(h.scratch.exists())

    async def test_r3_a_stop_during_a_failed_connect_waits_for_the_abandoning(self):
        """Review round 2, F2: the same, when the cancel lands on the closing of a client
        whose `connect` did not finish -- the handle had no client to wait on."""
        h = sessions.StepHandle()
        client = _SlowClient()
        connecting = asyncio.Event()

        async def connect():
            connecting.set()
            await asyncio.Event().wait()

        client.connect = connect
        with mock.patch("coscc.sessions.ClaudeSDKClient", lambda options=None: client):
            task = asyncio.create_task(self._step(h))
            await connecting.wait()
            task.cancel()  # lands on `connect`: the step abandons the client
            await client.closing.wait()
            task.cancel()  # the Stop's, landing on the abandoning
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(h.scratch.is_dir(), "the CLI may still be running")
        client.release.set()
        await h._closing
        await asyncio.sleep(0)
        self.assertFalse(h.scratch.exists())

    async def test_r3_gone_when_the_client_cannot_be_built(self):
        _EnvClient.boom = True
        with self.assertRaises(RuntimeError):
            await self._step()
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_a_chats_directory_lasts_until_the_chat_is_closed(self):
        [_ async for _ in self.s.stream("/tmp", "hi")]
        root = self._root(_EnvClient.envs[0])
        self.assertTrue(root.is_dir())
        [_ async for _ in self.s.stream("/tmp", "again", session_id="sid-d")]
        self.assertEqual(len(_EnvClient.envs), 1, "the second turn reused the client")
        self.assertTrue(root.is_dir())
        await self.s.close("sid-d")
        self.assertFalse(root.exists())

    async def test_a_chat_whose_client_cannot_be_built_leaves_nothing(self):
        _EnvClient.boom = True
        with self.assertRaises(RuntimeError):
            [_ async for _ in self.s.stream("/tmp", "hi")]
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_r4_the_apps_database_is_protected_and_an_outer_one_kept(self):
        with mock.patch.dict(os.environ, {PROTECTED_DB_VAR: "/outer/cos.db"}):
            await self._step()
        listed = _EnvClient.envs[0][PROTECTED_DB_VAR].split(os.pathsep)
        self.assertEqual(listed, ["/outer/cos.db", str(self.app.resolve() / "cos.db")])


class OnlyAScratchDirectoryIsEverRemoved(unittest.TestCase):
    """`0076` plan Risk 1: `_drop` is an `rmtree`, so it must refuse anything else."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_directory_scratch_dir_made_is_removed(self):
        made = sessions.scratch_dir(self.tmp / "app")
        (made / "cos.db").write_text("x", encoding="utf-8")
        sessions._drop(made)
        self.assertFalse(made.exists())

    def test_anything_else_is_left_alone(self):
        plain = self.tmp / "keep"
        plain.mkdir()
        nested = self.tmp / f"{sessions.SCRATCH_PREFIX}nested"
        nested.mkdir()
        link = Path(tempfile.gettempdir()) / f"{sessions.SCRATCH_PREFIX}link-{os.getpid()}"
        link.symlink_to(plain)
        self.addCleanup(link.unlink)
        for path in (plain, nested, link):
            sessions._drop(path)
        self.assertTrue(plain.is_dir())
        self.assertTrue(nested.is_dir())
        self.assertTrue(link.is_symlink())

    def test_a_temp_dir_inside_the_apps_root_is_refused(self):
        app = self.tmp / "app"
        inside = app / "tmp"
        inside.mkdir(parents=True)
        with mock.patch.object(tempfile, "tempdir", str(inside)):
            with self.assertRaises(Refused):
                sessions.scratch_dir(app)
        self.assertEqual(list(inside.iterdir()), [])
