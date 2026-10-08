"""The screenshot fixture's `0013` is held by `0012`, so the app under the camera starts no step."""

import unittest

from coscc.leif import decide
from scripts import capture_screens as cap


class TheOverlappingUnit(unittest.TestCase):
    def test_it_is_on_the_shortlist_and_names_a_file_the_running_unit_names(self):
        names = list(cap.AUTOPILOT_FIXTURE)
        self.assertEqual([n for n in names if n.startswith("overlapping")], ["overlapping-impl"])
        self.assertEqual(f"{8 + names.index('overlapping-impl'):04d}", cap.OVERLAP_IMPL[:4])
        self.assertIn(cap.OVERLAP_IMPL, self._shortlist())
        mine = cap.AUTOPILOT_FIXTURE["overlapping-impl"]["plan"]
        theirs = cap.AUTOPILOT_FIXTURE["conflict-while-impl"]["plan"]
        self.assertTrue(set(mine) & set(theirs))

    def test_the_pass_holds_it_overlap_by_the_running_step_and_picks_it_not(self):
        files = set(cap.AUTOPILOT_FIXTURE["overlapping-impl"]["plan"])
        got = decide.pick(
            [{"unit": cap.OVERLAP_IMPL, "stage": "impl", "files": files, "rank": 5, "need": 1.0}],
            [{"unit": cap.CONFLICT_IMPL, "stage": "impl", "files": files}],
            3,
            100.0,
        )
        self.assertEqual(got["held"], {cap.OVERLAP_IMPL: ("overlap", cap.CONFLICT_IMPL)})
        self.assertEqual(got["chosen"], [])

    @staticmethod
    def _shortlist() -> list[str]:
        import inspect

        source = inspect.getsource(cap.make_autopilot_fixture)
        return [n for n in (cap.OVERLAP_IMPL,) if "OVERLAP_IMPL" in source]


if __name__ == "__main__":
    unittest.main()
