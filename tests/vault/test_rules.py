"""Whether one use is allowed: each refusal has its own code, and the code a sentence."""

from __future__ import annotations

import unittest
from typing import get_args

from coscc.vault.rules import REFUSALS, Refusal, policy, sentence
from coscc.vault.store import Secret


def secret(**over) -> Secret:
    fields = dict(
        name="ws:db",
        workspace="/a",
        description="",
        stages=("impl",),
        modes=("env", "file"),
        broker=False,
        granted=(),
        created_by="human:owner",
        created_at="t",
        has_value=True,
    )
    return Secret(**{**fields, **over})


class EveryRefusalHasItsOwnCode(unittest.TestCase):
    def test_a_use_that_breaks_no_rule_gets_no_code(self):
        self.assertEqual(policy(secret(), "/a", "impl", "env"), "")
        self.assertEqual(policy(secret(stages=("impl", "spike")), "/a", "spike", "file"), "")

    def test_a_secret_that_is_not_there_or_has_no_value_is_unknown(self):
        self.assertEqual(policy(None, "/a", "impl", "env"), "unknown-secret")
        self.assertEqual(policy(secret(has_value=False), "/a", "impl", "env"), "unknown-secret")

    def test_another_workspaces_ws_secret_is_unknown_and_not_merely_refused(self):
        self.assertEqual(policy(secret(workspace="/b"), "/a", "impl", "env"), "unknown-secret")

    def test_a_global_secret_needs_a_grant_to_the_workspace(self):
        cloud = secret(name="global:cloud", workspace="", granted=("/b",))
        self.assertEqual(policy(cloud, "/a", "impl", "env"), "not-granted")
        self.assertEqual(policy(cloud, "/b", "impl", "env"), "")

    def test_a_stage_the_secret_does_not_list_is_refused(self):
        self.assertEqual(policy(secret(), "/a", "spike", "env"), "stage-not-allowed")
        self.assertEqual(policy(secret(), "/a", "ship", "env"), "stage-not-allowed")

    def test_a_way_of_passing_the_secret_does_not_list_is_refused(self):
        self.assertEqual(policy(secret(), "/a", "impl", "placeholder"), "mode-not-allowed")
        self.assertEqual(policy(secret(), "/a", "impl", "telepathy"), "mode-not-allowed")

    def test_a_broker_secret_only_goes_by_ssh(self):
        broker = secret(broker=True, modes=("ssh",))
        self.assertEqual(policy(broker, "/a", "impl", "env"), "broker-ssh-only")
        self.assertEqual(policy(broker, "/a", "impl", "ssh"), "")

    def test_a_workspace_that_is_not_granted_hears_that_before_the_stage(self):
        cloud = secret(name="global:cloud", workspace="", granted=())
        self.assertEqual(policy(cloud, "/a", "spike", "env"), "not-granted")

    def test_a_secret_the_runs_grant_does_not_name_is_refused(self):
        self.assertEqual(policy(secret(), "/a", "impl", "env", granted=set()), "not-in-grant")
        self.assertEqual(policy(secret(), "/a", "impl", "env", granted={"ws:db"}), "")
        self.assertEqual(policy(secret(), "/a", "spike", "env", set()), "stage-not-allowed")

    def test_the_six_codes_are_the_closed_set_and_each_has_a_sentence_naming_what_it_is_about(
        self,
    ):
        self.assertEqual(sorted(REFUSALS), sorted(get_args(Refusal)))
        self.assertEqual(len(REFUSALS), 6)
        for code in REFUSALS:
            with self.subTest(code=code):
                text = sentence(code, "ws:db", "/a", "spike", "file")
                self.assertIn("ws:db", text)
                self.assertTrue(text.endswith("."))
        self.assertIn("/a", sentence("not-granted", "global:x", "/a", "impl", "env"))
        self.assertIn("spike", sentence("stage-not-allowed", "ws:db", "/a", "spike", "env"))


if __name__ == "__main__":
    unittest.main()
