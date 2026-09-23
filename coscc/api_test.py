"""Tests for the HTTP surface.

None of these create a session — the guards are exactly the paths that must refuse
*before* anything is spawned, so testing them costs nothing (`spec.md` C4). What needs a
real session is `scripts/verify_0001.py`, which is run on purpose.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_agent_sdk as sdk
import httpx

from coscc.api import build
from coscc.config import Config


def _info(session_id="s1", cwd="/tmp"):
    return sdk.SDKSessionInfo(
        session_id=session_id, summary="s", last_modified=1, file_size=1,
        custom_title=None, first_prompt=None, git_branch=None, cwd=cwd,
        tag=None, created_at=1,
    )


class Surface(unittest.IsolatedAsyncioTestCase):
    """Driven over ASGI, the same way `scripts/verify_0001.py` drives it.

    No socket and no lifespan: the transport speaks to the app object directly, which is
    also the object Reflex mounts. A test that needed a port would be testing something
    the proof does not exercise.
    """

    async def asyncSetUp(self):
        self.app = build(Config(workspaces=("/tmp",)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_workspaces_lists_what_was_configured(self):
        # R13 widened this from a list of paths to a list of entries carrying
        # name, label, source and missing, plus the count the app could not answer
        # before. `paths` is kept so the older shape still reads.
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["paths"], ["/tmp"])
        self.assertEqual(body["count"], 1)
        entry = body["workspaces"][0]
        self.assertEqual(entry["path"], "/tmp")
        self.assertEqual(entry["source"], "env")
        self.assertFalse(entry["missing"])

    async def test_an_env_workspace_is_marked_as_such(self):
        # It matters that a caller can tell: an env entry cannot be renamed or removed
        # from the app, and showing a delete button for one would be a lie.
        body = (await self.client.get("/api/workspaces")).json()
        self.assertTrue(all(e["source"] == "env" for e in body["workspaces"]))

    async def test_no_route_leaks_configuration(self):
        # spec.md C3: a long-lived credential is in this process. The knobs and the
        # environment must not be readable over the port.
        body = (await self.client.get("/api/workspaces")).text
        for leak in ("TOKEN", "bypass", "permission_mode", "tools"):
            self.assertNotIn(leak, body)

    async def test_sessions_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get("/api/sessions", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("workspace", r.json()["error"])

    async def test_history_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.get(
            "/api/history", params={"cwd": "/etc", "session_id": "s1"}
        )
        self.assertEqual(r.status_code, 400)

    async def test_history_requires_a_session_id(self):
        r = await self.client.get("/api/history", params={"cwd": "/tmp"})
        self.assertEqual(r.status_code, 400)

    async def test_send_refuses_a_directory_outside_the_workspaces(self):
        r = await self.client.post("/api/send", json={"cwd": "/etc", "text": "hi"})
        self.assertEqual(r.status_code, 400)

    async def test_send_requires_text(self):
        r = await self.client.post("/api/send", json={"cwd": "/tmp", "text": "  "})
        self.assertEqual(r.status_code, 400)

    async def test_send_rejects_a_non_json_body(self):
        r = await self.client.post(
            "/api/send", content="notjson", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(r.status_code, 400)

    async def test_a_foreign_session_is_marked_read_only_not_hidden(self):
        # spec.md C1: terminal sessions are visible because the read layer sees them, but
        # the page has to be able to tell which ones it may write to.
        with mock.patch.object(sdk, "list_sessions", return_value=[_info()]):
            body = (await self.client.get("/api/sessions", params={"cwd": "/tmp"})).json()
        self.assertEqual(len(body["sessions"]), 1)
        self.assertFalse(body["sessions"][0]["resumable"])

    async def test_refusing_to_resume_arrives_as_an_ndjson_error_line(self):
        # The status line is committed before streaming starts, so the caller only learns
        # of this by reading to the end. verify_0001.py depends on that being true.
        r = await self.client.post(
            "/api/send", json={"cwd": "/tmp", "text": "hi", "session_id": "not-ours"}
        )
        self.assertEqual(r.status_code, 200)
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertIn("not created by this app", lines[-1]["error"])

    async def test_the_api_app_does_not_own_the_page(self):
        """`spec.md` R8, from the API side.

        `/` must stay unclaimed here. Reflex mounts this app as the outer one, so a route
        for `/` defined in FastAPI would win over the compiled-frontend mount and the
        Python-built page would never render. 404 from the bare API app is correct.
        """
        self.assertEqual((await self.client.get("/")).status_code, 404)


class WorkspaceRoutes(unittest.IsolatedAsyncioTestCase):
    """The write surface. None of these clone -- the failures they test happen first."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = build(
            Config(workspaces=(), working_dir=str(self.root), data_dir=str(self.root))
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_adopting_a_directory_then_listing_it(self):
        (self.root / "repo").mkdir()
        r = await self.client.post("/api/workspaces", json={"name": "repo", "label": "Mine"})
        self.assertEqual(r.status_code, 200)
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["workspaces"][0]["label"], "Mine")
        self.assertEqual(body["workspaces"][0]["source"], "store")

    async def test_a_name_that_would_escape_the_root_is_refused(self):
        for bad in ("../escape", "/etc", "a/b", "", "."):
            r = await self.client.post("/api/workspaces", json={"name": bad})
            self.assertEqual(r.status_code, 400, bad)

    async def test_the_working_folder_cannot_be_set_over_http(self):
        """`spec.md` R11. The body naming one is ignored, not honoured."""
        (self.root / "repo").mkdir()
        await self.client.post(
            "/api/workspaces", json={"name": "repo", "working_dir": "/etc"}
        )
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["working_dir"], str(self.root))
        self.assertTrue(body["workspaces"][0]["path"].startswith(str(self.root)))

    async def test_adopting_something_that_is_not_there_is_refused(self):
        r = await self.client.post("/api/workspaces", json={"name": "ghost"})
        self.assertEqual(r.status_code, 400)

    async def test_adding_the_same_name_twice_is_refused(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.post("/api/workspaces", json={"name": "repo"})
        self.assertEqual(r.status_code, 400)

    async def test_label_can_be_changed_and_read_back(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.patch("/api/workspaces/repo", json={"label": "Renamed"})
        self.assertEqual(r.status_code, 200)
        body = (await self.client.get("/api/workspaces")).json()
        self.assertEqual(body["workspaces"][0]["label"], "Renamed")

    async def test_remove_delists_but_leaves_the_directory(self):
        (self.root / "repo").mkdir()
        await self.client.post("/api/workspaces", json={"name": "repo"})
        r = await self.client.delete("/api/workspaces/repo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertTrue((self.root / "repo").is_dir())

    async def test_a_clone_with_a_bad_url_leaves_no_trace(self):
        """`spec.md` R16: no listed workspace, and no leftover directory."""
        r = await self.client.post(
            "/api/workspaces", json={"name": "repo", "repo_url": "git@github.com:x/y.git"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 0)
        self.assertFalse((self.root / "repo").exists())

    async def test_pull_on_something_not_listed_is_refused(self):
        r = await self.client.post("/api/workspaces/ghost/pull")
        self.assertEqual(r.status_code, 400)


class WithoutAWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """Without a store: the write routes say why rather than crashing."""

    async def asyncSetUp(self):
        self.app = build(Config(workspaces=("/tmp",)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_listing_still_works(self):
        self.assertEqual((await self.client.get("/api/workspaces")).json()["count"], 1)

    async def test_writing_explains_the_missing_setting(self):
        r = await self.client.post("/api/workspaces", json={"name": "repo"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("COS_WORKING_DIR", r.json()["error"])


class NoHandWrittenMarkup(unittest.TestCase):
    """`spec.md` R8 as a standing check, not just a one-off in the proof.

    `coscc/_web/` is excluded, and the exclusion is narrow on purpose. Since `0011` that
    directory holds the *compiled* bundle — thousands of generated `.html` and `.css`
    files that the release copies into the package and `.gitignore` keeps out of git. The
    claim this test defends is about markup somebody typed, so generated output was never
    in its scope; before `0011` there simply was nowhere in `coscc/` for generated output
    to sit. Excluding any wider path would start hiding the thing it is here to catch.
    """

    GENERATED = "_web"

    def test_the_app_ships_no_hand_written_html_or_css(self):
        repo = Path(__file__).resolve().parent.parent
        found = [
            p.relative_to(repo)
            for p in list(repo.glob("coscc/**/*.html")) + list(repo.glob("coscc/**/*.css"))
            if self.GENERATED not in p.relative_to(repo).parts
        ]
        self.assertEqual(found, [], f"hand-written markup is back: {found}")

    def test_the_exclusion_is_only_the_compiled_bundle(self):
        # If someone widens GENERATED to something like "coscc", the check above passes
        # while defending nothing. This is the tripwire for that.
        self.assertEqual(self.GENERATED, "_web")


class Loopback(unittest.TestCase):
    """Until 2026-09-22 this asserted `127.0.0.1`, and it was right to.

    `0001` R5 made the default loopback deliberately, against Reflex's own `0.0.0.0`, and
    this test existed so the posture could not drift back by accident. `0011` changed it
    on purpose: the app now ships to a VM that people reach from elsewhere, and the
    originator decided that after being shown that this file's own module docstring
    describes an app with no authentication of any kind.

    So the guarantee this class protects is no longer "loopback". It is that a person is
    *told*, every single start, and `coscc/run_test.py` is where that is checked.
    """

    def test_the_default_bind_is_every_interface_since_0011(self):
        from coscc.config import from_env
        self.assertEqual(from_env({}).host, "0.0.0.0")

    def test_loopback_is_still_one_variable_away(self):
        # The capability `0001` built did not go away, it stopped being the default.
        from coscc.config import from_env
        self.assertEqual(from_env({"COS_HOST": "127.0.0.1"}).host, "127.0.0.1")


class ShutdownClosesSessions(unittest.IsolatedAsyncioTestCase):
    """The one place the CLI processes are closed, and until now the one nothing checked.

    `scripts/verify_0001.py:153` has to close them by hand because `httpx.ASGITransport`
    never sends lifespan events, so every other test in this file walks past this path. The
    lifespan context is driven directly here instead: no socket, no port, no session and no
    quota, which is what lets the check live in `npm test` rather than in a command nobody
    runs. What it does not prove is that uvicorn runs *this* app's lifespan in production —
    that holds because Reflex mounts its app inside the FastAPI one (`reflex/app.py:815`,
    measured 2026-09-22), and nothing here would notice if a later release reversed it.
    """

    def setUp(self):
        # Built outside the coroutine: IsolatedAsyncioTestCase runs the event loop in debug
        # mode, and constructing the app inside the task took long enough to trip asyncio's
        # slow-callback line. That is noise this unit exists to remove.
        self.app = build(Config(workspaces=("/tmp",)))
        self.app.state.sessions.close_all = mock.AsyncMock()

    async def test_leaving_the_lifespan_closes_every_session(self):
        app = self.app
        async with app.router.lifespan_context(app):
            # Startup must not close anything; the app is meant to be serving here.
            app.state.sessions.close_all.assert_not_awaited()
        app.state.sessions.close_all.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()


class UnitHistoryRoutes(unittest.IsolatedAsyncioTestCase):
    """`0013` R8 over HTTP. The route decides nothing; it only translates."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work").mkdir()
        self.cwd = str(root / "work")
        self.app = build(
            Config(
                workspaces=(self.cwd,),
                working_dir=self.cwd,
                data_dir=str(root / "data"),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        from coscc.history import History

        self.log = History(self.cwd, root / "data")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_directory_outside_the_list_is_refused_with_400(self):
        for path in ("/api/unit-history?cwd=/etc&unit=0001_a", "/api/units-with-history?cwd=/etc"):
            got = await self.client.get(path)
            self.assertEqual(got.status_code, 400, path)
            self.assertIn("/etc", got.json()["error"])

    async def test_it_returns_the_transitions_and_the_projection_over_them(self):
        self.log.record(self.cwd, "0001_a-problem", "intent.md", "draft")
        self.log.record(self.cwd, "0001_a-problem", "intent.md", "accepted", session="s1")
        got = await self.client.get(
            "/api/unit-history", params={"cwd": self.cwd, "unit": "0001_a-problem"}
        )
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(len(body["transitions"]), 2)
        self.assertEqual(body["state"]["intent.md"], "accepted")
        self.assertEqual(body["machine"], "coscc-default")
        self.assertEqual([s["session"] for s in body["sessions"]], ["s1"])
        self.assertEqual(body["unknown_transitions"], 1)

    async def test_the_list_of_units_with_a_history_is_its_own_route(self):
        self.log.record(self.cwd, "0001_a-problem", "intent.md", "draft")
        got = await self.client.get("/api/units-with-history", params={"cwd": self.cwd})
        self.assertEqual(got.json()["units"], ["0001_a-problem"])


QUESTIONS = (
    "# Intent: q\n"
    "Author: t. Type: feat. Status: accepted.\n\n"
    "## Problem\n\nx\n\n"
    "## Open questions\n\n"
    "1. One?\n"
    "2. Two?\n"
    "3. Three?\n"
)


class AnsweringAQuestionOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0016` R2, R3, R4. The route appends one block or writes nothing at all."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.data_dir = root / "data"
        self.app = build(
            Config(
                workspaces=(self.cwd,),
                working_dir=str(root / "work"),
                data_dir=str(self.data_dir),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        made = (await self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
        )).json()
        self.unit = made["unit"]
        self.dir = Path(made["path"])
        self.intent = self.dir / "intent.md"
        self.intent.write_text(QUESTIONS, encoding="utf-8")

    async def asyncTearDown(self):
        await self.client.aclose()

    def body(self, **over):
        return {
            "cwd": self.cwd, "unit": self.unit, "artifact": "intent.md",
            "question": 2, "answer": "Tách ra. MARK-0016", "answered_by": "Phong", **over,
        }

    async def post(self, **over):
        return await self.client.post("/api/units/answer", json=self.body(**over))

    async def test_a_valid_answer_is_appended_and_nothing_above_it_moves(self):
        before = self.intent.read_bytes()
        got = await self.post()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["question"], 2)
        after = self.intent.read_bytes()
        self.assertTrue(after.startswith(before))
        tail = after[len(before):].decode("utf-8")
        self.assertIn("## Answers", tail)
        self.assertIn("### Câu 2", tail)
        self.assertIn("Answered by: Phong. Date: ", tail)
        self.assertIn("Via: product.", tail)
        self.assertIn("Tách ra. MARK-0016", tail)

    async def test_the_board_then_counts_one_fewer_open(self):
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual(board["units"][0]["open"], 3)
        await self.post()
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual(board["units"][0]["open"], 2)

    async def test_a_second_answer_appends_under_the_same_heading(self):
        await self.post()
        await self.post(question=1, answer="Có.")
        text = self.intent.read_text(encoding="utf-8")
        self.assertEqual(text.count("## Answers"), 1)
        self.assertIn("### Câu 1", text)

    async def test_the_answer_is_recorded_as_a_person_in_the_history(self):
        from coscc.history import History

        await self.post()
        rows = History(str(Path(self.cwd).parent), self.data_dir).outputs(
            str(Path(self.cwd).resolve()), self.unit
        )
        mine = [r for r in rows if r["source"] == "answer"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["actor"], "human:Phong")
        self.assertEqual(mine[0]["path"], "intent.md")

    async def refused(self, **over):
        before = self.intent.read_bytes()
        got = await self.post(**over)
        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.intent.read_bytes(), before)
        return got.json()["error"]

    async def test_a_unit_that_does_not_exist_is_refused(self):  # (a)
        self.assertIn("no such work unit", await self.refused(unit="0099_nothing"))

    async def test_an_artifact_without_questions_is_refused(self):  # (b)
        await self.refused(artifact="spec.md")
        await self.refused(artifact="idea.md")

    async def test_a_number_that_is_not_a_question_is_refused(self):  # (c)
        self.assertIn("no question 4", await self.refused(question=4))
        await self.refused(question="two")

    async def test_an_empty_answer_is_refused(self):  # (d)
        await self.refused(answer="   \n  ")

    async def test_an_answer_with_no_name_is_refused(self):  # (e)
        await self.refused(answered_by="  ")
        await self.refused(answered_by="A\nStatus: rejected")

    async def test_a_closed_unit_is_refused(self):  # (f)
        self.intent.write_text(QUESTIONS.replace("Status: accepted", "Status: rejected"), encoding="utf-8")
        self.assertIn("closed", await self.refused())

    async def test_a_finished_unit_is_refused(self):  # (f)
        (self.dir / "plan.md").write_text("# Plan\nIntent: intent.md. Status: done.\n", encoding="utf-8")
        self.assertIn("finished", await self.refused())

    async def test_an_answer_that_would_be_read_as_a_heading_is_refused(self):  # (g)
        await self.refused(answer="ok\n## Status: rejected")
        await self.refused(answer="### Câu 3\nhijack")

    async def test_a_file_with_a_section_after_its_answers_is_refused(self):  # (g)
        self.intent.write_text(QUESTIONS + "\n## Answers\n\n## Later\n", encoding="utf-8")
        self.assertIn("after its ## Answers", await self.refused())

    async def test_something_that_is_not_json_writes_nothing(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/answer", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_a_directory_outside_the_list_is_refused(self):
        got = await self.client.post("/api/units/answer", json=self.body(cwd="/etc"))
        self.assertEqual(got.status_code, 400)


class PostingAReviewRoundOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0021` R8. The route posts a round once; a second press finds it and says so."""

    PR_URL = "https://github.com/o/r/pull/7"
    REVIEW = (
        "# Review: a problem\nAuthor: t. Status: changes-requested.\n\n"
        "## Round 1\n\nReviewed: abcdef1. Verdict: changes-requested.\n\n"
        "### Findings\n\n- F1 [open] a thing\n"
    )

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        made = (await self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
        )).json()
        self.unit = made["unit"]
        d = Path(made["path"])
        (d / "pr.md").write_text(f"# PR\nStatus: accepted.\nPR: {self.PR_URL}\n", encoding="utf-8")
        (d / "review.md").write_text(self.REVIEW, encoding="utf-8")
        self.calls: list[list[str]] = []
        self.comments: list[dict] = []

        async def gh(argv, cwd, stdin):
            self.calls.append(list(argv))
            if argv[:2] == ["pr", "view"]:
                return 0, json.dumps({"comments": self.comments}), ""
            self.comments.append({"body": stdin, "url": f"{self.PR_URL}#c1"})
            return 0, f"{self.PR_URL}#c1\n", ""

        from coscc import prcomment

        patcher = mock.patch.object(prcomment, "_gh", gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def post(self, **over):
        body = {"cwd": self.cwd, "unit": self.unit, "round": 1, **over}
        return await self.client.post("/api/units/review-comment", json=body)

    async def test_two_presses_make_one_comment(self):
        first, second = await self.post(), await self.post()
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual((first.json()["state"], second.json()["state"]), ("posted", "already"))
        self.assertEqual(len([c for c in self.calls if c[:2] == ["pr", "comment"]]), 1)

    async def test_the_board_then_shows_the_round_on_the_pr(self):
        await self.post()
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        [rnd] = board["units"][0]["rounds"]
        self.assertEqual(rnd["comment"]["url"], f"{self.PR_URL}#c1")

    async def test_bad_requests_are_400_and_reach_no_gh(self):
        for over in ({"round": 9}, {"round": "x"}, {"unit": "0099_nothing"}, {"cwd": "/etc"}):
            with self.subTest(over):
                self.assertEqual((await self.post(**over)).status_code, 400)
        got = await self.client.post("/api/units/review-comment", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.calls, [])


class StartingAUnitOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0014` R1 and R4 over HTTP. The routes translate and decide nothing."""

    async def asyncSetUp(self):
        import subprocess

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "work" / "proj"
        self.repo.mkdir(parents=True)
        for args in (
            ("init", "-q", "-b", "main"),
            ("add", "-A"),
        ):
            if args[0] == "add":
                (self.repo / "README.md").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=T",
             "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "first"],
            check=True, capture_output=True,
        )
        # A branch is cut from what `origin` has since
        # `0001_product-describes-a-state-it-is-not-in` R1, so there has to be one. A bare
        # directory, pushed to once: no network.
        remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        for args in (("remote", "add", "origin", str(remote)), ("push", "-q", "origin", "main")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.cwd = str(self.repo)
        self.app = build(
            Config(
                workspaces=(self.cwd,),
                working_dir=str(root / "work"),
                data_dir=str(root / "data"),
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_unit_is_created_and_then_visible_on_the_board(self):
        made = await self.client.post(
            "/api/units",
            json={"cwd": self.cwd, "slug": "a-first-problem", "brief": "Nút Run im lặng."},
        )
        self.assertEqual(made.status_code, 200, made.text)
        name = made.json()["unit"]
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual([u["name"] for u in board["units"]], [name])

    async def test_the_brief_is_stored_as_the_idea_the_intent_step_will_read(self):
        made = await self.client.post(
            "/api/units",
            json={"cwd": self.cwd, "slug": "a-problem", "brief": "Nút Run im lặng."},
        )
        body = made.json()
        self.assertTrue(body["brief"])
        text = (Path(body["path"]) / "idea.md").read_text(encoding="utf-8")
        self.assertIn("Nút Run im lặng.", text)

    async def test_a_directory_outside_the_list_is_refused_with_400(self):
        for call in (
            self.client.post("/api/units", json={"cwd": "/etc", "slug": "a-problem"}),
            self.client.post("/api/units/branch", json={"cwd": "/etc", "unit": "0001_a"}),
            self.client.get("/api/branch", params={"cwd": "/etc"}),
        ):
            got = await call
            self.assertEqual(got.status_code, 400, got.text)
            self.assertIn("/etc", got.json()["error"])

    async def test_a_bad_slug_is_400_in_the_scripts_own_words(self):
        got = await self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": "Bad_Slug"}
        )
        self.assertEqual(got.status_code, 400)
        self.assertIn("Bad_Slug", got.json()["error"])

    async def test_the_branch_route_cuts_it_and_the_read_route_sees_it(self):
        made = (await self.client.post(
            "/api/units", json={"cwd": self.cwd, "slug": "a-problem", "brief": "x"}
        )).json()
        (Path(made["path"]) / "intent.md").write_text(
            "# Intent: a problem\nAuthor: t. Type: feat. Status: accepted.\n", encoding="utf-8"
        )
        cut = await self.client.post(
            "/api/units/branch", json={"cwd": self.cwd, "unit": made["unit"]}
        )
        self.assertEqual(cut.status_code, 200, cut.text)
        self.assertEqual(cut.json()["branch"], "feat/a-problem")
        self.assertEqual(cut.json()["base"], "origin/main")
        self.assertEqual(len(cut.json()["sha"]), 7)
        seen = (await self.client.get("/api/branch", params={"cwd": self.cwd})).json()
        self.assertEqual(seen["branch"], "feat/a-problem")

    async def test_something_that_is_not_json_is_refused_before_anything_is_made(self):
        got = await self.client.post("/api/units", content=b"not json")
        self.assertEqual(got.status_code, 400)
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual(board["count"], 0)


class TheNextStageOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0024`. `GET /api/units/next` is `cos.mjs next`'s answer, and it starts nothing."""

    # The fixture of `AnsweringAQuestionOverHttp`, borrowed so its tests run once.
    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_the_route_names_the_stage_the_script_names(self):
        got = await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        # QUESTIONS is an accepted intent, so the files alone say `spec`.
        self.assertEqual((body["stage"], body["blocked"]), ("spec", True))
        self.assertIn("write-spec", body["action"])
        # Asking wrote nothing: the unit still holds only what the fixture put there.
        self.assertFalse((self.dir / "spec.md").exists())

    async def test_missing_or_unknown_arguments_are_a_400(self):
        for params in ({"unit": self.unit}, {"cwd": self.cwd}, {"cwd": self.cwd, "unit": "0099_nope"},
                       {"cwd": "/etc", "unit": self.unit}):
            with self.subTest(params=params):
                got = await self.client.get("/api/units/next", params=params)
                self.assertEqual(got.status_code, 400)
