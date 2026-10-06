"""The store: names, tiers, grants, and values that only ever sit encrypted."""

from __future__ import annotations

import stat
import unittest
from pathlib import Path

from coscc.vault.store import BadSecret, Secret, generate
from tests.vault.fakes import make_store

VALUE = b"hunter2-is-not-a-good-value"


class ANameSaysWhichTierItIsIn(unittest.TestCase):
    def setUp(self):
        self.store, _ = make_store(self)

    def test_a_bad_name_is_refused_with_the_shape_it_should_have(self):
        for name in ("db", "ws:", "ws:Upper", "team:x", "ws:-x", "ws:" + "a" * 65, "global:a b"):
            with self.subTest(name=name), self.assertRaises(BadSecret) as caught:
                self.store.create(name, "/w")
            self.assertIn("global:<name> or ws:<name>", str(caught.exception))

    def test_a_ws_secret_needs_a_workspace_and_a_global_one_keeps_none(self):
        with self.assertRaises(BadSecret):
            self.store.create("ws:db", "")
        made = self.store.create("global:cloud", "/w")
        self.assertEqual((made.tier, made.workspace, made.granted), ("global", "", ()))

    def test_two_workspaces_each_have_their_own_ws_db(self):
        self.store.create("ws:db", "/a", "first")
        self.store.create("ws:db", "/b", "second")
        self.assertEqual(self.store.get("ws:db", "/a").description, "first")
        self.assertEqual(self.store.get("ws:db", "/b").description, "second")
        self.assertIsNone(self.store.get("ws:db", "/c"))

    def test_a_name_in_use_is_refused_and_the_first_stays(self):
        self.store.create("ws:db", "/a", "first")
        with self.assertRaises(BadSecret) as caught:
            self.store.create("ws:db", "/a", "second")
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(self.store.get("ws:db", "/a").description, "first")

    def test_the_defaults_are_impl_with_env_and_file(self):
        made = self.store.create("ws:db", "/a", actor="agent:impl")
        self.assertEqual(
            (made.stages, made.modes, made.created_by), (("impl",), ("env", "file"), "agent:impl")
        )
        self.assertFalse(made.has_value)

    def test_a_secret_created_with_no_agent_is_usable_by_nobody(self):
        self.assertEqual(self.store.create("ws:db", "/a", stages=()).stages, ())

    def test_a_broker_secret_is_ssh_only_from_the_start_and_after_a_policy_change(self):
        made = self.store.create("global:jump", "", modes=("env",), broker=True)
        self.assertEqual(made.modes, ("ssh",))
        changed = self.store.set_policy("global:jump", "", ("impl", "spike"), ("env", "file"))
        self.assertEqual((changed.modes, changed.stages), (("ssh",), ("spike", "impl")))

    def test_a_policy_names_only_stages_and_modes_that_exist(self):
        self.store.create("ws:db", "/a")
        for stages, modes in ((("ship",), ("env",)), (("impl",), ("telepathy",))):
            with self.subTest(stages=stages, modes=modes), self.assertRaises(BadSecret):
                self.store.set_policy("ws:db", "/a", stages, modes)


class AGlobalSecretIsGrantedToAWorkspaceByAPerson(unittest.TestCase):
    def setUp(self):
        self.store, _ = make_store(self)
        self.store.create("global:cloud", "")
        self.store.create("ws:db", "/a")
        self.store.create("ws:db", "/b")

    def visible(self, workspace):
        return sorted(s.name + s.workspace for s in self.store.visible(workspace))

    def test_it_is_visible_only_once_granted_and_no_more_once_revoked(self):
        self.assertEqual(self.visible("/a"), ["ws:db/a"])
        granted = self.store.grant("global:cloud", "/a")
        self.assertEqual(granted.granted, ("/a",))
        self.assertEqual(self.visible("/a"), ["global:cloud", "ws:db/a"])
        self.assertEqual(self.visible("/b"), ["ws:db/b"])
        self.assertEqual(self.store.revoke("global:cloud", "/a").granted, ())
        self.assertEqual(self.visible("/a"), ["ws:db/a"])

    def test_a_ws_secret_is_not_granted(self):
        with self.assertRaises(BadSecret):
            self.store.grant("ws:db", "/b")

    def test_deleting_a_global_secret_takes_its_grants(self):
        self.store.grant("global:cloud", "/a")
        self.store.delete("global:cloud", "")
        self.assertEqual(self.visible("/a"), ["ws:db/a"])
        self.assertEqual(self.store.create("global:cloud", "").granted, ())


class AValueSitsEncryptedAndIsReadBackWhole(unittest.TestCase):
    def setUp(self):
        self.store, self.root = make_store(self)
        self.store.create("ws:db", "/a")

    def files(self):
        return [p for p in self.root.rglob("*") if p.is_file() and "bin" not in p.parts]

    def test_a_value_round_trips_with_binary_bytes_and_a_trailing_newline(self):
        raw = b"s3cr\x00et-\xffvalue\n"
        self.store.put("ws:db", "/a", raw)
        self.assertEqual(self.store.open("ws:db", "/a"), raw)
        self.assertTrue(self.store.get("ws:db", "/a").has_value)

    def test_no_file_holds_the_value_and_the_data_does_not_either(self):
        self.store.put("ws:db", "/a", VALUE)
        for path in self.files():
            with self.subTest(path=path.name):
                self.assertNotIn(VALUE, path.read_bytes())
        self.assertEqual([p.suffix for p in self.store.dir.iterdir()], [".age"])
        self.assertEqual(stat.S_IMODE(self.store.dir.stat().st_mode), 0o700)
        (age,) = self.store.dir.iterdir()
        self.assertEqual(stat.S_IMODE(age.stat().st_mode), 0o600)

    def test_the_identity_is_made_on_the_first_put_and_is_private(self):
        self.assertFalse(self.store.identity.exists())
        self.store.put("ws:db", "/a", VALUE)
        self.assertEqual(self.store.identity, Path(self.root / "config" / "coscc" / "vault.key"))
        self.assertEqual(stat.S_IMODE(self.store.identity.stat().st_mode), 0o600)

    def test_it_can_encrypt_only_with_both_tools_found(self):
        self.assertTrue(self.store.can_encrypt())
        self.store.age_keygen = str(self.root / "no-such-keygen")
        self.assertFalse(self.store.can_encrypt())

    def test_a_second_put_replaces_the_value(self):
        self.store.put("ws:db", "/a", VALUE)
        self.store.put("ws:db", "/a", b"other-value-entirely")
        self.assertEqual(self.store.open("ws:db", "/a"), b"other-value-entirely")

    def test_an_empty_value_and_a_secret_that_is_not_there_are_refused(self):
        with self.assertRaises(BadSecret):
            self.store.put("ws:db", "/a", b"")
        with self.assertRaises(BadSecret):
            self.store.put("ws:nope", "/a", VALUE)
        with self.assertRaises(BadSecret) as caught:
            self.store.open("ws:db", "/a")
        self.assertIn("no value", str(caught.exception))

    def test_deleting_removes_the_row_and_the_file(self):
        self.store.put("ws:db", "/a", VALUE)
        self.store.delete("ws:db", "/a")
        self.assertIsNone(self.store.get("ws:db", "/a"))
        self.assertEqual(list(self.store.dir.iterdir()), [])

    def test_values_for_holds_each_visible_secret_that_has_a_value_and_no_other(self):
        self.store.create("ws:empty", "/a")
        self.store.create("ws:db", "/b")
        self.store.put("ws:db", "/b", b"someone-elses-value")
        self.store.create("global:cloud", "")
        self.store.put("global:cloud", "", b"cloud-value-1")
        self.store.put("ws:db", "/a", VALUE)
        self.assertEqual(self.store.values_for("/a"), {"ws:db": VALUE})
        self.store.grant("global:cloud", "/a")
        self.assertEqual(
            self.store.values_for("/a"), {"ws:db": VALUE, "global:cloud": b"cloud-value-1"}
        )

    def test_a_missing_age_says_to_install_it(self):
        self.store.age = str(self.root / "no-such-age")
        with self.assertRaises(BadSecret) as caught:
            self.store.put("ws:db", "/a", VALUE)
        self.assertIn("install age", str(caught.exception))

    def test_the_paths_a_command_may_not_name_are_the_store_and_the_config(self):
        paths = self.store.protected()
        self.assertIn(str(self.store.dir), paths)
        self.assertIn(str(self.root / "config" / "coscc"), paths)


class AnAgentMayGenerateAWsSecretAndNeverNameItsValue(unittest.TestCase):
    def setUp(self):
        self.store, _ = make_store(self)

    def test_it_is_32_random_bytes_in_urlsafe_base64_without_padding(self):
        made = generate(self.store, "ws:token", "/a", "for the api")
        self.assertIsInstance(made, Secret)
        self.assertTrue(made.has_value)
        self.assertEqual(made.created_by, "agent:impl")
        value = self.store.open("ws:token", "/a")
        self.assertRegex(value.decode(), r"^[A-Za-z0-9_-]{43}$")
        generate(self.store, "ws:other", "/a")
        self.assertNotEqual(self.store.open("ws:other", "/a"), value)

    def test_it_never_makes_a_global_secret_and_never_overwrites(self):
        with self.assertRaises(BadSecret):
            generate(self.store, "global:token", "/a")
        generate(self.store, "ws:token", "/a")
        first = self.store.open("ws:token", "/a")
        with self.assertRaises(BadSecret):
            generate(self.store, "ws:token", "/a")
        self.assertEqual(self.store.open("ws:token", "/a"), first)

    def test_a_failed_encryption_leaves_no_valueless_secret_behind(self):
        self.store.age = "/no/such/age"
        with self.assertRaises(BadSecret):
            generate(self.store, "ws:token", "/a")
        self.assertIsNone(self.store.get("ws:token", "/a"))


if __name__ == "__main__":
    unittest.main()
