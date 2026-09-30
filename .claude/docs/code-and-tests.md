# Code and tests

## The checks

`npm test` runs `npm run lint` before the tests, and CI runs `npm test`. Before a commit:

```
uv run ruff format && uv run ruff check --fix   # then npm test
uv run pytest tests/<pkg>/test_<module>.py      # while working: the module you changed
```

- `ruff check`: the pyflakes rules (`F`): unused imports and variables, undefined names,
  redefinitions; the blind-`except` rules (`BLE`, `S110`, `S112`, below); and `RUF100`, a
  `noqa` nothing needs. No style rule.
  - Size: a function stays within complexity 20 (`C901`) and 60 statements (`PLR0915`). One
    still over carries `# noqa: C901, PLR0915 - still to split`; split it and `RUF100` makes
    the mark go.
  - `ARG`: an argument is read. A callback that must take one it ignores names it `_x`.
    Tests are exempt (a fake takes what it stands in for).
- `ruff format --check`: one format, line length 100. Never format by hand.
- `ty check`: types, on `coscc/` and `scripts/`, not on `tests/` (running them checks them).
  - Off only in Reflex code, `coscc/screens/`, `coscc/state/` and `coscc/coscc.py`, where `Var`
    fields make them report false findings: `invalid-argument-type`, `invalid-assignment`,
    `not-subscriptable`, `unresolved-attribute`, `unsupported-operator`, `deprecated`,
    `no-matching-overload` and `invalid-return-type`.
  - Elsewhere every rule is on: a value that may be `None` is checked before it is read or
    passed on, and a finding is fixed, never silenced with `cast` or `# ty: ignore`.
- `tests/test_comments.py`: no unit or requirement id (`0088`, `R3`, `spec.md C7`,
  `review round 2`) in a comment, a docstring or a function or class name, in `coscc/` or
  `tests/`.
- `tests/test_citations.py`: every `NAME` `path:N` under `.claude/` points at its name. A
  change that moves lines fixes the citations in the same commit.
- `tests/test_layers.py`: no import goes up a layer (below).

Fix the code, not the check. A rule is switched off only in `pyproject.toml`, with its reason. A
`# noqa` or `# ty: ignore` names its rule and says why on the same line.

## Errors and logs

- Catch an error by its type. A read or write of `cos.db` raises `data.Unusable` (`Busy`,
  `Protected`, `Incompatible`), `sqlite3.Error` or `OSError`.
- `except Exception` is for what nobody expected. It calls `log.exception(...)`, so the
  traceback reaches the log, then records or shows what the caller needs.
- Only cleanup on the way out (a close, a kill, a disconnect) or a poll that fails the same way
  whenever the machine is offline swallows silently, with `# noqa: BLE001` and why.
- A module logs through `log = logging.getLogger(__name__)`; `run.py` configures it once
  (journald adds the time). `print` is for the command line's output only.
- A failure that repeats on every read is a bug to fix, not a line to log each time.

## Layers

`LAYERS` in `tests/test_layers.py`, from the top:

```
coscc.py, run.py        the page app, the command line
screens/                components
state/                  what the page shows (place, present)
api.py                  the JSON API
service/                every decision the page and the API ask for
github/, update/        pull requests, integration, release; updating the app
runner/                 one step of one unit
units/                  units, their states and guards, worktrees, the submit tool
git/, runlog/           the git and gh commands; the run log
agent/                  sessions, grants, the harness
auth.py, build.py, frontend.py, ui.py
data.py
config.py
```

- A module imports its own package and anything lower, never a package above or beside it.
  An import inside a function counts: it hides a cycle, it does not remove one.
- When a lower module needs something from above, move the thing down to where both can
  reach it, or move the module up. Never import late to get round it.
- A new package or root module gets a line in `LAYERS`, or the test fails.

## Imports

- Import a name from the module that defines it. A package's `__init__.py` does not re-export for
  others.
- Moving a name moves its importers and its `mock.patch` strings in the same commit: no alias
  and no shim is left behind.
- A test patches a name where it is looked up (`mock.patch("coscc.service.steps.CI_REFRESH")`).

## Tests

- **Where a test goes.** `tests/` mirrors `coscc/`: `coscc/<pkg>/<module>.py` is tested by
  `tests/<pkg>/test_<module>.py`, a root module `coscc/<module>.py` by `tests/test_<module>.py`,
  and a package's `__init__.py` by `test_<package>.py`. A file too big to read splits by aspect,
  as `test_<module>_<aspect>.py` (`test_steps_integrate.py`), so `test_<module>*.py` finds every
  test of a module. Moving a module moves its tests to the mirrored place in the same commit.
- **Packages.** Each directory under `tests/` has an empty `__init__.py`, so two packages can
  both hold a `test_backlog.py`, and a test imports another's helper as
  `from tests.github.test_prmachine import FakeGh`.
- **Tests that span modules.**
  - Tests that check the whole repository sit in `tests/`: `test_comments.py`,
    `test_citations.py`, `test_layers.py`, `test_rules_budget.py`, `test_repository.py`.
  - A test that spans one package's modules is named for what it checks
    (`units/test_seven_places.py`).
- **Classes.** A test class subclasses `unittest.TestCase`, and its name is a sentence with no
  `Test` prefix (`OverridesComeFirst`). pytest collects `TestCase` subclasses only by type, so
  a plain class named that way is skipped without a word.
- **Wrappers.** A thin wrapper around a command (`git/gh.py`) gets no test of its own. Its
  callers take a fake (`gh.Run`), and their tests check the decisions.
- **Names.** A test method's name says the behaviour
  (`test_a_second_press_while_one_runs_is_refused`), never the unit or requirement that asked
  for it.
- **Fixtures.** Fixture files go in a `testdata/` directory beside the test. A fixture class
  stays in its own test file.
- **Speed.** `npm test` runs everything with `pytest -n auto`, in about two minutes.
