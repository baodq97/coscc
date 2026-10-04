"""`coscc/features/vault/`, the person's side: the page and the routes, driven over ASGI.

The bait values of `test_vault.py`'s `Bed` are in the store throughout; every response and header
of every route is read for them in all five forms.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

from coscc import kernel
from coscc import auth, screens, vault
from coscc.agent import policy
from coscc.features import vault as feature
from coscc.features.vault import page
from coscc.vault.store import NAME
from tests.features import test_vault as base

GET_ROUTES = {"/vault", "/api/vault/secrets", "/api/vault/leaks"}
POST_ROUTES = {
    "/api/vault/secrets",
    "/api/vault/policy",
    "/api/vault/grant",
    "/api/vault/revoke",
    "/api/vault/delete",
}


class Http(base.Bed):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.seen: list[tuple[str, bytes]] = []

    def note(self, r: httpx.Response) -> httpx.Response:
        head = json.dumps(dict(r.headers)).encode()
        self.seen += [(f"{r.request.method} {r.request.url.path}", r.content), ("headers", head)]
        return r

    async def get(self, path: str, **params) -> httpx.Response:
        return self.note(await self.client.get(path, params={"cwd": str(self.ws), **params}))

    async def send(self, path: str, **sent) -> httpx.Response:
        return self.note(await self.client.post(path, json={"cwd": str(self.ws), **sent}))

    async def form(self, **fields) -> httpx.Response:
        data = {"cwd": str(self.ws), **fields}
        return self.note(await self.client.post("/api/vault/secrets", data=data))

    async def turn_off(self):
        await self.client.post(
            "/api/features", json={"cwd": str(self.ws), "name": "vault", "on": False}
        )

    def vault_lines(self, **kw):
        return self.journal().records(self.key, None, kind="vault", **kw)


class ThePage(Http):
    async def test_it_is_a_plain_form_with_no_reflex_hook_and_lists_metadata(self):
        r = await self.get("/vault")
        self.assertEqual((r.status_code, r.headers["content-type"][:9]), (200, "text/html"))
        html = r.text
        self.assertIn('<form method="post" action="/api/vault/secrets"', html)
        self.assertIn('<textarea name="value"', html)
        self.assertNotIn('type="password"', html)
        self.assertNotIn("handleSubmit", html)
        self.assertNotIn("onsubmit", html.lower())
        for name in ("ws:db", "global:tok", "global:other", "the database", "a shared token"):
            self.assertIn(name, html)
        self.assertIn('data-act="grant"', html)
        self.assertIn('data-act="revoke"', html)
        self.assertNotIn('name="by"', html)

    async def test_it_asks_for_no_name_of_the_person(self):
        html = (await self.get("/vault")).text.lower()
        for word in ("your name", "who are you", "author"):
            self.assertNotIn(word, html)

    async def test_without_a_workspace_it_says_so_and_a_wrong_one_is_400(self):
        r = self.note(await self.client.get("/vault"))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<form", r.text)
        self.assertEqual((await self.get("/vault", cwd="/etc")).status_code, 400)

    async def test_a_workspace_without_secrets_says_so_once(self):
        for s in self.store.all():
            self.store.delete(s.name, s.workspace)
        html = (await self.get("/vault")).text
        self.assertIn("No secrets here yet.", html)

    async def test_what_the_redirect_says_is_shown_escaped(self):
        html = (await self.get("/vault", error="<script>x</script>", saved="ws:db", short="1")).text
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Saved ws:db.", html)
        self.assertIn("That value is short", html)

    async def test_secrets_are_a_table_and_every_change_opens_in_a_dialog(self):
        html = (await self.get("/vault")).text
        self.assertIn("<th>Name</th><th>Kept for</th><th>Used by</th><th>Passed as</th>", html)
        row = html[html.index('<span class="name">ws:db') :]
        row = row[: row.index("</tr>")]
        self.assertIn('data-open="access-', row)
        self.assertIn('data-open="value-', row)
        self.assertIn('data-act="delete"', row)
        self.assertIn('data-open="add"', html[: html.index("<table")])
        access = html[html.index('<dialog id="access-') :]
        self.assertIn('data-act="policy"', access[: access.index("</dialog>")])
        form = html[html.index('<dialog id="add"') :]
        form = form[: form.index("</dialog>")]
        legends = [part.split("</legend>")[0] for part in form.split("<legend>")[1:]]
        self.assertEqual(legends, ["Secret", "Access", "Value"])
        self.assertEqual(form.count('type="submit"'), 1)
        self.assertIn(">Add secret</button>", form)

    def test_the_name_rule_on_the_page_is_the_stores(self):
        rule = re.compile(page.NAME_PATTERN, re.ASCII)
        for name in ("deploy-key.v2", "db", "a_b", "Bad Name", "-db", "ws:db", "x" * 64):
            store = bool(NAME.fullmatch("ws:" + name))
            self.assertEqual(bool(rule.fullmatch(name)) and len(name) <= 64, store, name)

    async def test_without_age_it_says_so_and_no_value_can_be_saved(self):
        self.store.age = str(self.ws / "no-such-age")
        html = (await self.get("/vault")).text
        self.assertIn(page.NO_AGE, html)
        self.assertIn("disabled>Add secret</button>", html)
        self.assertIn("disabled>Replace value</button>", html)
        self.assertIn("Install age to save a value.", html)
        self.store.age = shutil.which("true") or "/bin/true"
        self.assertNotIn(page.NO_AGE, (await self.get("/vault")).text)

    async def test_with_the_vault_off_it_opens_with_delete_and_revoke_only(self):
        await self.turn_off()
        r = await self.get("/vault")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Vault is off for this workspace", r.text)
        self.assertNotIn('action="/api/vault/secrets"', r.text)
        self.assertNotIn('data-act="policy"', r.text)
        self.assertNotIn('data-act="grant"', r.text)
        self.assertIn('data-act="delete"', r.text)
        self.assertIn('data-act="revoke"', r.text)


class MetadataRoutes(Http):
    async def test_secrets_lists_metadata_and_no_value_or_size(self):
        got = (await self.get("/api/vault/secrets")).json()
        self.assertEqual(got["workspace"], self.key)
        self.assertEqual({s["name"] for s in got["secrets"]}, {"ws:db", "global:tok"})
        self.assertEqual(
            {s["name"]: s["granted"] for s in got["globals"]},
            {"global:tok": True, "global:other": False},
        )
        for row in [*got["secrets"], *got["globals"]]:
            self.assertEqual(
                set(row),
                {
                    "name",
                    "tier",
                    "description",
                    "stages",
                    "modes",
                    "broker",
                    "has_value",
                    "granted",
                },
            )

    async def test_leaks_names_the_last_scan_and_a_clean_one_clears_it(self):
        self.tree()
        (self.root / "unit").mkdir(exist_ok=True)
        (self.root / "unit" / "pr.md").write_bytes(base.DB)
        self.assertIn("ws:db", feature._leaks(self.ctx, lambda: self.store, self.facts("pr")) or "")
        got = (await self.get("/api/vault/leaks", unit="0001_thing")).json()
        self.assertEqual(got, {"unit": "0001_thing", "names": ["ws:db"]})
        (self.root / "unit" / "pr.md").write_bytes(b"clean")
        self.assertIsNone(feature._leaks(self.ctx, lambda: self.store, self.facts("pr")))
        got = (await self.get("/api/vault/leaks", unit="0001_thing")).json()
        self.assertEqual(got["names"], [])
        self.assertEqual((await self.get("/api/vault/leaks")).json()["names"], [])


class TheOneRouteAValueGoesInBy(Http):
    async def test_a_form_post_makes_a_secret_and_answers_303_with_no_value_anywhere(self):
        value = "brand-new/Value+1=&z"
        r = await self.form(
            name="fresh", tier="ws", description="fresh one", value=value, stage="impl", mode="env"
        )
        self.assertEqual((r.status_code, r.content), (303, b""))
        where = urlsplit(r.headers["location"])
        self.assertEqual(where.path, "/vault")
        self.assertEqual(parse_qs(where.query), {"cwd": [str(self.ws)], "saved": ["ws:fresh"]})
        made = self.store.get("ws:fresh", self.key)
        assert made is not None
        self.assertEqual(
            (made.description, made.stages, made.modes), ("fresh one", ("impl",), ("env",))
        )
        self.assertEqual(self.store.open("ws:fresh", self.key), value.encode())
        (line,) = [r for r in self.vault_lines() if r["name"] == "ws:fresh"]
        self.assertEqual(
            (line["action"], line["actor"], line["workspace"]), ("create", "human:owner", self.key)
        )
        self.assertNotIn(value, json.dumps(line))
        self.assertNotIn("by", line)

    async def test_the_same_name_replaces_the_value_and_keeps_the_policy(self):
        r = await self.form(name="ws:db", value="replaced-value")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.open("ws:db", self.key), b"replaced-value")
        self.assertEqual(self.store.get("ws:db", self.key).description, "the database")
        actions = [(r["action"], r["name"]) for r in self.vault_lines()]
        self.assertEqual(actions, [("replace", "ws:db")])

    async def test_a_global_secret_is_made_but_not_granted(self):
        await self.form(name="shared", tier="global", value="g-value-123")
        made = self.store.get("global:shared", "")
        assert made is not None
        self.assertEqual(made.granted, ())

    async def test_a_short_value_is_said_short_and_a_broker_secret_keeps_ssh_only(self):
        r = await self.form(name="tiny", value="abc", broker="1", mode="env", stage="impl")
        self.assertEqual(parse_qs(urlsplit(r.headers["location"]).query)["short"], ["1"])
        made = self.store.get("ws:tiny", self.key)
        assert made is not None
        self.assertEqual((made.broker, made.modes), (True, ("ssh",)))

    async def test_a_one_line_value_loses_the_line_break_typed_after_it(self):
        await self.form(name="tok2", value="one-line-token\r\n")
        self.assertEqual(self.store.open("ws:tok2", self.key), b"one-line-token")

    @unittest.skipUnless(
        all(shutil.which(p) for p in ("ssh-keygen", "ssh-agent", "ssh-add")), "needs OpenSSH"
    )
    async def test_a_key_pasted_in_the_box_reaches_ssh_add_whole(self):
        path = self.root / "id"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
            check=True,
            capture_output=True,
        )
        key = path.read_bytes()
        # How a browser sends a textarea: every line break as CRLF.
        await self.form(name="deploy", value=key.decode().replace("\n", "\r\n"), broker="1")
        self.assertEqual(self.store.open("ws:deploy", self.key), key)
        self.store.set_policy("ws:deploy", self.key, ("impl",), ("ssh",))
        facts = self.facts(commands=(*policy.IMPL_COMMANDS, "ssh-add"))
        args = {"command": "ssh-add -l", "uses": [{"name": "ws:deploy", "mode": "ssh"}]}
        got = await self.call(facts, "vault_exec", args)
        self.assertEqual(got["exit_code"], 0, got)
        self.assertIn("ED25519", got["stdout"])

    async def test_a_bad_name_goes_back_to_the_page_with_the_reason_and_makes_nothing(self):
        r = await self.form(name="Bad Name!", value="v-value-1234")
        self.assertEqual(r.status_code, 303)
        self.assertIn("error", parse_qs(urlsplit(r.headers["location"]).query))
        self.assertIsNone(self.store.get("ws:Bad Name!", self.key))
        self.assertEqual(self.vault_lines(), [])

    async def test_no_value_a_wrong_workspace_or_a_huge_body_is_400(self):
        self.assertEqual((await self.form(name="x", value="")).status_code, 400)
        self.assertEqual((await self.form(name="x", value="v", cwd="/etc")).status_code, 400)
        self.assertEqual((await self.form(name="x", value="v" * feature.MAX_FORM)).status_code, 400)
        self.assertIsNone(self.store.get("ws:x", self.key))

    async def test_it_is_the_only_route_that_reads_a_value(self):
        sent = {"name": "ws:db", "value": "sneaky-value-1", "secret": "sneaky-value-2"}
        for path in ("/api/vault/policy", "/api/vault/grant", "/api/vault/revoke"):
            await self.send(path, **sent)
        self.assertEqual(self.store.open("ws:db", self.key), base.DB)


class TheJsonRoutes(Http):
    async def test_policy_changes_stages_and_modes_and_writes_one_line(self):
        r = await self.send(
            "/api/vault/policy", name="ws:db", stages=["impl", "spike"], modes=["env"]
        )
        self.assertEqual(
            (r.status_code, r.json()["stages"], r.json()["modes"]),
            (200, ["impl", "spike"], ["env"]),
        )
        (line,) = self.vault_lines()
        self.assertEqual(
            (line["action"], line["actor"], line["stages"], line["modes"]),
            ("policy", "human:owner", ["impl", "spike"], ["env"]),
        )

    async def test_a_policy_the_store_refuses_is_400_and_writes_nothing(self):
        for sent in (
            {"stages": ["deploy"], "modes": ["env"]},
            {"stages": ["impl"], "modes": ["carrier-pigeon"]},
            {"stages": "impl", "modes": []},
            {"modes": []},
        ):
            r = await self.send("/api/vault/policy", name="ws:db", **sent)
            self.assertEqual(r.status_code, 400, sent)
            self.assertIn("error", r.json())
        self.assertEqual(self.vault_lines(), [])
        self.assertEqual(self.store.get("ws:db", self.key).stages, ("impl",))

    async def test_grant_and_revoke_move_a_global_secret_in_and_out_of_the_workspace(self):
        r = await self.send("/api/vault/grant", name="global:other")
        self.assertEqual((r.status_code, r.json()["granted"]), (200, True))
        self.assertIn("global:other", {s.name for s in self.store.visible(self.key)})
        r = await self.send("/api/vault/revoke", name="global:other")
        self.assertEqual((r.status_code, r.json()["granted"]), (200, False))
        self.assertNotIn("global:other", {s.name for s in self.store.visible(self.key)})
        lines = [(r["action"], r["name"], r["actor"]) for r in self.vault_lines()]
        self.assertEqual(
            lines,
            [("grant", "global:other", "human:owner"), ("revoke", "global:other", "human:owner")],
        )

    async def test_a_ws_secret_is_not_granted_and_an_unknown_one_is_400(self):
        for path in ("/api/vault/grant", "/api/vault/revoke"):
            self.assertEqual((await self.send(path, name="ws:db")).status_code, 400)
            self.assertEqual((await self.send(path, name="global:none")).status_code, 400)
        self.assertEqual(
            (await self.send("/api/vault/policy", name="ws:none", stages=[], modes=[])).status_code,
            400,
        )

    async def test_delete_removes_a_secret_and_writes_one_line(self):
        r = await self.send("/api/vault/delete", name="ws:db")
        self.assertEqual((r.status_code, r.json()), (200, {"deleted": "ws:db"}))
        self.assertIsNone(self.store.get("ws:db", self.key))
        (line,) = self.vault_lines()
        self.assertEqual(
            (line["action"], line["name"], line["actor"]), ("delete", "ws:db", "human:owner")
        )
        self.assertEqual((await self.send("/api/vault/delete", name="ws:db")).status_code, 400)

    async def test_a_body_that_is_no_json_object_is_400(self):
        for content in (b"[1]", b"nope"):
            r = self.note(await self.client.post("/api/vault/delete", content=content))
            self.assertEqual(r.status_code, 400)


class WithTheVaultOff(Http):
    async def test_every_route_refuses_the_workspace_but_delete_and_revoke(self):
        await self.turn_off()
        refused = [
            await self.get("/api/vault/secrets"),
            await self.get("/api/vault/leaks", unit="0001_thing"),
            await self.form(name="new", value="v-value-1234"),
            await self.send("/api/vault/policy", name="ws:db", stages=["impl"], modes=["env"]),
            await self.send("/api/vault/grant", name="global:other"),
        ]
        self.assertEqual([r.status_code for r in refused], [400] * 5)
        self.assertTrue(all("off" in r.json()["error"] for r in refused))
        self.assertIsNone(self.store.get("ws:new", self.key))
        self.assertEqual(self.store.get("ws:db", self.key).stages, ("impl",))
        self.assertEqual(self.vault_lines(), [])
        revoked = await self.send("/api/vault/revoke", name="global:tok")
        self.assertEqual((revoked.status_code, revoked.json()["granted"]), (200, False))
        deleted = await self.send("/api/vault/delete", name="ws:db")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual([r["action"] for r in self.vault_lines()], ["revoke", "delete"])

    async def test_the_tool_guard_and_block_go_with_it(self):
        await self.turn_off()
        parts = self.service.steps.hooks.for_step("impl", str(self.ws))
        self.assertEqual([t.server for t in parts.tools], [])
        self.assertEqual([g.name for g in parts.guards], [])
        self.assertEqual([b.name for b in parts.blocks if b.name == "vault"], [])

    async def test_it_is_on_again_when_switched_on(self):
        await self.turn_off()
        await self.client.post(
            "/api/features", json={"cwd": str(self.ws), "name": "vault", "on": True}
        )
        self.assertEqual((await self.get("/api/vault/secrets")).status_code, 200)


class NoRouteGivesAValueBack(Http):
    async def test_every_route_answers_with_no_bait_in_body_or_headers_in_any_of_five_forms(self):
        fresh = "fresh/Value+3&=z"
        calls = [
            await self.get("/vault"),
            await self.get("/vault", saved="ws:db", short="1", error="a reason"),
            await self.get("/api/vault/secrets"),
            await self.get("/api/vault/leaks", unit="0001_thing"),
            await self.form(name="fresh", value=fresh, description="d"),
            await self.form(name="ws:db", value="new-db-value"),
            await self.form(name="Bad Name", value=fresh),
            await self.form(name="x", value=""),
            await self.send(
                "/api/vault/policy", name="ws:db", stages=["impl"], modes=["env", "file"]
            ),
            await self.send("/api/vault/grant", name="global:other"),
            await self.send("/api/vault/revoke", name="global:other"),
            await self.send("/api/vault/delete", name="ws:fresh"),
            await self.send("/api/vault/delete", name="ws:none"),
        ]
        self.assertGreaterEqual(len(calls), 13)
        values = {**self.values(), "fresh": fresh.encode()}
        hits = vault.scan(values, self.seen)
        self.assertEqual(hits, [])
        # The scan can see: the forms it looks for are in a page that did carry one.
        self.assertTrue(vault.forms(base.DB)["raw"])
        self.assertEqual(vault.scan({"ws:db": base.DB}, [("x", b"page " + base.DB)])[0].form, "raw")

    async def test_the_routes_exercised_are_every_route_of_the_feature(self):
        found = {
            (m, r.path)
            for r in self.app.routes
            for m in getattr(r, "methods", ())
            if "vault" in r.path
        }
        self.assertEqual(
            found - {("HEAD", p) for p in GET_ROUTES},
            {("GET", p) for p in GET_ROUTES} | {("POST", p) for p in POST_ROUTES},
        )

    async def test_every_route_is_behind_the_login(self):
        exempt = {path for _, path in auth.EXEMPT}
        for path in GET_ROUTES | POST_ROUTES:
            self.assertNotIn(path, exempt)
        self.assertFalse([p for p in exempt if "vault" in p])

    async def test_no_route_takes_a_value_but_the_one(self):
        source = json.dumps(sorted(str(r.path) for r in self.app.routes if "vault" in r.path))
        self.assertIn("/api/vault/secrets", source)
        posts = [
            r for r in self.app.routes if "vault" in r.path and "POST" in getattr(r, "methods", ())
        ]
        self.assertEqual(len(posts), 5)


class TheSlots(unittest.TestCase):
    def test_the_script_draws_a_line_on_a_unit_and_names_only(self):
        script = feature.FEATURE.scripts[0]
        self.assertNotIn("slot-topbar", script)
        self.assertIn('window.coscc.slot("slot-unit"', script)
        self.assertIn("/api/vault/leaks", script)
        self.assertNotIn("innerHTML", script)

    def test_the_sidebar_entry_frames_the_page(self):
        self.assertEqual(feature.FEATURE.page, kernel.Page("Vault", "key-round", "/vault"))

    def test_the_shell_carries_it_once_and_the_slots_are_there(self):
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        self.assertEqual(shell.count("__coscc_vault = true"), 1)
        self.assertIn("slot-topbar", shell)


class ThePlugin(unittest.TestCase):
    def test_it_owns_the_store_s_tables_and_names_its_routes(self):
        self.assertEqual(feature.FEATURE.name, "vault")
        self.assertEqual(feature.FEATURE.tables, vault.TABLES)
        self.assertIsNotNone(feature.FEATURE.agent)


if __name__ == "__main__":
    unittest.main()
