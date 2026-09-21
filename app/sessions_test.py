"""Tests for the read layer and the guards on the session layer.

Nothing here creates a session. Creating one spends account quota (`spec.md` C4), so the
suite stays free to run in a loop; what needs a real session is the proof command, which
is run deliberately.
"""

import asyncio
import unittest
from unittest import mock

import claude_agent_sdk as sdk

from app.config import Config
from app.sessions import Refused, Sessions, _options, _text_of, history, list_for_directory


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

    def test_write_tools_are_stripped_on_the_way_to_the_sdk(self):
        c = Config(tools=("Read", "Bash"))
        self.assertEqual(_options(c, "/p", None).tools, ["Read"])


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
        with mock.patch("app.sessions.ClaudeSDKClient", side_effect=RuntimeError("connect")):
            with self.assertRaises(RuntimeError):
                await s.send("/tmp", "hi", session_id="not-ours")

    async def test_closing_nothing_is_not_an_error(self):
        await Sessions(Config()).close_all()


if __name__ == "__main__":
    unittest.main()
