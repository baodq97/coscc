"""Building the features into the app: what `add_sessions` refuses."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc.agent.agents import DEFAULT_PATH
from coscc.http import plugin
from coscc.units import contracts
from coscc.units.contracts import ContractError


class ABrokenShippedDeclarationStopsTheBuild(unittest.TestCase):
    def setUp(self):
        contracts._shipped.cache_clear()
        self.addCleanup(contracts._shipped.cache_clear)

    def test_a_spec_without_unmeasured_is_refused_with_its_reader(self):
        raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        del raw["agents"]["spec"]["output"]["fields"]["unmeasured"]
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / "agents.json"
            broken.write_text(json.dumps(raw), encoding="utf-8")
            with (
                mock.patch.object(contracts, "DEFAULT_PATH", broken),
                self.assertRaises(ContractError) as e,
            ):
                plugin.add_sessions(mock.Mock(), [])
        self.assertEqual(
            str(e.exception), "contract-field-missing: spec.unmeasured (read by spike-holds)"
        )

    def test_the_shipped_declarations_build(self):
        plugin.add_sessions(mock.Mock(), [])


if __name__ == "__main__":
    unittest.main()
