"""The comparison command of its pairs, its `gh` shim, its verdicts and its printed line."""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from coscc.loop import STAGE_NAMES
from coscc.loop.compare import (
    Program,
    install_shim,
    main,
    pairs_for,
    report,
    run_pairs,
    units_of,
)
from tests.loop.conftest import REPO, UnitStore, entry, env, fake_gh, git_repo, header


def _ported() -> bool:
    try:
        import_module("coscc.loop.rules")
    except ImportError:
        return False
    return True


# `status`, `gate` and `next` are answered by `coscc.loop.rules`, ported by another step.
needs_rules = pytest.mark.skipif(not _ported(), reason="coscc.loop.rules is not ported yet")


def two_units(store: UnitStore) -> UnitStore:
    for name, status in [("0001_alpha", "accepted"), ("0002_beta", "draft")]:
        store.unit(
            name,
            {"intent.md": header(name, status)},
            known=entry({"intent.md": status}),
        )
    (store.cos / "ideas").mkdir()
    (store.cos / "ideas" / "0003_idea.md").write_text("# Idea: x\nStatus: draft.\n")
    return store


def shim_env(
    tmp_path: Path, answers: dict[str, tuple[int, str, str]]
) -> tuple[dict[str, str], Path]:
    """The env that runs the shim over a fake real gh; the fake's table file."""
    bin_dir = tmp_path / "real"
    fake_gh(bin_dir, answers)
    shim = install_shim(tmp_path / "shim", str(bin_dir / "gh"))
    return env(**shim), bin_dir / "gh.json"


def gh(environ: dict[str, str], cwd: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["gh", *argv], env=environ, cwd=cwd, capture_output=True, check=False)


# --- the shim --------------------------------------------------------------------------


def test_the_shim_records_the_first_answer_and_replays_it(tmp_path):
    environ, table = shim_env(tmp_path, {"pr list": (0, "[1]\n", "warn\n")})
    first = gh(environ, tmp_path, "pr", "list")
    assert (first.returncode, first.stdout, first.stderr) == (0, b"[1]\n", b"warn\n")

    table.write_text(json.dumps({"pr list": [3, "[2]\n", "changed\n"]}))
    again = gh(environ, tmp_path, "pr", "list")
    assert (again.returncode, again.stdout, again.stderr) == (0, b"[1]\n", b"warn\n")
    # Another argv is another key: it asks the real one.
    other = gh(environ, tmp_path, "pr", "view")
    assert other.returncode == 1
    assert b"no answer for pr view" in other.stderr


def test_the_shim_keys_on_the_directory_too(tmp_path):
    environ, table = shim_env(tmp_path, {"api x": (0, "one\n", "")})
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert gh(environ, tmp_path / "a", "api", "x").stdout == b"one\n"
    table.write_text(json.dumps({"api x": [0, "two\n", ""]}))
    assert gh(environ, tmp_path / "a", "api", "x").stdout == b"one\n"
    assert gh(environ, tmp_path / "b", "api", "x").stdout == b"two\n"


def test_the_shim_records_a_failure_and_replays_it(tmp_path):
    environ, table = shim_env(tmp_path, {"auth status": (4, "", "not logged in\n")})
    assert gh(environ, tmp_path, "auth", "status").returncode == 4
    table.write_text(json.dumps({"auth status": [0, "ok\n", ""]}))
    again = gh(environ, tmp_path, "auth", "status")
    assert (again.returncode, again.stdout, again.stderr) == (4, b"", b"not logged in\n")


def test_the_shim_without_a_real_gh_says_so(tmp_path):
    environ = env(**install_shim(tmp_path / "shim", None))
    r = gh(environ, tmp_path, "pr", "list")
    assert r.returncode == 127
    assert b"command not found" in r.stderr


def test_parallel_first_calls_ask_the_real_gh_once(tmp_path):
    count = tmp_path / "count"
    real = tmp_path / "real_gh"
    real.write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        f"open({str(count)!r}, 'a').write('x')\n"
        "time.sleep(0.3)\n"
        "print('answer')\n"
    )
    real.chmod(0o755)
    environ = env(**install_shim(tmp_path / "shim", str(real)))
    procs = [
        subprocess.Popen(
            ["gh", "pr", "list"],
            env=environ,
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(5)
    ]
    outs = [p.communicate()[0] for p in procs]
    assert outs == [b"answer\n"] * 5
    assert count.read_text() == "x"


# --- the pairs and the verdicts --------------------------------------------------------


def test_the_pairs_are_status_once_then_every_stage_and_next_per_unit(store, tmp_path):
    two_units(store)
    assert units_of(store.root) == ["0001_alpha", "0002_beta"]
    state = store.state()
    repo = tmp_path / "repo"
    pairs = pairs_for(store.root, state, repo)
    tail = ["--root", str(store.root), "--state", str(state)]
    assert pairs[0] == ["status", "--json", *tail]
    assert len(pairs) == 1 + 2 * (len(STAGE_NAMES) + 1)
    assert [p[2] for p in pairs if p[0] == "gate" and p[1] == "0002_beta"] == STAGE_NAMES
    assert ["next", "0001_alpha", "--repo", str(repo), *tail] in pairs
    assert all("--repo" not in p for p in pairs_for(store.root, state, None))


def test_a_store_without_a_cos_dir_has_no_units(tmp_path):
    assert units_of(tmp_path) == []


def fake_program(name: str, text: str, code: int = 0) -> Program:
    body = f"import sys; sys.stdout.write({text!r}); sys.exit({code})"
    return Program(name, (sys.executable, "-c", body))


def test_equal_programs_make_no_difference():
    a, b = fake_program("a", "same\n"), fake_program("b", "same\n")
    (v,) = run_pairs([["x"]], env(), (a, b))
    assert not v.differs


def test_a_difference_of_stdout_or_code_is_reported_with_a_diff():
    a, b = fake_program("a", "one\ntwo\n"), fake_program("b", "one\nthree\n", code=1)
    (v,) = run_pairs([["gate", "0001_x", "spec"]], env(), (a, b))
    assert v.differs
    text = report(v, ("a", "b"))
    assert "DIFFERENT: gate 0001_x spec" in text
    assert "exit code: a 0, b 1" in text
    assert "-two" in text and "+three" in text


def test_verdicts_come_in_the_order_given():
    a = Program("a", (sys.executable, "-c", "import sys; print(sys.argv[1])"))
    got = run_pairs([[str(i)] for i in range(20)], env(), (a, a))
    assert [v.results[0].out for v in got] == [f"{i}\n" for i in range(20)]


# --- the command -----------------------------------------------------------------------


def test_root_without_state_is_refused(store):
    with pytest.raises(SystemExit) as e:
        main(["--root", str(store.root)])
    assert e.value.code == 2


@needs_rules
def test_the_command_prints_the_count_and_exits_0_when_nothing_differs(store, tmp_path):
    two_units(store)
    repo = git_repo(tmp_path / "repo")
    bin_dir = tmp_path / "bin"
    fake_gh(bin_dir, {})
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "coscc.loop.compare",
            "--root",
            str(store.root),
            "--state",
            str(store.state()),
            "--repo",
            str(repo),
            "--gh-real",
            str(bin_dir / "gh"),
        ],
        cwd=REPO,
        env=env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.stdout == f"pairs: {1 + 2 * (len(STAGE_NAMES) + 1)}, different: 0\n", r.stderr
    assert r.returncode == 0


@needs_rules
def test_a_snapshot_neither_program_reads_is_the_same_refusal_for_both(store, tmp_path, capsys):
    two_units(store)
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    code = main(["--root", str(store.root), "--state", str(bad), "--gh-real", "/nonexistent"])
    out = capsys.readouterr().out
    assert out == f"pairs: {1 + 2 * (len(STAGE_NAMES) + 1)}, different: 0\n"
    assert code == 0
