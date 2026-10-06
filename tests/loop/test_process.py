"""The loop walks the process a unit records: `short` (intent → impl → pr → review → ship) is
data in the pack, and a unit on it reaches ship with no spec and no plan asked for."""

from __future__ import annotations

import json

from tests.loop.conftest import UnitStore, entry, header, pr_row, python, round_row

SHORT = "coscc-sdlc/short"
FILES = {
    "intent.md": header("x", "accepted"),
    "impl.md": header("x", "accepted", "Impl"),
    "pr.md": header("x", "accepted", "PR"),
    "review.md": header("x", "accepted", "Review"),
    "ship.md": header("x", "accepted", "Ship"),
}


def _status(store: UnitStore) -> dict:
    ran = python(store.argv("status", "--json"))
    assert ran.code == 0, ran.err
    [unit] = json.loads(ran.out)["units"]
    return unit


def _walked(store: UnitStore, reached: dict[str, str], **extra) -> dict:
    """The unit `0001_x` on `short` with the files of `reached` written and their statuses."""
    store.unit(
        "0001_x", {f: FILES[f] for f in reached}, {**entry(reached, **extra), "process": SHORT}
    )
    return _status(store)


def test_a_unit_on_short_walks_intent_impl_pr_review_ship_and_is_finished(store):
    steps = [
        ({"intent.md": "draft"}, "", "finish and accept intent.md"),
        ({"intent.md": "accepted"}, "impl", "write-impl — implementation starts"),
        ({"intent.md": "accepted", "impl.md": "accepted"}, "pr", "pr"),
        (
            {"intent.md": "accepted", "impl.md": "accepted", "pr.md": "accepted"},
            "review",
            "write-review",
        ),
    ]
    for reached, stage, action in steps:
        unit = _walked(store, reached, pr_md=pr_row(7))
        assert unit["process"] == SHORT
        assert (unit["next"]["stage"], unit["next"]["action"]) == (stage, action), reached
        assert not unit["problems"], unit["problems"]
    passed = {
        "intent.md": "accepted",
        "impl.md": "accepted",
        "pr.md": "accepted",
        "review.md": "accepted",
    }
    unit = _walked(
        store, passed, pr_md=pr_row(7), review_md={"rounds": [round_row(1, "pass", "a" * 40)]}
    )
    assert (unit["next"]["stage"], unit["next"]["action"]) == ("ship", "ship")
    unit = _walked(store, {**passed, "ship.md": "accepted"}, shipped=True, merged=True)
    assert unit["next"]["why"] == "finished"


def test_no_gate_on_short_asks_for_a_spec_or_a_plan(store):
    store.unit(
        "0001_x",
        {"intent.md": FILES["intent.md"]},
        {**entry({"intent.md": "accepted"}), "process": SHORT},
    )
    impl = python(store.argv("gate", "0001_x", "impl", "--json"))
    assert (impl.code, json.loads(impl.out)["reasons"]) == (0, [])
    ship = json.loads(python(store.argv("gate", "0001_x", "ship", "--json")).out)
    assert ship["lines"][1:] == [
        "  - impl.md does not exist",
        "  - pr.md does not exist",
        "  - review.md does not exist",
    ]
    spec = json.loads(python(store.argv("gate", "0001_x", "spec", "--json")).out)
    assert (
        spec["lines"][1] == '  - unknown stage "spec" — use one of intent, impl, pr, review, ship'
    )
    nxt = json.loads(python(store.argv("next", "0001_x")).out)
    assert (nxt["stage"], nxt["process"]) == ("impl", SHORT)


def test_a_unit_that_records_no_process_walks_full(store):
    store.unit("0001_x", {"intent.md": FILES["intent.md"]}, entry({"intent.md": "accepted"}))
    unit = _status(store)
    assert (unit["process"], unit["next"]["stage"]) == ("coscc-sdlc/full", "spec")
