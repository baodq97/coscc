"""Tests for the read layer and the guards on the session layer.

Nothing here creates a session. Creating one spends account quota (`spec.md` C4), so the
suite stays free to run in a loop; what needs a real session is the proof command, which
is run deliberately.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk

from coscc import frontend, sessions
from coscc.config import Config
from coscc.sessions import (
    Live,
    Refused,
    Sessions,
    _options,
    _text_of,
    history,
    list_for_directory,
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
        inherited.update(sessions.child_env(cwd))
        return inherited

    def test_the_web_workdir_the_child_reads_is_the_workspace_not_this_app(self):
        with mock.patch.dict(os.environ, {frontend.WEB_WORKDIR_VAR: "/installed/_web"}):
            self.assertEqual(
                self.child("/w")[frontend.WEB_WORKDIR_VAR], str(Path("/w") / ".web")
            )

    def test_no_setting_of_this_app_reaches_the_child_with_a_value(self):
        with mock.patch.dict(os.environ, {"COS_DATA_DIR": "/d", "COS_PORT": "1"}):
            child = self.child()
            self.assertEqual(
                [k for k, v in child.items() if k.startswith("COS_") and v], []
            )

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
        inherited.update(sessions.child_env(cwd, workspace))
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
