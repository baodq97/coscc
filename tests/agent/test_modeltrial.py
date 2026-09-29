"""`coscc/agent/modeltrial.py`: the arm a unit is in, and which step asks for its model."""

from __future__ import annotations

import hashlib
import unittest

from coscc.agent import models, modeltrial


class TheArm(unittest.TestCase):
    def test_the_arm_is_the_parity_of_the_first_sha256_byte(self):
        for n in range(200):
            name = f"{n:04d}_unit-{n}"
            with self.subTest(name=name):
                even = hashlib.sha256(name.encode("utf-8")).digest()[0] % 2 == 0
                self.assertEqual(
                    modeltrial.arm(name), modeltrial.OPUS_ARM if even else modeltrial.SONNET_ARM
                )

    def test_about_half_of_a_thousand_names_are_in_each_arm(self):
        names = [f"{n:04d}_unit-{n}" for n in range(1000)]
        share = sum(1 for n in names if modeltrial.arm(n) == modeltrial.OPUS_ARM) / len(names)
        self.assertGreaterEqual(share, 0.45)
        self.assertLessEqual(share, 0.55)


class TheModel(unittest.TestCase):
    def test_only_a_routine_impl_asks_for_its_arms_model(self):
        for stage in ("impl", "review", "spec", "integrate"):
            for label in ("routine", "novel", None):
                for arm in (modeltrial.OPUS_ARM, modeltrial.SONNET_ARM):
                    with self.subTest(stage=stage, label=label, arm=arm):
                        wanted = (
                            modeltrial.MODELS[arm]
                            if (stage, label) == ("impl", "routine")
                            else None
                        )
                        self.assertEqual(modeltrial.model_for(stage, label, arm), wanted)

    def test_the_arms_name_the_models_models_json_names(self):
        # The two models the defaults already run, `[1m]` and all.
        defaults, _ = models.load_defaults()
        shipped = {row["model"] for row in defaults.values()}
        self.assertEqual(set(modeltrial.MODELS.values()) - shipped, set())
        self.assertEqual(
            modeltrial.MODELS,
            {"opus-5-5": "claude-opus-5-5[1m]", "sonnet-5-5": "claude-sonnet-5-5[1m]"},
        )


if __name__ == "__main__":
    unittest.main()
