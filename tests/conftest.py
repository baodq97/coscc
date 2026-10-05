"""What every test runs inside: a data root of its own, never the machine's `~/.cos`.

A test that builds `Config()` or `Data()` with no root would otherwise reach the real one, and
behave differently wherever `TMPDIR` lies (inside an app step it lies under `~/.cos`)."""

from __future__ import annotations

import pytest

from coscc.store import db


@pytest.fixture(autouse=True)
def _own_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DIR", str(tmp_path / "cos"))
