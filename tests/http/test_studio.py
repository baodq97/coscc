from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from coscc.http import studio


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app-1.js").write_text("run()")
    monkeypatch.setattr(studio, "BUILT", tmp_path)
    return tmp_path


def client() -> TestClient:
    return TestClient(FastAPI(routes=studio.router.routes))


def test_a_built_asset_is_served_and_kept_for_good(built: Path) -> None:
    res = client().get("/assets/app-1.js")
    assert res.text == "run()"
    assert "immutable" in res.headers["cache-control"]


@pytest.mark.parametrize("path", ["/", "/work/coscc", "/unit/coscc/162", "/feature/vault"])
def test_every_page_path_gets_the_page(built: Path, path: str) -> None:
    res = client().get(path)
    assert res.status_code == 200
    assert res.text == "<div id=root></div>"
    assert res.headers["cache-control"] == "no-cache"


def test_a_path_outside_the_build_gets_the_page_not_the_file(built: Path) -> None:
    (built.parent / "secret.txt").write_text("no")
    res = client().get("/..%2fsecret.txt")
    assert "no" != res.text


@pytest.mark.parametrize("path", ["/api", "/api/nothing-here"])
def test_an_unknown_api_path_is_not_found_not_the_page(built: Path, path: str) -> None:
    res = client().get(path)
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}
