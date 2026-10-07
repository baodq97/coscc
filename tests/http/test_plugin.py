"""Building the features into the app: what `check_declarations` refuses."""

from __future__ import annotations

import unittest
from unittest import mock

import copy

from coscc.agent import pack
from coscc.http import plugin
from coscc.units import contracts
from coscc.units.contracts import ContractError


class ABrokenShippedDeclarationStopsTheBuild(unittest.TestCase):
    def setUp(self):
        contracts._READ[:] = []
        self.addCleanup(contracts._READ.clear)

    def test_a_spec_without_unmeasured_is_refused_with_its_reader(self):
        broken = copy.deepcopy(pack.rows())
        del broken["spec"]["output"]["fields"]["unmeasured"]
        with (
            mock.patch.object(pack, "rows", return_value=broken),
            self.assertRaises(ContractError) as e,
        ):
            plugin.check_declarations()
        self.assertEqual(
            str(e.exception), "contract-field-missing: spec.unmeasured (read by spike-holds)"
        )

    def test_the_shipped_declarations_build(self):
        plugin.check_declarations()


if __name__ == "__main__":
    unittest.main()
