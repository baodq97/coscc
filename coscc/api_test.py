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

from coscc import update
from coscc.api import build
from coscc.config import Config


def _tmp_config(test: unittest.TestCase) -> Config:
    """`/tmp` as the one workspace, and a data root of the test's own.

    `0076`: these fixtures used to leave `data_dir` unset, which is `~/.cos`, and
    `/api/send` opened the real database on every `npm test`. Nothing caught it until a
    step's environment named that database as one not to open.
    """
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Config(workspaces=("/tmp",), data_dir=tmp.name)


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
        self.app = build(_tmp_config(self))
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


class StageModelsOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0004_no-setting-says-which-model-runs-a-stage`."""

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

    async def rows(self):
        r = await self.client.get("/api/settings/models")
        self.assertEqual(r.status_code, 200)
        return {row["name"]: row for row in r.json()["rows"]}

    async def test_sixteen_rows_with_nothing_configured(self):
        # `0033` R9 and `0039`: nine stages, a `:novel` row for each of the four after `plan`,
        # `estimate` (`0074`), `precedent` (`0044`), chat.
        rows = await self.rows()
        self.assertEqual(len(rows), 16)
        self.assertEqual(list(rows)[-3:], ["estimate", "precedent", "chat"])
        self.assertEqual(rows["impl"]["source"], "default")
        self.assertEqual((rows["impl:novel"]["effort"], rows["impl:novel"]["effort_source"]), ("high", "default"))

    async def test_effort_set_then_remove(self):
        r = await self.client.post("/api/settings/efforts", json={"name": "impl:novel", "effort": "max"})
        self.assertEqual(r.status_code, 200)
        row = (await self.rows())["impl:novel"]
        self.assertEqual((row["effort"], row["effort_source"]), ("max", "override"))
        r = await self.client.post("/api/settings/efforts", json={"name": "impl:novel"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.rows())["impl:novel"]["effort_source"], "default")

    async def test_bad_effort_requests_are_400(self):
        for body in ({"name": "bogus", "effort": "low"}, {"name": "plan", "effort": "turbo"},
                     {"effort": "low"}, {"name": "chat", "effort": "low"}, {"name": "plan:novel", "effort": "low"}):
            r = await self.client.post("/api/settings/efforts", json=body)
            self.assertEqual(r.status_code, 400, body)
        r = await self.client.post("/api/settings/efforts", content=b"not json")
        self.assertEqual(r.status_code, 400)

    async def test_set_then_remove(self):
        r = await self.client.post("/api/settings/models", json={"name": "impl", "model": "m"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.rows())["impl"]["source"], "override")
        r = await self.client.post("/api/settings/models", json={"name": "impl"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((await self.rows())["impl"]["source"], "default")

    async def test_bad_requests_are_400(self):
        for body in ({"name": "bogus", "model": "m"}, {"name": "plan", "model": "  "},
                     {"model": "m"}, {"name": "plan", "model": 3}):
            r = await self.client.post("/api/settings/models", json=body)
            self.assertEqual(r.status_code, 400, body)
        r = await self.client.post("/api/settings/models", content=b"not json")
        self.assertEqual(r.status_code, 400)


class WithoutAWorkingFolder(unittest.IsolatedAsyncioTestCase):
    """Without a store: the write routes say why rather than crashing."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
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
    described an app that asked nobody for a password — true until `0070`.

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
        self.app = build(_tmp_config(self))
        self.app.state.sessions.close_all = mock.AsyncMock()

    async def test_leaving_the_lifespan_closes_every_session(self):
        app = self.app
        async with app.router.lifespan_context(app):
            # Startup must not close anything; the app is meant to be serving here.
            app.state.sessions.close_all.assert_not_awaited()
        app.state.sessions.close_all.assert_awaited_once()

    async def test_running_steps_are_cancelled_before_the_sessions_close(self):
        # `0034`. A step's task goes first, so it is not left writing after its client
        # was closed under it.
        app = self.app
        order = []
        app.state.service.shutdown = mock.AsyncMock(side_effect=lambda: order.append("steps"))
        app.state.sessions.close_all.side_effect = lambda: order.append("sessions")
        async with app.router.lifespan_context(app):
            app.state.service.shutdown.assert_not_awaited()
        app.state.service.shutdown.assert_awaited_once()
        self.assertEqual(order, ["steps", "sessions"])


class StoppingAStepOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0034`. The route decides nothing; it translates `Service.stop_step`."""

    async def asyncSetUp(self):
        self.app = build(_tmp_config(self))
        self.service = self.app.state.service
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_a_stop_on_nothing_running_is_a_400(self):
        r = await self.client.post("/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": "Lan"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no step running", r.json()["error"])

    async def test_a_stop_without_a_name_is_a_400(self):
        r = await self.client.post("/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": ""})
        self.assertEqual(r.status_code, 400)

    async def test_a_stop_of_a_running_step_is_a_200_naming_it(self):
        running = self.service.steps.claim(self.service._journal_key("/tmp"), "0001_a", "spec")
        r = await self.client.post("/api/board/stop", json={"cwd": "/tmp", "unit": "0001_a", "by": "Lan"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"unit": "0001_a", "stage": "spec", "stopped_by": "Lan"})
        self.assertTrue(running.stop_requested)

    async def test_the_running_list_is_what_the_registry_holds(self):
        self.assertEqual((await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json(), [])
        self.service.steps.claim(self.service._journal_key("/tmp"), "0001_a", "plan")
        [row] = (await self.client.get("/api/board/steps", params={"cwd": "/tmp"})).json()
        self.assertEqual((row["unit"], row["stage"], row["stopping"]), ("0001_a", "plan", False))

    async def test_the_running_list_refuses_a_directory_that_is_not_a_workspace(self):
        r = await self.client.get("/api/board/steps", params={"cwd": "/etc"})
        self.assertEqual(r.status_code, 400)


class WatchingAStepOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0073` R9. Two routes that translate `events_page` and `follow_events`, and write
    nothing. Neither is exempt from the login (`auth_test` walks every route for that)."""

    async def asyncSetUp(self):
        from coscc import events
        from coscc.data import Data

        self.app = build(_tmp_config(self))
        self.service = self.app.state.service
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.key = self.service._journal_key("/tmp")
        data = Data(self.service.config.data_dir)
        self.ended = events.Recorder("r-ended", data, "/w", self.key, "0001_a", "spec")
        for i in range(3):
            self.ended.denied("Bash", {"n": i}, "no")
        await self.ended.close("done", "")
        self.live = events.Recorder("r-live", data, "/w", self.key, "0001_a", "impl")
        self.live.denied("Bash", {}, "no")
        self.live._emit("end", outcome="done", detail="")
        self.live.closed = True
        self.service._recorders["r-live"] = self.live

    async def asyncTearDown(self):
        await self.client.aclose()

    def get(self, path, **params):
        return self.client.get(path, params={"cwd": "/tmp", "unit": "0001_a", **params})

    async def test_a_page_of_a_finished_step(self):
        r = await self.get("/api/board/events", run="r-ended", limit="2")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["status"], [e["seq"] for e in body["events"]]), ("ended", [3, 4]))
        one = (await self.get("/api/board/events", run="r-ended", seq="1")).json()
        self.assertEqual(one["events"][0]["input"], {"n": 0})

    async def test_refusals_are_400(self):
        from coscc import auth

        for params in ({"run": "r-ended", "limit": "many"}, {"run": "nope"}, {"run": "r-ended", "unit": "0002_b"}):
            r = await self.get("/api/board/events", **params)
            self.assertEqual(r.status_code, 400, params)
        r = await self.get("/api/board/events/follow", run="nope")
        self.assertEqual(r.status_code, 400)
        r = await self.get("/api/board/events/follow", run="r-live", after="x")
        self.assertEqual(r.status_code, 400)
        exempt = {path for _, path in auth.EXEMPT}
        self.assertFalse({"/api/board/events", "/api/board/events/follow"} & exempt)

    async def test_following_is_ndjson_that_ends_with_the_step(self):
        r = await self.get("/api/board/events/follow", run="r-live", after="0")
        lines = [json.loads(line) for line in r.text.splitlines()]
        self.assertEqual([(x["type"], x["seq"]) for x in lines], [("event", 1), ("event", 2)])
        self.assertEqual(lines[-1]["kind"], "end")
        r = await self.get("/api/board/events/follow", run="r-ended", after="0")
        [line] = [json.loads(line) for line in r.text.splitlines()]
        self.assertEqual((line["type"], line["status"]), ("status", "ended"))


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

    async def test_an_answer_with_no_name_is_recorded_as_owner(self):  # (e), `0082` R3
        await self.refused(answered_by="A\nStatus: rejected")
        got = await self.post(answered_by="  ")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["answered_by"], "owner")

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

    async def test_nobody_answers_as_jera(self):  # `0044` R11
        self.assertIn("Jera", await self.refused(answered_by=" jera "))


class AskingJeraOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0044`. `POST /api/units/precedent`: a 400 before any session, or a summary."""

    # The fixture of the class above, borrowed rather than inherited so its tests run once.
    _answering_setup = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown
    body = AnsweringAQuestionOverHttp.body
    post = AnsweringAQuestionOverHttp.post

    class _Sessions:
        def __init__(self) -> None:
            self.text, self.calls = "", 0

        def in_flight(self):
            return []

        async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
            self.calls += 1
            yield ("chunk", self.text)
            yield ("done", {"session_id": "s", "cost": {"cost_usd": 0.01, "turns": 1}})

    async def asyncSetUp(self):
        await self._answering_setup()
        self.sessions = self._Sessions()
        self.app.state.service.sessions = self.sessions

    async def ask(self, **over):
        return await self.client.post("/api/units/precedent", json={"cwd": self.cwd, "unit": self.unit, **over})

    async def test_a_unit_with_no_open_question_is_a_400_and_spends_nothing(self):
        for n in (1, 2, 3):
            self.assertEqual((await self.post(question=n)).status_code, 200)
        got = await self.ask()
        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.sessions.calls, 0)
        self.assertEqual((await self.ask(unit="0099_nothing")).status_code, 400)
        self.assertEqual((await self.client.post("/api/units/precedent", content=b"nope")).status_code, 400)

    async def test_a_reply_comes_back_as_a_summary(self):
        before = self.intent.read_bytes()
        self.sessions.text = '```json\n[{"artifact": "intent.md", "n": 1, "verdict": "needs-person", ' \
                             '"category": "product-direction", "text": "Đề xuất.", "reason": "hướng", "cites": []}]\n```'
        got = await self.ask()
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual(body["outcome"], "done")
        self.assertEqual([q["n"] for q in body["needs_person"]], [1, 2, 3])
        self.assertEqual((body["written"], body["cost_usd"]), ([], 0.01))
        self.assertEqual(self.intent.read_bytes(), before)


class RecordingAnOutcomeOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0047` R1, R7. The route appends one `### Outcome` block or writes nothing at all;
    what it refuses is `Service.record_outcome`'s decision, tested there."""

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
        unit_dir = Path(made["path"])
        for stage in ("spec", "impl", "pr", "review", "ship"):
            (unit_dir / f"{stage}.md").write_text(f"# {stage}\nStatus: accepted.\n", encoding="utf-8")
        (unit_dir / "plan.md").write_text("# plan\nStatus: done.\n", encoding="utf-8")
        self.intent = unit_dir / "intent.md"
        self.intent.write_text(QUESTIONS, encoding="utf-8")

    async def asyncTearDown(self):
        await self.client.aclose()

    def body(self, **over):
        return {
            "cwd": self.cwd, "unit": self.unit, "result": "trượt", "measured_by": "agent",
            "source": "board, 2026-10-08", "recorded_by": "Phong", **over,
        }

    async def test_a_valid_outcome_is_appended_and_the_board_shows_it(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/outcome", json=self.body())
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["result"], got.json()["recorded_by"]), ("trượt", "Phong"))
        after = self.intent.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertIn("### Outcome", after[len(before):].decode("utf-8"))
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual(board["units"][0]["outcome"]["result"], "missed")
        self.assertEqual(board["units"][0]["outcome_label"]["text"], "trượt")

    async def test_a_refusal_is_a_400_and_writes_nothing(self):
        before = self.intent.read_bytes()
        got = await self.client.post("/api/units/outcome", json=self.body(source=""))
        self.assertEqual(got.status_code, 400)
        self.assertIn("source", got.json()["error"])
        for bad in (b"nope", b"[1]"):
            got = await self.client.post("/api/units/outcome", content=bad)
            self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)


class HoldingAUnitOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0045`. `POST /api/units/hold` appends one block, or answers 400 and writes nothing."""

    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def hold(self, **over):
        body = {"cwd": self.cwd, "unit": self.unit, "to": "paused", "reason": "chờ 0034", "by": "Leif", **over}
        return await self.client.post("/api/units/hold", json=body)

    async def test_a_pause_is_appended_and_read_back(self):
        before = self.intent.read_bytes()
        got = await self.hold()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["from"], got.json()["to"], got.json()["effects"]), ("active", "paused", []))
        self.assertTrue(self.intent.read_bytes().startswith(before))
        self.assertIn("### Paused\nDecided by: Leif. Date: ", self.intent.read_text(encoding="utf-8"))
        nxt = (await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})).json()
        self.assertEqual(nxt["stage"], "")

    async def test_a_refusal_is_400_and_writes_nothing(self):
        before = self.intent.read_bytes()
        for over in ({"to": "active"}, {"reason": ""}, {"to": "sideways"}, {"cwd": "/etc"}):
            got = await self.hold(**over)
            self.assertEqual(got.status_code, 400, over)
        got = await self.client.post("/api/units/hold", content=b"nope")
        self.assertEqual(got.status_code, 400)
        self.assertEqual(self.intent.read_bytes(), before)

    async def test_no_name_is_recorded_as_owner(self):
        """`0082` R3: what `{"by": ""}` was refused for until then."""
        got = await self.hold(by="")
        self.assertEqual(got.status_code, 200, got.text)
        self.assertIn("owner", self.intent.read_text(encoding="utf-8").split("## Answers", 1)[1])


_ROUND_0028 = "\n## Round {n}\n\nReviewed: aaaaaaa. Verdict: {v}.\n\n### Findings\n\n{f}\n"
REVIEW_CLAIMED = (
    "# Review: q\nAuthor: t. Status: changes-requested.\n"
    + _ROUND_0028.format(n=1, v="changes-requested", f="- F2 [open] b\n- F3 [open] c")
)
REVIEW_CONFIRMED = REVIEW_CLAIMED + _ROUND_0028.format(
    n=2, v="needs-person", f="- F2 [needs-person] b\n- F3 [needs-person] c"
)


class AnsweringAFindingOverHttp(AnsweringAQuestionOverHttp):
    """`0028` R7, plan step 8. A finding the last review round confirmed needs a person is
    answered by its id into `review.md`; nothing else may be answered that way. Inherits the
    `0016` class's setup and helpers, so the `0016` tests also run once more on this unit —
    they answer `intent.md`, which this fixture leaves as it was."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for name, text in {
            "spec.md": "Status: accepted.\n",
            "plan.md": "Status: accepted.\n",
            "impl.md": "# Impl\nStatus: accepted.\n\n## Needs a person\n\n- F2: no budget\n- F3: no gh\n",
            "pr.md": "PR: https://github.com/o/r/pull/3. Status: accepted.\n",
            "review.md": REVIEW_CONFIRMED,
        }.items():
            (self.dir / name).write_text(text, encoding="utf-8")
        self.review = self.dir / "review.md"

    async def finding(self, **over):
        return await self.post(**{"artifact": "review.md", "question": "F2", **over})

    async def test_a_finding_is_appended_as_its_own_block_and_nothing_above_moves(self):
        before = self.review.read_bytes()
        got = await self.finding()
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["question"], "F2")
        after = self.review.read_bytes()
        self.assertTrue(after.startswith(before))
        tail = after[len(before):].decode("utf-8").splitlines()
        at = tail.index("### F2")
        self.assertRegex(tail[at + 1], r"^Answered by: Phong\. Date: \S+\. Via: product\.$")
        self.assertIn("## Answers", tail)

    async def test_the_board_then_waits_on_the_other_one_only(self):
        await self.finding()
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        [u] = board["units"]
        self.assertEqual(u["waiting"], ["F3"])
        self.assertEqual([p["answered"] for p in u["person_findings"]], [True, False])

    async def refused_in_review(self, **over):
        before = self.review.read_bytes()
        got = await self.finding(**over)
        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.review.read_bytes(), before)
        return got.json()["error"]

    async def test_a_finding_not_awaiting_a_person_is_refused(self):
        self.assertIn("F9 is not a finding", await self.refused_in_review(question="F9"))

    async def test_a_finding_is_answered_only_in_review_md(self):
        before = (self.dir / "impl.md").read_bytes()
        got = await self.finding(artifact="impl.md")
        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("answered in review.md", got.json()["error"])
        self.assertEqual((self.dir / "impl.md").read_bytes(), before)

    async def test_a_claim_no_review_has_confirmed_is_refused(self):
        self.review.write_text(REVIEW_CLAIMED, encoding="utf-8")
        await self.refused_in_review()


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
        # `0017`: the workspace stays on `main`; the branch is on the unit's worktree, and
        # the board says so.
        seen = (await self.client.get("/api/branch", params={"cwd": self.cwd})).json()
        self.assertEqual(seen["branch"], "main")
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        tree = next(u["worktree"] for u in board["units"] if u["name"] == made["unit"])
        self.assertEqual(tree["branch"], "feat/a-problem")
        self.assertEqual(tree["path"], cut.json()["worktree"])

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


class WhatIsRunningOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0051` R2. `GET /api/board/running`: two keys, and nothing a stop button would need."""

    asyncSetUp = AnsweringAQuestionOverHttp.asyncSetUp
    asyncTearDown = AnsweringAQuestionOverHttp.asyncTearDown

    async def test_missing_or_foreign_cwd_is_a_400(self):
        for params in ({}, {"cwd": ""}, {"cwd": "/etc"}):
            with self.subTest(params=params):
                got = await self.client.get("/api/board/running", params=params)
                self.assertEqual(got.status_code, 400)

    async def test_both_keys_and_no_session_id_prompt_or_path(self):
        service = self.app.state.service
        key = service._journal_key(self.cwd)
        service._mark_running(key, self.unit, "impl", "step")
        service._journal().started(
            key, "0099_other", "plan", "manual", session_id="sess-secret", prompt_chars=10,
        )
        got = await self.client.get("/api/board/running", params={"cwd": self.cwd})
        self.assertEqual(got.status_code, 200, got.text)
        body = got.json()
        self.assertEqual(set(body), {"running", "unknown_end"})
        self.assertEqual(body["running"][self.unit][0]["agent"], {"glyph": "ᚢ", "name": "Uruz"})
        self.assertEqual(list(body["unknown_end"]), ["0099_other"])

        keys: set[str] = set()

        def walk(node):
            if isinstance(node, dict):
                keys.update(node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(body)
        for banned in ("session_id", "prompt", "prompt_chars", "worktree", "path", "workspace", "cwd"):
            self.assertNotIn(banned, keys)
        self.assertNotIn("sess-secret", got.text)


class IntegratingOverHttp(PostingAReviewRoundOverHttp):
    """`0035` R12 over HTTP: outside the window is a 400 before anything runs, and the
    route is never a stage `next` offers (R3)."""

    async def test_a_unit_outside_the_window_is_a_400_and_leaves_a_record(self):
        # A draft pr.md: `cos.mjs` says the unit is not between pr and ship, so no gh is asked.
        pr_md = Path(self.app.state.service._unit_dir(self.cwd, self.unit)) / "pr.md"
        pr_md.write_text(f"# PR\nStatus: draft.\nPR: {self.PR_URL}\n", encoding="utf-8")
        got = await self.client.post("/api/units/integrate", json={"cwd": self.cwd, "unit": self.unit})
        self.assertEqual(got.status_code, 400)
        self.assertIn("not between pr and ship", got.json()["error"])
        service = self.app.state.service
        rows = service._journal().records(service._journal_key(self.cwd), kind="integration")
        self.assertEqual([r["outcome"] for r in rows], ["refused"])

    async def test_unknown_arguments_are_a_400(self):
        for body in ({"cwd": self.cwd, "unit": "0099_nope"}, {"cwd": "/etc", "unit": self.unit}, {}):
            with self.subTest(body=body):
                got = await self.client.post("/api/units/integrate", json=body)
                self.assertEqual(got.status_code, 400)

    async def test_next_never_offers_it(self):
        got = await self.client.get("/api/units/next", params={"cwd": self.cwd, "unit": self.unit})
        self.assertNotEqual(got.json().get("stage"), "integrate")

    # The parent's own tests post comments; they are not this class's to run again.
    test_two_presses_make_one_comment = None  # type: ignore[assignment]
    test_the_board_then_shows_the_round_on_the_pr = None  # type: ignore[assignment]
    test_bad_requests_are_400_and_reach_no_gh = None  # type: ignore[assignment]


class UpdateRoutes(unittest.IsolatedAsyncioTestCase):
    """`0068` R2, R11 and R15 over HTTP."""

    SHA = "0" * 40

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = build(Config(workspaces=(self.tmp.name,), data_dir=str(Path(self.tmp.name) / "d")))
        self.service = self.app.state.service
        self.updater = self.service.updater
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    def as_a_service(self):
        self.updater._me = {
            "version": "0.12.0", "commit": self.SHA, "commit_label": self.SHA, "install": "package",
            "shape": "service", "reason": "", "build_id": f"0.12.0+{self.SHA}", "uv": "/u/uv",
            "tool_dir": "/t", "bin_dir": "/b",
        }
        self.updater.release = {"state": "ready", "version": "0.13.0"}

    async def test_status_says_what_runs_and_why_there_is_no_button(self):
        body = (await self.client.get("/api/update")).json()
        self.assertEqual(body["shape"], "unavailable")
        self.assertIn(update.UNAVAILABLE, body["reason"])
        self.assertTrue(body["version"])

    async def test_every_post_is_409_where_updates_are_unavailable(self):
        for path in ("/api/update/apply", "/api/update/cancel", "/api/update/build-local"):
            with self.subTest(path=path):
                r = await self.client.post(path, json={"channel": "release", "mode": "wait", "by": "an"})
                self.assertEqual(r.status_code, 409)
        self.assertEqual((await self.client.get("/api/update/cut-list")).status_code, 409)

    async def test_a_source_in_the_body_is_never_read(self):
        self.as_a_service()
        seen = []

        async def apply(channel, mode, by, token=""):
            seen.append((channel, mode, by, token))
            return {"state": "applying"}

        self.updater.apply = apply
        plain = {"channel": "release", "mode": "wait", "by": "an", "token": ""}
        smuggled = {**plain, "url": "https://evil.example/x.whl", "path": "/tmp/x.whl",
                    "version": "9.9.9", "ref": "evil", "wheel": "/tmp/x.whl"}
        a = await self.client.post("/api/update/apply", json=plain)
        b = await self.client.post("/api/update/apply", json=smuggled)
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        self.assertEqual(seen, [("release", "wait", "an", "")] * 2)

    async def test_r11_is_503_on_run_integrate_send_and_build(self):
        self.as_a_service()
        self.updater.window = True
        cwd = self.tmp.name
        for path, body in (
            ("/api/board/run", {"cwd": cwd, "unit": "0001_a", "stage": "impl"}),
            ("/api/units/integrate", {"cwd": cwd, "unit": "0001_a"}),
            ("/api/send", {"cwd": cwd, "text": "hi"}),
            ("/api/update/build-local", {"by": "an"}),
        ):
            with self.subTest(path=path):
                r = await self.client.post(path, json=body)
                self.assertEqual(r.status_code, 503, r.text)
                self.assertIn("update is being applied", r.json()["error"])

    async def test_a_stale_cut_list_is_refused_with_the_fresh_one(self):
        self.as_a_service()
        self.service.steps.claim("/w", "0001_a", "impl")
        r = await self.client.post("/api/update/apply", json={
            "channel": "release", "mode": "now", "by": "an", "token": "old"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(r.json()["cut_list"]["items"]), 1)

    def test_no_update_route_uses_a_path_reflex_reserves(self):
        paths = [getattr(r, "path", "") for r in self.app.routes if "update" in getattr(r, "path", "")]
        self.assertEqual(len(paths), 5)
        for p in paths:
            self.assertFalse(p.startswith(("/ping/", "/_event", "/_upload")), p)


class TheBacklogOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0074`. Each route is 200 on a valid body and 400 on a refusal; what is refused is
    `backlog.py`'s and `Service`'s decision, tested there."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "work" / "proj").mkdir(parents=True)
        self.cwd = str(root / "work" / "proj")
        self.app = build(
            Config(workspaces=(self.cwd,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")
        post = lambda slug: self.client.post("/api/units", json={"cwd": self.cwd, "slug": slug, "brief": "x"})  # noqa: E731
        self.a = (await post("one-problem")).json()["unit"]
        self.b = (await post("two-problem")).json()["unit"]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_estimate_relation_and_shortlist(self):
        est = {"cwd": self.cwd, "unit": self.a, "value": 3, "effort": "M", "basis": "x", "by": "Leif"}
        self.assertEqual((await self.client.post("/api/backlog/estimate", json=est)).status_code, 200)
        bad = await self.client.post("/api/backlog/estimate", json={**est, "value": 0})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("value", bad.json()["error"])
        rel = {"cwd": self.cwd, "unit": self.a, "other": self.b, "type": "trùng", "op": "add", "reason": "r", "by": "L"}
        self.assertEqual((await self.client.post("/api/backlog/relation", json=rel)).status_code, 200)
        self.assertEqual((await self.client.post("/api/backlog/relation", json=rel)).status_code, 400)
        short = {"cwd": self.cwd, "units": [self.a], "reason": "r", "by": "L"}
        self.assertEqual((await self.client.post("/api/backlog/shortlist", json=short)).status_code, 200)
        self.assertEqual(
            (await self.client.post("/api/backlog/shortlist", json={**short, "units": [self.b]})).status_code, 400
        )
        board = (await self.client.get("/api/board", params={"cwd": self.cwd})).json()
        self.assertEqual([e["unit"] for e in board["backlog"]["shortlist"]], [self.a])
        self.assertTrue(board["backlog"]["propose_warning"])
        self.assertEqual((await self.client.post("/api/backlog/shortlist", content=b"nope")).status_code, 400)

    async def test_propose_streams_and_a_refusal_is_a_400(self):
        class Replies:
            async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
                yield ("chunk", "not json")
                yield ("done", {"session_id": "s", "cost": {"cost_usd": 0.01}})

        self.app.state.service.sessions = Replies()
        got = await self.client.post("/api/backlog/propose", json={"cwd": self.cwd})
        self.assertEqual(got.status_code, 200)
        last = json.loads(got.text.strip().splitlines()[-1])
        self.assertEqual((last["type"], last["estimate"]["outcome"]), ("done", "failed"))
        self.assertEqual((await self.client.post("/api/backlog/propose", json={"cwd": "/nope"})).status_code, 400)


class TheAutopilotsSettingsOverHttp(unittest.IsolatedAsyncioTestCase):
    """`0043` R1, R2 and `spec.md ## Answers`, câu 3."""

    async def client_for(self, host: str) -> httpx.AsyncClient:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Config(workspaces=("/tmp",), data_dir=tmp.name, working_dir=tmp.name, host=host)
        app = build(self.config)
        self.service = app.state.service
        self.started: list[str] = []
        self.service.autopilot_start = self.started.append
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        self.addAsyncCleanup(client.aclose)
        return client

    def prefs(self) -> dict:
        from coscc.data import Data
        return Data(self.config.data_dir).prefs()

    async def test_defaults_are_off_four_and_fifty(self):
        client = await self.client_for("127.0.0.1")
        got = (await client.get("/api/settings/autopilot", params={"cwd": "/tmp"})).json()
        self.assertEqual(
            (got["autopilot"], got["autopilot_may_ship"], got["max_parallel"], got["daily_cap_usd"]),
            (False, False, 4, 50.0),
        )
        self.assertEqual(got["refused_because"], "")

    async def test_a_wrong_value_is_a_400_and_nothing_is_written(self):
        client = await self.client_for("127.0.0.1")
        for name, value in [
            ("autopilot", "yes"), ("autopilot", 1), ("autopilot_may_ship", None),
            ("max_parallel", 0), ("max_parallel", 2.5), ("max_parallel", True), ("max_parallel", "3"),
            ("daily_cap_usd", 0), ("daily_cap_usd", -1), ("daily_cap_usd", True), ("daily_cap_usd", "50"),
            ("no_such", 1),
        ]:
            got = await client.post("/api/settings/autopilot", json={"cwd": "/tmp", "name": name, "value": value})
            self.assertEqual(got.status_code, 400, (name, value))
        # JSON has no infinity; the service refuses one too.
        from coscc.service import Invalid
        with self.assertRaises(Invalid):
            self.service.set_autopilot("/tmp", "daily_cap_usd", float("inf"))
        self.assertEqual(self.prefs(), {})
        self.assertEqual(self.started, [])

    async def test_off_loopback_the_switch_will_not_turn_on(self):
        client = await self.client_for("0.0.0.0")
        got = await client.post("/api/settings/autopilot", json={"cwd": "/tmp", "name": "autopilot", "value": True})
        self.assertEqual(got.status_code, 400)
        self.assertIn("restart it on 127.0.0.1", got.json()["error"])
        self.assertEqual((self.prefs(), self.started), ({}, []))
        # Everything but the switch itself may still be set.
        ok = await client.post("/api/settings/autopilot", json={"cwd": "/tmp", "name": "max_parallel", "value": 2})
        self.assertEqual(ok.status_code, 200)

    async def test_a_change_is_stored_started_and_logged_with_old_and_new(self):
        from coscc.journal import Journal
        client = await self.client_for("127.0.0.1")
        got = await client.post("/api/settings/autopilot", json={"cwd": "/tmp", "name": "autopilot", "value": True})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(got.json()["autopilot"])
        self.assertEqual(self.started, ["/tmp"])
        await client.post("/api/settings/autopilot", json={"cwd": "/tmp", "name": "daily_cap_usd", "value": 20})
        rows = Journal(self.config.working_dir, self.config.data_dir).records(kind="setting")
        self.assertEqual(
            [(r["name"], r["old"], r["new"]) for r in rows],
            [("autopilot:/tmp", False, True), ("autopilot_daily_cap_usd", 50.0, 20.0)],
        )


class NoRequestIsTheAutopilot(unittest.IsolatedAsyncioTestCase):
    """`0043` R3. `started_by` is not read off a body: a request is always `person`."""

    async def test_a_body_naming_the_autopilot_is_not_believed(self):
        app = build(_tmp_config(self))
        called: list[tuple] = []

        async def run_step(*args, **kwargs):
            called.append(("run", args, kwargs))
            yield ("done", {"outcome": "done"})

        async def integrate(*args, **kwargs):
            called.append(("integrate", args, kwargs))
            yield ("done", {"integration": {}})

        service = app.state.service
        service.run_step = run_step
        service.integrate = integrate
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            body = {"cwd": "/tmp", "unit": "0001_a", "stage": "spec", "started_by": "autopilot"}
            self.assertEqual((await client.post("/api/board/run", json=body)).status_code, 200)
            await client.post("/api/units/integrate", json=body)
        self.assertEqual([c[0] for c in called], ["run", "integrate"])
        for _, args, kwargs in called:
            self.assertNotIn("started_by", kwargs)
            self.assertNotIn("autopilot", args)
