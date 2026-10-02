"""A unit's two scratch directories: where they are, what refuses them, what removes them."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import units
from coscc.units import scratch

UNIT = "0145_x"


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.temp = base / "tmp"
        self.temp.mkdir()
        patch = mock.patch.object(tempfile, "tempdir", str(self.temp))
        patch.start()
        self.addCleanup(patch.stop)
        self.data = base / "data"
        self.workspace = base / "repo"
        self.workspace.mkdir()
        self.slot = units.slot(self.workspace)
        self.ram_root = self.temp / f"coscc-scratch-{os.getuid()}"

    def test_places_and_modes(self):
        ram, disk = scratch.ensure(self.workspace, UNIT, self.data)
        self.assertEqual(ram, self.ram_root / self.slot / UNIT)
        self.assertEqual(disk, self.data / "scratch" / self.slot / UNIT)
        self.assertEqual((ram, disk), scratch.places(self.workspace, UNIT, self.data))
        for where in (ram, ram.parent, self.ram_root, disk, disk.parent, disk.parent.parent):
            self.assertEqual(stat.S_IMODE(os.lstat(where).st_mode), 0o700, where)
        # A second step of the unit finds what the first left.
        (disk / "keep").write_text("a")
        scratch.ensure(self.workspace, UNIT, self.data)
        self.assertTrue((disk / "keep").exists())

    def test_a_bad_unit_name_is_refused(self):
        with self.assertRaises(units.BadUnit):
            scratch.places(self.workspace, "../x", self.data)

    def test_a_symlinked_ram_root_is_refused(self):
        elsewhere = self.temp / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        self.ram_root.symlink_to(elsewhere)
        with self.assertRaisesRegex(scratch.Unsafe, "symlink"):
            scratch.ensure(self.workspace, UNIT, self.data)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_ram_root_not_0700_is_refused(self):
        self.ram_root.mkdir()
        os.chmod(self.ram_root, 0o755)
        with self.assertRaisesRegex(scratch.Unsafe, "0755"):
            scratch.ensure(self.workspace, UNIT, self.data)
        self.assertEqual(list(self.ram_root.iterdir()), [])

    def test_a_ram_root_of_another_uid_is_refused(self):
        self.ram_root.mkdir(mode=0o700)
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            # The name follows the uid, so the other uid's root is the one made above.
            with mock.patch.object(scratch, "ram_root", return_value=self.ram_root):
                with self.assertRaisesRegex(scratch.Unsafe, "uid"):
                    scratch.ensure(self.workspace, UNIT, self.data)

    def test_remove_takes_both_and_nothing_else(self):
        ram, disk = scratch.ensure(self.workspace, UNIT, self.data)
        other, _ = scratch.ensure(self.workspace, "0146_y", self.data)
        (ram / "f").write_text("a")
        (disk / "sub").mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        (outside / "precious").write_text("a")
        (disk / "sub" / "link").symlink_to(outside)
        scratch.remove(self.workspace, UNIT, self.data)
        self.assertFalse(ram.exists())
        self.assertFalse(disk.exists())
        self.assertTrue(other.exists())
        self.assertTrue((outside / "precious").exists())
        # Gone already, or never there: nothing raises.
        scratch.remove(self.workspace, UNIT, self.data)
        scratch.remove(self.workspace, "../..", self.data)

    def test_remove_does_not_follow_a_symlinked_unit(self):
        scratch.ensure(self.workspace, "0146_y", self.data)
        outside = self.temp / "outside"
        outside.mkdir()
        (outside / "precious").write_text("a")
        (self.ram_root / self.slot / UNIT).symlink_to(outside)
        scratch.remove(self.workspace, UNIT, self.data)
        self.assertTrue((outside / "precious").exists())

    def test_sweep_removes_ghosts_and_removed_workspaces(self):
        ram, disk = scratch.ensure(self.workspace, UNIT, self.data)
        ghost = self.data / "scratch" / self.slot / "9999_ghost"
        ghost.mkdir(mode=0o700)
        (ghost / "f").write_text("a")
        gone = self._tmp.name + "/gone"
        gone_ram, gone_disk = scratch.ensure(gone, UNIT, self.data)
        stray = self.data / "scratch" / self.slot / "not-a-unit"
        stray.mkdir()
        scratch.sweep({self.slot: {UNIT}}, self.data)
        self.assertTrue(ram.exists())
        self.assertTrue(disk.exists())
        self.assertFalse(ghost.exists())
        self.assertFalse(gone_ram.parent.exists())
        self.assertFalse(gone_disk.parent.exists())
        self.assertTrue(stray.exists())

    def test_sweep_leaves_an_unsafe_root_alone(self):
        self.ram_root.mkdir()
        os.chmod(self.ram_root, 0o755)
        ghost = self.ram_root / self.slot / "9999_ghost"
        ghost.mkdir(parents=True)
        scratch.sweep({}, self.data)
        self.assertTrue(ghost.exists())


if __name__ == "__main__":
    unittest.main()
