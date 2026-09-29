# Code and tests

## The checks

`npm test` runs `npm run lint` before the tests, and CI runs `npm test`. Before a commit:

```
uv run ruff format && uv run ruff check --fix   # then npm test
uv run pytest coscc/<pkg>/<module>_test.py      # while working: the file you changed
```

- `ruff check`: the pyflakes rules only (`F`): unused imports and variables, undefined names,
  redefinitions. No style rule.
- `ruff format --check`: one format, line length 100. Never format by hand.
- `ty check`: types, on code and `scripts/`, not on tests (running them checks them).
  - Off everywhere: `unresolved-attribute`, `invalid-argument-type`, `invalid-assignment` and
    `not-subscriptable`. Reflex `Var` fields and the `Service` mixins make them report about
    2,000 false findings.
  - Also off in `coscc/screens/` and `coscc/state/` (Reflex): `unsupported-operator`,
    `deprecated`, `no-matching-overload` and `invalid-return-type`.
  - What stays on catches a name that does not import, a call with wrong arguments and an
    `await` on what cannot be awaited.
- `coscc/comments_test.py`: no unit or requirement id (`0088`, `R3`, `spec.md C7`,
  `review round 2`) in a comment, a docstring or a function or class name, tests included.
- `coscc/citations_test.py`: every `NAME` `path:N` under `.claude/` points at its name. A
  change that moves lines fixes the citations in the same commit.
- `coscc/layers_test.py`: no import goes up a layer (below).

Fix the code, not the check. A rule is switched off only in `pyproject.toml`, with its reason. A
`# noqa` or `# ty: ignore` names its rule and says why on the same line.

## Layers

`LAYERS` in `coscc/layers_test.py`, from the top:

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

- **Where a test goes.** A test sits beside its module as `<module>_test.py`. A package's
  `__init__.py` is tested by `<package>_test.py`. A file too big to read splits by aspect, as
  `<module>_<aspect>_test.py` (`steps_integrate_test.py`), so `<module>*_test.py` finds every
  test of a module.
- **Tests that span modules.**
  - Tests that check the whole repository sit in `coscc/`: `comments_test.py`,
    `citations_test.py`, `layers_test.py`, `rules_budget_test.py`, `repository_test.py`.
  - A test that spans one package's modules is named for what it checks
    (`units/seven_places_test.py`).
- **Classes.** A test class subclasses `unittest.TestCase`, and its name is a sentence with no
  `Test` prefix (`OverridesComeFirst`). pytest collects `TestCase` subclasses only by type, so
  a plain class named that way is skipped without a word.
- **Wrappers.** A thin wrapper around a command (`git/gh.py`) gets no test of its own. Its
  callers take a fake (`gh.Run`), and their tests check the decisions.
- **Names.** A test method's name says the behaviour
  (`test_a_second_press_while_one_runs_is_refused`), never the unit or requirement that asked
  for it.
- **Fixtures.** Fixture files go in a `testdata/` directory beside the test; `pyproject.toml`
  keeps them out of the wheel. A fixture class stays in its own test file.
- **Speed.** `npm test` runs everything with `pytest -n auto`, in about two minutes.
