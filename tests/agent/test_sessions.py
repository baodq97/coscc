"""Tests for the read layer and the guards on the session layer.

Nothing here creates a session. Creating one spends account quota, so the suite stays free to run in
a loop; what needs a real session is the proof command, which is run deliberately."""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from coscc.agent import sessions
from coscc.agent.helpers import Denials, Gate, Helpers
from coscc.agent.policy import Grant
from coscc.config import PROTECTED_DB_VAR, Config
from coscc.store.db import Data
from coscc.agent.sessions import (
    Live,
    Refused,
    Sessions,
    history,
    list_for_directory,
)


# What every gate of these tests denies; a real run's are the app's (`sessions.secrets_of`).
SECRETS = ("/data/cos.db",)


def _grant(**kw) -> Grant:
    """A run's grant in `/p`, holding `kw`."""
    return Grant(**{"cwd": "/p", "secrets": SECRETS, **kw})


def _options(*args, **kw):
    kw.setdefault("data_dir", tempfile.gettempdir())
    kw.setdefault("gate", Gate(_grant()))
    return sessions._options(*args, **kw)


def _child_env(cwd, workspace=None, scratch=None):
    return sessions.child_env(
        cwd, workspace, data_dir=tempfile.gettempdir(), app_db=Data().db_path, scratch=scratch
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


class ListingIsPerProject(unittest.TestCase):
    def test_every_entry_carries_its_cwd(self):
        with mock.patch.object(sdk, "list_sessions", return_value=[_info(cwd="/p")]):
            rows = list_for_directory("/p")
        self.assertEqual(rows[0]["cwd"], "/p")

    def test_worktrees_are_excluded_so_one_project_is_one_list(self):
        # A worktree of the same repo has a different cwd, so including them would break exactly
        # that.
        with mock.patch.object(sdk, "list_sessions", return_value=[]) as m:
            list_for_directory("/p")
        self.assertFalse(m.call_args.kwargs["include_worktrees"])


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
    """`transcript_excerpt` never the prompt, always the result."""

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
            full, total = sessions.transcript_excerpt("s1", "/p", 10**9)
            excerpt, total_again = sessions.transcript_excerpt("s1", "/p", 20)
        self.assertTrue(full.endswith(excerpt))
        self.assertEqual(len(excerpt), 20)
        self.assertEqual(total, len(full))
        self.assertEqual(total_again, total)

    def test_an_empty_session_id_is_refused(self):
        with self.assertRaises(ValueError):
            sessions.transcript_excerpt("", "/p", 8000)


class OptionsCarryTheKnobs(unittest.TestCase):
    def test_project_settings_cannot_widen_the_tool_list(self):
        # A repo's own .claude/settings.json must not be able to grant a tool the four knobs did
        # not. C2b is about the app deciding, not the directory it visits.
        options = _options(Config(), "/p", None)
        self.assertEqual(options.setting_sources, [])
        self.assertIs(options.strict_mcp_config, True)
        self.assertEqual(options.mcp_servers, {})

    def test_the_apps_own_submit_server_is_the_only_one_a_step_gets(self):
        """The server the runner hands in, and strict config kept beside it."""
        from coscc.units.submit import Channel

        server = Channel(
            run="r1", stage="spec", directory="/nonexistent", artifact="spec.md", own=False
        ).server()
        options = _options(Config(), "/p", None, mcp_servers={"cos": server})
        self.assertEqual(list(options.mcp_servers), ["cos"])
        self.assertEqual(options.mcp_servers["cos"]["type"], "sdk")
        self.assertIs(options.strict_mcp_config, True)

    def test_the_prompt_reaches_the_model_as_written(self):
        """No `@path` expansion and no slash-command dispatch, for every session.

        Measured 2026-09-23: with this off, a session holding no tools at all was sent
        `@/tmp/canary.txt` and repeated the word inside the file."""
        self.assertTrue(_options(Config(), "/p", None).verbatim_prompts)
        # A board step's options are built by the same function; asserting it here too
        # keeps a future special case for steps from quietly turning it back off.
        step = _options(Config(), "/p", None, max_turns=50, tools=["Read"])
        self.assertTrue(step.verbatim_prompts)

    def test_write_tools_are_stripped_on_the_way_to_the_sdk(self):
        c = Config(tools=("Read", "Bash"))
        self.assertEqual(_options(c, "/p", None).tools, ["Read"])

    # A board step with tools runs on Claude Code's own system prompt; nothing else does, and the
    # preset must not move any knob that decides what a step may do.

    def test_no_system_prompt_is_set_unless_asked(self):
        # Chat and tool-less steps keep the SDK's default, as before.
        self.assertIsNone(_options(Config(), "/p", None).system_prompt)

    def test_a_preset_reaches_the_options_as_given(self):
        # No `append`: the spec keeps the preset bare.
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        got = _options(Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET).system_prompt
        self.assertEqual(got, {"type": "preset", "preset": "claude_code"})
        self.assertNotIn("append", got)
        # A copy, so nothing downstream can edit the module's constant through it.
        self.assertIsNot(got, CLAUDE_CODE_PRESET)

    def test_a_preset_changes_nothing_else(self):
        # The grant is `tools` + the gate + `auto`, and project settings stay out. The preset may
        # move none of them.
        from coscc.agent import policy
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        gate = Gate(_grant(tools=policy.READ_TOOLS))
        common = dict(max_turns=40, tools=list(policy.READ_TOOLS), gate=gate)
        bare = _options(Config(), "/p", None, **common)
        preset = _options(Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET, **common)
        self.assertEqual(preset.tools, bare.tools)
        self.assertEqual(preset.can_use_tool, gate.can_use_tool)
        self.assertEqual(bare.can_use_tool, gate.can_use_tool)
        self.assertEqual(preset.setting_sources, [])
        self.assertTrue(preset.verbatim_prompts)
        self.assertEqual(preset.permission_mode, bare.permission_mode)
        self.assertEqual(preset.max_turns, bare.max_turns)

    def test_attribution_rides_beside_auto_only_with_a_preset(self):
        from coscc.agent import agents, policy
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        given = agents.settings_json(agents.agent_for("impl"))
        common = dict(max_turns=40, tools=list(policy.READ_TOOLS))
        plain = _options(Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET, **common)
        signed = _options(
            Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET, settings=given, **common
        )
        bare = _options(Config(), "/p", None, settings=given, **common)
        self.assertEqual(
            json.loads(signed.settings or ""),
            {"autoMode": sessions.AUTO_MODE, **json.loads(given)},
        )
        for options in (plain, bare):
            self.assertEqual(json.loads(options.settings or ""), {"autoMode": sessions.AUTO_MODE})
        self.assertEqual(signed.setting_sources, [])
        self.assertTrue(signed.strict_mcp_config)
        self.assertEqual(
            (signed.tools, signed.permission_mode, signed.extra_args),
            (plain.tools, plain.permission_mode, plain.extra_args),
        )

    # No settings source, no MCP server, and the project's own instructions put into the system
    # prompt by the app, since the CLI no longer loads them.

    def test_the_project_instructions_go_into_the_preset_or_become_the_prompt(self):
        # The fixture is the reader's own, with a canary in every kind of file.
        from coscc.agent import instructions
        from tests.agent.test_instructions import plant
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            plant(Path(d))
            block = instructions.read(d).text
            written = Path(data) / sessions.PROMPT_FILE
            preset = _options(Config(), d, None, system_prompt=CLAUDE_CODE_PRESET, data_dir=data)
            self.assertEqual(written.read_text(encoding="utf-8"), block)
            written.unlink()
            bare = _options(Config(), d, None, data_dir=data)
            self.assertEqual(written.read_text(encoding="utf-8"), block)
        self.assertIn("CANARY-DOTCLAUDE", block)
        # The preset itself stays bare; the block rides on the CLI's file flag.
        self.assertEqual(preset.system_prompt, {"type": "preset", "preset": "claude_code"})
        self.assertEqual(preset.extra_args, {"append-system-prompt-file": str(written)})
        self.assertEqual(bare.system_prompt, {"type": "file", "path": str(written)})
        self.assertEqual(bare.extra_args, {})
        # The module's constant is still bare.
        self.assertNotIn("append", CLAUDE_CODE_PRESET)

    def test_a_directory_with_no_instructions_leaves_the_prompt_as_it_was(self):
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        with tempfile.TemporaryDirectory() as data:
            self.assertIsNone(_options(Config(), "/p", None, data_dir=data).system_prompt)
            got = _options(Config(), "/p", None, system_prompt=CLAUDE_CODE_PRESET, data_dir=data)
            self.assertNotIn("append", got.system_prompt)
            self.assertEqual(got.extra_args, {})
            # Nothing is written when there is nothing to carry.
            self.assertEqual(list(Path(data).iterdir()), [])

    def test_instructions_past_the_argument_limit_never_reach_argv(self):
        # Linux refuses one argument over `MAX_ARG_STRLEN` (32 pages, 128 KiB at 4 KiB pages) with
        # `E2BIG`; a block four times that must leave argv no longer than it is without one.
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            (Path(d) / "CLAUDE.md").write_text(
                "x" * (512 * 1024) + "\nCANARY-BIG\n", encoding="utf-8"
            )
            for prompt in (None, CLAUDE_CODE_PRESET):
                with self.subTest(preset=prompt is not None):
                    options = _options(Config(), d, None, system_prompt=prompt, data_dir=data)
                    transport = SubprocessCLITransport(prompt="", options=options)
                    transport._cli_path = "claude"
                    argv = transport._build_command()
                    self.assertLess(max(len(a) for a in argv), 4096)
                    flag = "--append-system-prompt-file" if prompt else "--system-prompt-file"
                    carried = Path(argv[argv.index(flag) + 1]).read_text(encoding="utf-8")
                    self.assertTrue(carried.endswith("CANARY-BIG\n"))
                    self.assertNotIn("--append-system-prompt", argv)
                    self.assertNotIn("--system-prompt", argv)

    def test_every_shape_of_session_reaches_the_cli_with_no_source_and_no_mcp(self):
        from coscc.agent import policy
        from tests.agent.test_instructions import plant
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            plant(Path(d))
            shapes = {
                "no tool": _options(Config(), "/p", None, tools=[]),
                "read tools": _options(Config(), "/p", None, tools=list(policy.READ_TOOLS)),
                "preset with the block": _options(
                    Config(),
                    d,
                    None,
                    tools=["Read"],
                    system_prompt=CLAUDE_CODE_PRESET,
                    data_dir=data,
                ),
                "the block alone": _options(Config(), d, None, tools=[], data_dir=data),
            }
            argvs = {}
            for name, options in shapes.items():
                transport = SubprocessCLITransport(prompt="", options=options)
                transport._cli_path = "claude"
                argv = argvs[name] = transport._build_command()
                self.assertIn("--setting-sources=", argv, name)
                self.assertFalse(
                    [
                        a
                        for a in argv
                        if a.startswith("--setting-sources=") and a != "--setting-sources="
                    ],
                    name,
                )
                self.assertIn("--strict-mcp-config", argv, name)
            for name, flag in (
                ("preset with the block", "--append-system-prompt-file"),
                ("the block alone", "--system-prompt-file"),
            ):
                argv = argvs[name]
                carried = Path(argv[argv.index(flag) + 1]).read_text(encoding="utf-8")
                self.assertIn("CANARY-ROOT", carried, name)

    # Effort is chosen per stage and label; `_options` only carries it.

    # The values and where each was measured are in `sessions.FOREGROUND_ENV`.
    FOREGROUND = {
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        "BASH_DEFAULT_TIMEOUT_MS": "600000",
        "BASH_MAX_TIMEOUT_MS": "600000",
    }

    def test_a_session_holding_bash_runs_commands_in_the_foreground(self):
        env = _options(Config(), "/p", None, tools=["Read", "Bash"]).env
        self.assertEqual({k: env.get(k) for k in self.FOREGROUND}, self.FOREGROUND)

    # One screenshot is one stdout line; every session gets the same ceiling on it.

    def test_the_installed_sdk_has_a_max_buffer_size_field(self):
        # Known only from the SDK's own source, so the installed one is asked.
        import dataclasses

        fields = {f.name for f in dataclasses.fields(sdk.ClaudeAgentOptions)}
        self.assertIn("max_buffer_size", fields)

    def test_every_combination_carries_the_buffer(self):
        # Whatever the caller asks for, the ceiling is the same.
        import itertools

        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        self.assertEqual(sessions.MAX_BUFFER, 33_554_432)
        for tools, helpers, prompt, effort, budget in itertools.product(
            (None, [], ["Read"]),
            (None, Helpers()),
            (None, CLAUDE_CODE_PRESET),
            (None, "high"),
            (None, 1.0),
        ):
            with self.subTest(
                tools=tools,
                helpers=helpers is not None,
                preset=prompt is not None,
                effort=effort,
                budget=budget,
            ):
                options = _options(
                    Config(),
                    "/p",
                    None,
                    tools=tools,
                    gate=Gate(_grant(), helpers=helpers),
                    system_prompt=prompt,
                    effort=effort,
                    max_budget_usd=budget,
                )
                self.assertEqual(options.max_buffer_size, sessions.MAX_BUFFER)

    def test_options_are_built_only_in_one_place(self):
        # Chat, a board step, Gebo and an estimate all reach the SDK through `_options`; a second
        # `ClaudeAgentOptions(` anywhere else in the package would be a session without the ceiling.
        # `_web/` is built, not committed.
        import ast

        package = Path(sessions.__file__).parents[1]
        found = []
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(package)
            if path.name.endswith("_test.py") or relative.parts[0] == "_web":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "ClaudeAgentOptions":
                    found.append((relative, node.lineno, tree))
        self.assertEqual(len(found), 1, [(str(r), n) for r, n, _ in found])
        relative, line, tree = found[0]
        self.assertEqual(relative, Path("agent/sessions.py"))
        builder = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_options"
        )
        self.assertTrue(builder.lineno <= line <= builder.end_lineno)


class EverySessionRunsAuto(unittest.TestCase):
    """`auto`, its settings inline, the server-side review off, and the gate in front, for a
    chat, a session holding no tool and a step alike."""

    def test_every_shape_runs_auto_behind_the_gate(self):
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        for name, kw in {
            "chat": {},
            "no tool": {"tools": []},
            "a step": {"tools": ["Read", "Bash"], "system_prompt": CLAUDE_CODE_PRESET},
        }.items():
            with self.subTest(name):
                gate = Gate(_grant(tools=tuple(kw.get("tools") or ())))
                options = _options(Config(), "/p", None, gate=gate, **kw)
                self.assertEqual(options.permission_mode, "auto")
                self.assertEqual(json.loads(options.settings or "")["autoMode"], sessions.AUTO_MODE)
                self.assertEqual(options.env["CLAUDE_CODE_AUTO_MODE_SERVER"], "0")
                self.assertEqual(options.can_use_tool, gate.can_use_tool)
                self.assertEqual(
                    options.hooks["PreToolUse"][0].hooks,  # ty: ignore[not-subscriptable] - set above
                    [gate.pre_tool_use],
                )
                self.assertEqual(options.allowed_tools, [])

    def test_auto_keeps_its_defaults_and_is_told_where_it_runs(self):
        self.assertEqual(sessions.AUTO_MODE["soft_deny"], ["$defaults"])
        self.assertEqual(sessions.AUTO_MODE["hard_deny"], ["$defaults"])
        self.assertEqual(sessions.AUTO_MODE["environment"][0], "$defaults")
        self.assertIn("worktree", sessions.AUTO_MODE["environment"][1])

    def test_the_apps_own_mcp_tools_are_allowed_by_name_and_no_command_is(self):
        grant = _grant(
            helpers=("scout", "worker"),
            mcp=("mcp__cos__submit", "mcp__cos__peers", "mcp__vault__vault_exec"),
        )
        options = _options(Config(), "/p", None, gate=Gate(grant))
        self.assertEqual(
            options.allowed_tools,
            ["mcp__cos__submit", "mcp__cos__peers", "mcp__vault__vault_exec"],
        )

    def test_a_session_with_no_helpers_hooks_no_helper(self):
        options = _options(Config(), "/p", None)
        self.assertEqual(set(options.hooks or {}), {"PreToolUse"})


class AClassifierDenialIsRecorded(unittest.IsolatedAsyncioTestCase):
    """A refusal `auto` made itself reaches the stream as a system message, and the gate records
    it; a session started with no gate gets a locked one, with the secrets."""

    async def _stream(self, messages, **kw):
        s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(s.close_all)
        _Recording.messages = messages
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _Recording):
            return [item async for item in s.stream("/tmp", "hi", **kw)]

    async def test_the_classifiers_refusal_is_in_the_runs_denials(self):
        from claude_agent_sdk._internal.message_parser import parse_message

        denied = parse_message(
            {
                "type": "system",
                "subtype": "permission_denied",
                "uuid": "u",
                "session_id": "sid",
                "tool_name": "Bash",
                "tool_use_id": "toolu_1",
                "decision_reason_type": "classifier",
                "decision_reason": "[Data Exfiltration]",
            }
        )
        denials = Denials()
        config = Config(workspaces=("/tmp",))
        grant = Grant(
            cwd="/tmp", write=("/tmp",), secrets=sessions.secrets_of(config), home=config.home
        )
        gate = Gate(grant, denials)
        await self._stream([denied, _result("sid")], gate=gate, step=sessions.StepHandle())
        self.assertEqual(denials.count, 1)
        self.assertIn("[Data Exfiltration]", denials.reasons[0])

    async def test_a_session_given_no_gate_gets_a_locked_one_with_the_secrets(self):
        await self._stream([_result("sid")])
        options = _Recording.options
        (matcher,) = options.hooks["PreToolUse"]
        gate = matcher.hooks[0].__self__
        self.assertEqual((gate.grant.cwd, gate.grant.write), ("/tmp", ()))
        self.assertEqual(gate.grant.tools, ())
        self.assertIn(str(Data(Config().data_dir).db_path), gate.grant.secrets)
        self.assertEqual(options.permission_mode, "auto")


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
        with mock.patch(
            "coscc.agent.sessions.ClaudeSDKClient", side_effect=RuntimeError("connect")
        ):
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


class _Recording(_FakeClient):
    """Keeps the options it was built with."""

    options = None

    def __init__(self, options=None):
        _Recording.options = options


def _assistant(text, session_id):
    return sdk.AssistantMessage(
        content=[sdk.TextBlock(text=text)], model="m", session_id=session_id
    )


def _result(session_id, turns=3, cost=0.5):
    return sdk.ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=10,
        is_error=False,
        num_turns=turns,
        session_id=session_id,
        total_cost_usd=cost,
    )


class TheSessionIdIsToldBeforeTheStepIsOver(unittest.IsolatedAsyncioTestCase):
    """`("session", id)` once, as soon as it is known, and the accounting of the `ResultMessage` is
    untouched by it."""

    async def _run(self, messages, session_id=None, adopt=None):
        s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(s.close_all)  # a chat keeps its data root until closed
        if adopt:
            s.adopt(adopt)
        _FakeClient.messages = messages
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _FakeClient):
            return [item async for item in s.stream("/tmp", "hi", session_id=session_id)]

    async def test_a_new_session_says_its_id_once_before_done(self):
        items = await self._run(
            [_assistant("a", "sid-1"), _assistant("b", "sid-1"), _result("sid-1")]
        )
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
    """A chat turn is held while it answers and let go when it ends, which the bus hears as `chat-turn.ended`;
    a board step is never one."""

    def setUp(self):
        self.s = Sessions(Config(workspaces=("/tmp",)))
        self.addAsyncCleanup(self.s.close_all)  # a chat keeps its data root until closed
        self.ended = 0

        def ended():
            self.ended += 1

        self.s.bus.subscribe("chat-turn.ended", lambda _: ended())
        _CountingClient.made = []
        _CountingClient.hold = asyncio.Event()
        _CountingClient.fail = False
        _CountingClient.messages = [_assistant("a", "sid-t"), _result("sid-t")]
        patcher = mock.patch("coscc.agent.sessions.ClaudeSDKClient", _CountingClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _reader(self, **kw):
        return [item async for item in self.s.stream("/tmp", "hi", **kw)]

    def turns(self):
        return list(self.s._turns.values())

    async def _until_told(self):
        for _ in range(100):
            turns = self.turns()
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
        self.assertEqual((self.turns(), self.ended), ([], 1))


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
    """The app's own timeout used to cancel the SDK's close before its SIGTERM/SIGKILL, so a CLI
    that ignored stdin EOF was never signalled."""

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
        while not client.disconnects:
            await asyncio.sleep(0)
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
            ready = Path(tmp) / "ready"
            cli.write_text(
                f"#!{sys.executable}\n"
                "import signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"open({str(ready)!r}, 'w').close()\n"
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

            # The script has installed its handler.
            for _ in range(500):
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            await sessions.StepHandle(client=Client()).close()
            await asyncio.wait_for(process.wait(), 2)
            self.assertEqual(process.returncode, -9)


class AScreenshotLineReachesTheStep(unittest.IsolatedAsyncioTestCase):
    """The SDK's real transport, given what `_options` builds, and a CLI that prints one JSON
    line of a chosen length and exits -- it answers no initialize, and the transport reads the
    line anyway. Run `c58b7e48` died on a line past the SDK's default of 1 048 576."""

    async def _read(self, length):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "claude"
            cli.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                'head = \'{"type":"user","pad":"\'\n'
                "tail = '\"}'\n"
                f"sys.stdout.write(head + 'A' * ({length} - len(head) - len(tail)) + tail + '\\n')\n"
                "sys.stdout.flush()\n"
            )
            cli.chmod(0o755)
            options = _options(Config(), tmp, None, data_dir=tmp)
            options.cli_path = str(cli)
            transport = SubprocessCLITransport(prompt="", options=options)
            try:
                with mock.patch.dict(os.environ, {"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1"}):
                    await transport.connect()
                return [message async for message in transport.read_messages()]
            finally:
                await transport.close()

    async def test_a_line_eight_times_the_old_ceiling_is_one_message(self):
        messages = await self._read(8_388_608)
        self.assertEqual([m["type"] for m in messages], ["user"])

    async def test_a_line_past_the_ceiling_still_raises(self):
        # The ceiling reached the transport, and there still is one. The default would raise here
        # too, so the error must name this ceiling.
        with self.assertRaises(sdk.CLIJSONDecodeError) as raised:
            await self._read(sessions.MAX_BUFFER + 1)
        self.assertIn(f"maximum buffer size of {sessions.MAX_BUFFER} ", str(raised.exception))


class AStepsClientIsClosedWhenTheStepEnds(unittest.IsolatedAsyncioTestCase):
    """A board step's client is closed however the step ends, exactly once, and never kept in
    `_live` for resuming. Chat's clients still are."""

    def setUp(self):
        _CountingClient.made = []
        _CountingClient.hold = None
        _CountingClient.fail = False
        _CountingClient.messages = [_assistant("a", "sid-s"), _result("sid-s")]
        _StartingClient.made = []
        _StartingClient.spawned = asyncio.Event()
        _StartingClient.go = asyncio.Event()
        patcher = mock.patch("coscc.agent.sessions.ClaudeSDKClient", _CountingClient)
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
        """A `disconnect` during `connect` is empty in the SDK, so a Stop there must not count as
        the close."""
        h = sessions.StepHandle()
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _StartingClient):
            task = asyncio.create_task(self._drain(h))
            await asyncio.wait_for(_StartingClient.spawned.wait(), 5)
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
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _StartingClient):
            task = asyncio.create_task(self._drain(h))
            await asyncio.wait_for(_StartingClient.spawned.wait(), 5)
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
        """`task.cancel()` landing on `stream`'s own close left the handle in `_steps`, and
        `live_in` saying so until a restart."""
        h = sessions.StepHandle()
        client = _SlowClient()
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", lambda options=None: client):
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
    """The question `pull` has to ask before it touches a workspace."""

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

    def test_the_session_in_that_directory_and_only_that_one(self):
        self._live("in-a", self.a)
        self._live("in-b", self.b)
        self.assertEqual(self.s.live_in(str(self.a)), ["in-a"])
        self.assertEqual(self.s.live_in(str(self.b)), ["in-b"])

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
    """**These assertions are about the value the child would read, not about this dict.**
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

    def test_no_setting_of_this_app_reaches_the_child_with_a_value(self):
        """Every `COS_*` is blank but one: `COS_DATA_DIR` is the session's own."""
        with mock.patch.dict(os.environ, {"COS_DATA_DIR": "/d", "COS_PORT": "1"}):
            child = self.child()
            self.assertEqual(
                [k for k, v in child.items() if k.startswith("COS_") and v], ["COS_DATA_DIR"]
            )
            self.assertEqual(child["COS_DATA_DIR"], tempfile.gettempdir())

    def test_the_settings_the_child_reads_still_load(self):
        """The child reads `COS_PORT=""`, and `config.from_env` must take that as unset. A
        worktree's own `npm test` loads the config, and `int("")` errored seven of its tests
        whenever the app had been given a port."""
        from coscc import config

        with mock.patch.dict(os.environ, {"COS_HOST": "127.0.0.1", "COS_PORT": "9999"}):
            c = config.from_env(self.child())
            self.assertEqual((c.host, c.port), ("0.0.0.0", 8790))

    def test_the_unit_scratch_directories_reach_the_child_and_tmpdir_is_the_disk_one(self):
        """Set after every `COS_*` is blanked, so an inherited value of the same name is replaced."""
        with mock.patch.dict(
            os.environ, {"COS_SCRATCH_RAM": "/old", "COS_SCRATCH_DISK": "/old", "TMPDIR": "/old"}
        ):
            inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            inherited.update(_child_env("/w", scratch=("/r/ram", "/r/disk")))
        self.assertEqual(inherited["COS_SCRATCH_RAM"], "/r/ram")
        self.assertEqual(inherited["COS_SCRATCH_DISK"], "/r/disk")
        self.assertEqual(inherited["TMPDIR"], "/r/disk")

    def test_a_session_without_a_unit_names_no_scratch_and_keeps_its_tmpdir(self):
        with mock.patch.dict(os.environ, {"COS_SCRATCH_RAM": "/old", "TMPDIR": "/t"}):
            env = _child_env("/w")
            self.assertEqual(env["COS_SCRATCH_RAM"], "")
            self.assertNotIn("TMPDIR", env)

    def test_everything_else_is_left_alone(self):
        """Overridden, not replaced. A session that loses `HOME` cannot sign in."""
        with mock.patch.dict(os.environ, {"HOME": "/home/someone", "PATH": "/bin"}):
            child = self.child()
            self.assertEqual(child["HOME"], "/home/someone")
            self.assertEqual(child["PATH"], "/bin")

    # -- a unit's session reads its own tree, not the workspace's --------

    def unit_child(self, cwd, workspace):
        inherited = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        inherited.update(_child_env(cwd, workspace))
        return inherited

    def test_the_virtualenv_the_child_reads_is_the_worktrees(self):
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/ws/.venv"}):
            self.assertEqual(
                self.unit_child("/wt", "/ws")["VIRTUAL_ENV"], str(Path("/wt") / ".venv")
            )

    def test_no_path_entry_under_the_workspace_or_the_package_reaches_the_child(self):
        import coscc

        pkg = str(Path(coscc.__file__).resolve().parent / "bin")
        with mock.patch.dict(
            os.environ, {"PATH": os.pathsep.join(["/ws/.venv/bin", pkg, "/usr/bin"])}
        ):
            self.assertEqual(self.unit_child("/wt", "/ws")["PATH"], "/usr/bin")


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
        patcher = mock.patch("coscc.agent.sessions.ClaudeSDKClient", _EnvClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _step(self, h=None):
        return [i async for i in self.s.stream("/tmp", "hi", step=h or sessions.StepHandle())]

    def _root(self, env):
        return Path(env["COS_DATA_DIR"])

    async def test_an_absolute_existing_directory_apart_from_the_apps(self):
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

    async def test_a_steps_unit_scratch_is_in_the_environment_its_client_is_built_with(self):
        paths = (str(self.tmp / "ram"), str(self.tmp / "disk"))
        h = sessions.StepHandle()
        [_ async for _ in self.s.stream("/tmp", "hi", step=h, unit_scratch=paths)]
        [env] = _EnvClient.envs
        self.assertEqual(env["COS_SCRATCH_RAM"], paths[0])
        self.assertEqual(env["COS_SCRATCH_DISK"], paths[1])
        self.assertEqual(env["TMPDIR"], paths[1])

    async def test_gone_after_a_step_that_finishes(self):
        await self._step()
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_gone_after_a_step_stopped_by_its_ceiling(self):
        capped = sdk.ResultMessage(
            subtype="error_max_budget_usd",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=9,
            session_id="sid-d",
            total_cost_usd=8.0,
        )
        _CountingClient.messages = [_assistant("a", "sid-d"), capped]
        items = await self._step()
        self.assertEqual(dict(items)["done"]["terminal_reason"], "error_max_budget_usd")
        self.assertFalse(self._root(_EnvClient.envs[0]).exists())

    async def test_a_stop_that_cancels_the_close_waits_for_the_closing(self):
        """The directory went while the CLI the cancelled close was still ending could open a `Data`
        and make it again."""
        h = sessions.StepHandle()
        client = _SlowClient()
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", lambda options=None: client):
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

    async def test_a_stop_during_a_failed_connect_waits_for_the_abandoning(self):
        """The same, when the cancel lands on the closing of a client whose `connect` did not finish
        -- the handle had no client to wait on."""
        h = sessions.StepHandle()
        client = _SlowClient()
        connecting = asyncio.Event()

        async def connect():
            connecting.set()
            await asyncio.Event().wait()

        client.connect = connect
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", lambda options=None: client):
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

    async def test_gone_when_the_client_cannot_be_built(self):
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

    async def test_the_apps_database_is_protected_and_an_outer_one_kept(self):
        with mock.patch.dict(os.environ, {PROTECTED_DB_VAR: "/outer/cos.db"}):
            await self._step()
        listed = _EnvClient.envs[0][PROTECTED_DB_VAR].split(os.pathsep)
        self.assertEqual(listed, ["/outer/cos.db", str(self.app.resolve() / "cos.db")])


class OnlyAScratchDirectoryIsEverRemoved(unittest.TestCase):
    """`_drop` is an `rmtree`, so it must refuse anything else."""

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


# -- knowing what runs, and pausing it --------------------------------------------


def _init(session_id):
    return sdk.SystemMessage(subtype="init", data={"session_id": session_id})


class ASessionIsKnownWhileItRuns(unittest.IsolatedAsyncioTestCase):
    def test_every_resume_builds_options_with_snapshot(self):
        from tests.agent.test_instructions import plant
        from coscc.runner.attempt import CLAUDE_CODE_PRESET

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as data:
            plant(Path(d))
            written = str(Path(data) / sessions.PROMPT_FILE)
            preset = _options(
                Config(), d, "sid", system_prompt=CLAUDE_CODE_PRESET, data_dir=data, resume_at="u"
            )
            bare = _options(Config(), d, "sid", data_dir=data, resume_at="u")
        empty = _options(Config(), "/tmp", "sid", resume_at="u")
        self.assertEqual(
            preset.system_prompt, {"type": "preset", "preset": "claude_code", "snapshot": True}
        )
        self.assertEqual(preset.extra_args, {"append-system-prompt-file": written})
        # The file is still appended, never passed as a value in argv.
        self.assertEqual(bare.system_prompt, {"type": "custom", "prompt": "", "snapshot": True})
        self.assertEqual(bare.extra_args, {"append-system-prompt-file": written})
        self.assertEqual(empty.system_prompt, {"type": "custom", "prompt": "", "snapshot": True})
        self.assertNotIn("snapshot", CLAUDE_CODE_PRESET)

    async def test_a_stream_learns_its_session_id_from_init(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        _FakeClient.messages = [_init("sid-i"), _result("sid-i")]
        handle = sessions.StepHandle()
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _FakeClient):
            items = [i async for i in s.stream("/tmp", "hi", step=handle, owner={"kind": "step"})]
        self.assertEqual(items[0], ("session", "sid-i"))
        self.assertEqual((handle.session_id, handle.owner), ("sid-i", {"kind": "step"}))

    async def test_a_different_session_id_in_init_is_refused(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        s.adopt("sid-a")
        _FakeClient.messages = [_init("sid-b"), _result("sid-b")]
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _FakeClient):
            with self.assertRaises(Refused):
                async for _ in s.stream("/tmp", "hi", "sid-a", step=sessions.StepHandle()):
                    pass

    async def test_a_suspended_stream_raises_suspended_and_yields_no_done(self):
        s = Sessions(Config(workspaces=("/tmp",)))
        _CountingClient.made, _CountingClient.fail = [], False
        _CountingClient.hold = asyncio.Event()
        _CountingClient.messages = [_init("sid-p"), _result("sid-p")]
        handle = sessions.StepHandle()
        items = []

        async def read():
            async for item in s.stream("/tmp", "hi", step=handle):
                items.append(item)

        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _CountingClient):
            task = asyncio.create_task(read())
            for _ in range(100):
                if handle.session_id:
                    break
                await asyncio.sleep(0.01)
            handle.suspended = True
            _CountingClient.hold.set()
            with self.assertRaises(sessions.Suspended):
                await task
        self.assertNotIn("done", [k for k, _ in items])


class _PausingClient:
    """A live client for `suspend_all`: records the order of `interrupt` and `disconnect`,
    and writes the lines the CLI writes on an interrupt into its transcript."""

    def __init__(self, path, order, hang=0.0, process=None, gate=None):
        self.path, self.order, self.hang, self.gate = path, order, hang, gate
        self._transport = mock.Mock(_process=process) if process is not None else None
        self._query = object()

    async def interrupt(self):
        self.order.append(("interrupt", sessions.transcript.boundary(self.path)))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(
                '{"type": "user", "uuid": "fake"}\n{"type": "cost-state", "totalCostUSD": 0.25}\n'
            )

    async def disconnect(self):
        self.order.append("disconnect")
        if self.gate:
            await self.gate()
        if self.hang:
            await asyncio.sleep(self.hang)


class SuspendingEverySession(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(sessions.transcript, "projects_root", lambda: self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.s = Sessions(Config(workspaces=("/tmp",)))
        self.order = []

    def _flow(self, sid, **kw):
        path = sessions.transcript.path_for("/tmp/w_1", sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"type": "user", "uuid": "p", "message": {"content": "go"}}\n'
            '{"type": "assistant", "uuid": "a", "message": {"id": "m1", "content": '
            '[{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}}]}}\n',
            encoding="utf-8",
        )
        client = _PausingClient(path, self.order, **kw)
        handle = sessions.StepHandle(
            cwd="/tmp/w_1",
            client=client,
            session_id=sid,
            owner={"kind": "step", "unit": "0001_a", "start_at": "t0"},
        )
        self.s._steps.add(handle)
        return handle

    async def test_no_stream_opens_once_every_session_is_paused(self):
        # A step between two sessions while the settle waits for it ends `Refused`, with no client
        # made and nothing registered for the hand-off to cut.
        await self.s.suspend_all()
        _CountingClient.made, _CountingClient.fail = [], False
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _CountingClient):
            for step in (sessions.StepHandle(), None):
                with self.assertRaises(sessions.Refused) as caught:
                    await self.s.stream("/tmp", "hi", step=step).__anext__()
                self.assertEqual(str(caught.exception), sessions.PAUSED)
        self.assertEqual((_CountingClient.made, self.s._steps, self.s._turns), ([], set(), {}))

    async def test_no_stream_opens_on_a_grant_missing_this_apps_secrets_or_home(self):
        # A grant issued without the app's config denies a thinner list: refused before a client.
        _CountingClient.made, _CountingClient.fail = [], False
        full = self.s.secrets()
        home = self.s.config.home
        thin = (
            Grant(cwd="/p", secrets=full[:1], home=home),
            Grant(cwd="/p", secrets=full, home="/x"),
        )
        with mock.patch("coscc.agent.sessions.ClaudeSDKClient", _CountingClient):
            for grant in thin:
                with self.assertRaises(sessions.Refused) as caught:
                    await self.s.stream("/tmp", "hi", gate=Gate(grant)).__anext__()
                self.assertEqual(str(caught.exception), sessions.THIN_GRANT)
        self.assertEqual((_CountingClient.made, self.s._steps, self.s._turns), ([], set(), {}))

    async def test_suspend_closes_every_session_in_parallel(self):
        # A close is open from the client's `disconnect` to the SIGKILL that ends `_shut`. The first
        # close stays open until the second has started (or the bound passes), so a sequential
        # `suspend_all` records start, end, start whatever the speed of the machine.
        events, started, both = [], [], asyncio.Event()

        class Process(_Process):
            def __init__(self, sid):
                super().__init__(obeys=False)
                self.sid = sid

            def kill(self):
                events.append(("end", self.sid))
                super().kill()

        def gate(sid):
            async def wait():
                events.append(("start", sid))
                started.append(sid)
                if len(started) == 2:
                    both.set()
                with suppress(TimeoutError):
                    await asyncio.wait_for(both.wait(), 1)

            return wait

        with (
            mock.patch.object(sessions, "DISCONNECT_TIMEOUT", 2.0),
            mock.patch.object(sessions, "KILL_AFTER", 0.05),
        ):
            for sid in ("sid-3", "sid-4"):
                self._flow(sid, process=Process(sid), gate=gate(sid))
            records = await self.s.suspend_all()
        self.assertEqual(len(records), 2)
        self.assertEqual({k for k, _ in events[:2]}, {"start"})
        self.assertEqual({k for k, _ in events[2:]}, {"end"})
        self.assertEqual(len(events), 4)

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "no /proc")
    async def test_suspend_kills_descendants_left_alive_after_a_sigkill(self):
        cli = await asyncio.create_subprocess_exec("sh", "-c", "trap '' TERM; sleep 60 & wait")
        self.addCleanup(lambda: cli.returncode is None and cli.kill())
        for _ in range(100):
            left = sessions._descendants(cli.pid)
            if left:
                break
            await asyncio.sleep(0.02)
        self.assertTrue(left)
        with (
            mock.patch.object(sessions, "DISCONNECT_TIMEOUT", 0.1),
            mock.patch.object(sessions, "KILL_AFTER", 0.1),
        ):
            self._flow("sid-5", process=cli)
            await self.s.suspend_all()
        # The child is reaped by the loop, which under load lags behind `suspend_all`.
        await asyncio.wait_for(cli.wait(), 5)
        self.assertEqual(cli.returncode, -9)  # `_shut`'s SIGKILL: the tree under it is orphaned

        def running(pid):
            # A process being reaped vanishes between any two reads: ESRCH, not ENOENT.
            try:
                return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except OSError:
                return False

        for _ in range(100):
            alive = [p for p, _ in left if running(p)]
            if not alive:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(alive, [])

    async def test_a_stream_with_no_session_id_is_closed_and_marked_unresumable(self):
        handle = sessions.StepHandle(cwd="/tmp", owner={"kind": "step"})
        self.s._steps.add(handle)
        [record] = await self.s.suspend_all()
        self.assertEqual(record["unresumable"], "no session id yet")
        self.assertTrue(handle.closed and handle.suspended)
