"""`rerun` answers as its goldens hold in `python -m coscc.loop`: the offers, each refusal,
and the records of what a rerun makes stale."""

from __future__ import annotations

import json

from coscc.loop import proc_of
from tests.loop.conftest import UnitStore, entry, expect, header, python, rerun_row

RERUNNABLE = proc_of(None).rerun("fresh")

UNIT = "0001_x"
KINDS = {
    "intent.md": "Intent",
    "spec.md": "Spec",
    "spike.md": "Spike",
    "plan.md": "Plan",
    "impl.md": "Impl",
    "pr.md": "PR",
    "review.md": "Review",
    "ship.md": "Ship",
}


def make(store: UnitStore, statuses: dict[str, str], *, unmeasured: bool = False, **fields) -> None:
    """A unit with one file per `statuses` item, the app's snapshot saying the same; with
    `unmeasured`, it says spec.md has an [unmeasured] item, so spike.md is required."""
    files = {f: header("x", s, KINDS[f]) for f, s in statuses.items()}
    for f in statuses:
        key = f.replace(".md", "_md")
        fields[key] = {**fields.get(key, {}), "record": record(f)}
    if unmeasured:
        fields["spec_md"] = {**fields["spec_md"], "result": {"unmeasured": ["U1"]}}
    store.unit(UNIT, files, entry(statuses, **fields))


def record(file: str) -> int:
    """The record id an artifact holds: its place among the stages' files."""
    return list(KINDS).index(file) + 1


def make_stale(store: UnitStore, file: str, stage: str) -> None:
    """A rerun row that makes `file` stale: it names `file` with the record it holds now."""
    store.units[f"ws/{UNIT}"]["reruns"].append(
        rerun_row(stage, date="2026-09-01", **{file.replace(".md", "_md"): record(file)})
    )


def rerun(store: UnitStore, *words: str):
    return expect(store.argv("rerun", *words))


def offered(store: UnitStore) -> list[str]:
    r = rerun(store, UNIT)
    assert r.code == 0
    return [o["stage"] for o in json.loads(r.out)["offers"]]


# --- misuse ------------------------------------------------------------------------------


# --- the offers --------------------------------------------------------------------------


def test_several_accepted_stages_are_offered_with_what_runs_again(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted", "plan.md": "accepted"})
    r = rerun(store, UNIT)
    assert r.code == 0
    said = json.loads(r.out)
    assert [o["stage"] for o in said["offers"]] == ["intent", "spec", "plan"]
    assert said["why"] == ""


def test_a_stale_stage_is_not_offered(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted"})
    make_stale(store, "spec.md", "spec")
    assert offered(store) == ["intent"]


def test_a_rejected_unit_offers_nothing_and_says_why(store):
    make(store, {"intent.md": "accepted", "spec.md": "rejected"})
    assert "spec.md is rejected" in json.loads(rerun(store, UNIT).out)["why"]


def test_a_paused_unit_offers_nothing_and_says_why(store):
    hold = {"state": "paused", "reason": "waiting", "by": "person", "date": "2026-10-01"}
    make(store, {"intent.md": "accepted", "spec.md": "accepted"}, holds=[hold])
    assert json.loads(rerun(store, UNIT).out)["why"] == "the unit is paused"


def test_spike_is_offered_when_spec_is_unmeasured(store):
    make(
        store,
        {"intent.md": "accepted", "spec.md": "accepted", "spike.md": "accepted"},
        unmeasured=True,
    )
    said = json.loads(rerun(store, UNIT).out)
    assert [o["stage"] for o in said["offers"]] == ["intent", "spec", "spike"]
    assert said["offers"][0]["later"][:2] == ["spec", "spike"]


def test_spike_is_not_offered_when_spec_measures_everything(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted", "spike.md": "accepted"})
    said = json.loads(rerun(store, UNIT).out)
    assert [o["stage"] for o in said["offers"]] == ["intent", "spec"]
    assert "spike" not in said["offers"][0]["later"]


# --- the refusals ------------------------------------------------------------------------


def test_a_stage_the_board_cannot_rerun_is_refused(store):
    make(store, {"intent.md": "accepted", "impl.md": "accepted"})
    r = rerun(store, UNIT, "impl")
    assert r.code == 1
    assert "impl cannot be run again from the board" in r.err


def test_a_stage_that_has_not_run_is_refused(store):
    make(store, {"intent.md": "accepted"})
    r = rerun(store, UNIT, "spec")
    assert r.code == 1
    assert "does not exist — spec has not run yet" in r.err


def test_a_spike_nobody_requires_is_refused(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted"})
    r = rerun(store, UNIT, "spike")
    assert r.code == 1
    assert "spike is not required" in r.err


def test_a_draft_is_refused(store):
    make(store, {"intent.md": "accepted", "spec.md": "draft"})
    r = rerun(store, UNIT, "spec")
    assert r.code == 1
    assert 'spec.md is "draft", not accepted' in r.err


def test_a_stage_already_stale_is_refused(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted"})
    make_stale(store, "spec.md", "spec")
    r = rerun(store, UNIT, "spec")
    assert r.code == 1
    assert "spec.md is already stale — run spec from the next step" in r.err


def test_a_closed_gate_is_refused_with_what_it_needs(store):
    make(store, {"intent.md": "accepted", "spec.md": "draft", "plan.md": "accepted"})
    r = rerun(store, UNIT, "plan")
    assert r.code == 1
    assert r.err.startswith(f"plan cannot be run again for {UNIT}: ")


def test_a_shipped_unit_offers_nothing_and_refuses_every_stage(store):
    accepted = {f: "accepted" for f in KINDS}
    make(store, accepted, unmeasured=True)
    assert json.loads(python(store.argv("rerun", UNIT)).out)["offers"] != []
    store.units[f"ws/{UNIT}"]["shipped"] = True
    r = python(store.argv("rerun", UNIT))
    assert r.code == 0
    assert json.loads(r.out) == {
        "unit": UNIT,
        "offers": [],
        "why": "the unit is finished: it shipped",
    }
    for stage in RERUNNABLE:
        r = python(store.argv("rerun", UNIT, stage))
        assert r.code == 1, stage
        assert (
            r.err.strip()
            == f"{stage} cannot be run again for {UNIT}: the unit is finished: it shipped"
        )


def test_a_paused_unit_refuses_a_stage(store):
    hold = {"state": "paused", "reason": "waiting", "by": "person", "date": "2026-10-01"}
    make(store, {"intent.md": "accepted", "spec.md": "accepted"}, holds=[hold])
    r = rerun(store, UNIT, "spec")
    assert r.code == 1
    assert "the unit is paused" in r.err


# --- a granted rerun ---------------------------------------------------------------------


def test_a_granted_rerun_prints_the_record_of_each_file_it_makes_stale(store):
    make(store, {"intent.md": "accepted", "spec.md": "accepted", "plan.md": "accepted"})
    r = rerun(store, UNIT, "spec")
    assert r.code == 0
    said = json.loads(r.out)
    assert said["later"] == ["plan", "impl", "pr", "review", "ship"]
    assert said["stale"] == {"spec.md": record("spec.md"), "plan.md": record("plan.md")}


def test_a_granted_rerun_names_only_the_files_that_exist(store):
    make(store, {"intent.md": "accepted", "pr.md": "accepted"})
    said = json.loads(rerun(store, UNIT, "intent").out)
    assert said["later"] == ["spec", "plan", "impl", "pr", "review", "ship"]
    assert said["stale"] == {"intent.md": record("intent.md"), "pr.md": record("pr.md")}


def test_a_bad_unit_name_is_refused(store):
    r = rerun(store, "nope")
    assert r.code == 2
    assert "Invalid unit name" in r.err


def test_a_unit_that_does_not_exist_is_refused(store):
    r = rerun(store, "0002_y")
    assert r.code == 2
    assert "No such work unit" in r.err


def test_an_unknown_stage_is_refused(store):
    make(store, {"intent.md": "accepted"})
    r = rerun(store, UNIT, "nope")
    assert r.code == 2
    assert "unknown stage" in r.err
