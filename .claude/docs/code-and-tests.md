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
  - `PIE794`: a class defines a field once.
  - Refuse first: check the failing case and return, raise or continue, so the main path runs
    unindented; `RET505`–`RET508` and `PLR1702` (4 nested blocks) hold it, and
    `# noqa: PLR1702 - still to split` marks a function that waits.
- `ruff format --check`: one format, line length 100. Never format by hand.
- `ty check`: types, on `coscc/` and `scripts/`, not on `tests/` (running them checks them).
  - Off only in Reflex code, `coscc/screens/`, `coscc/state/` and `coscc/coscc.py`, where `Var`
    fields make them report false findings: `invalid-argument-type`, `invalid-assignment`,
    `not-subscriptable`, `unresolved-attribute`, `unsupported-operator`, `deprecated`,
    `no-matching-overload` and `invalid-return-type`.
  - Elsewhere every rule is on: a value that may be `None` is checked before it is read or
    passed on, and a finding is fixed, never silenced with `cast` or `# ty: ignore`.
- `tests/test_comments.py`: no unit or requirement id (a unit number, a `spec.md` requirement,
  a review round) in a comment, a docstring, a function or class name, or a string under
  `coscc/` (the SQL schema comments), nor in a markdown line under `.claude/` or
  `coscc/features/`. The failure names file, line and id: say why, not which unit. Not read:
  `old-units.md`, `.claude/scripts/testdata`, `.claude/worktrees`, strings under `tests/`
  (fixture data); a named exception covers the `CLAUDE.md` pointer to `old-units.md` and the
  finding, spike and fixture-unit formats in the skills and the UI rule.
- `tests/test_citations.py`: every `NAME` `path:N` under `.claude/` points at its name. A
  change that moves lines fixes the citations in the same commit.
- `tests/test_layers.py`: no import goes up a layer (below).
- `tests/test_boundaries.py`: no private name, foreign table SQL or new `dict[str, Any]` (below).

Fix the code, not the check. A rule is switched off only in `pyproject.toml`, with its reason. A
`# noqa` or `# ty: ignore` names its rule and says why on the same line.

## Types

- A closed set of strings is a `Literal`, and its tuple comes from it with `get_args`, next to it
  (`journal.Outcome`, `OUTCOMES`). A value read from JSON, the database or a request is checked
  against the tuple once, where it is read.

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
features/, plugin.py    features that plug in (below), and their door
service/                every decision the page and the API ask for
github/, update/        pull requests, integration, release; updating the app
runner/                 one step of one unit
units/                  units, their states and guards, worktrees, the submit tool
git/, runlog/           the git and gh commands; the run log
agent/                  sessions, grants, the harness
auth.py, build.py, bus.py, frontend.py, ui.py
data.py
config.py
```

- A module imports its own package and anything lower, never a package above or beside it.
  An import inside a function counts: it hides a cycle, it does not remove one.
- No import cycle among a package's modules either. An `if TYPE_CHECKING:` import does not
  count; an import inside a function does.
- When a lower module needs something from above, move the thing down to where both can
  reach it, or move the module up. Never import late to get round it.
- A new package or root module gets a line in `LAYERS`, or the test fails.
- `tests/test_boundaries.py`: modules talk through three channels, and each has a ratchet whose
  list of today's findings only shrinks.
  - Calls: a name with a leading underscore is not imported from another module (`PRIVATE_IMPORTS`).
    Drop the underscore, or keep the name in the one module that uses it.
  - Data: each table has one owner module (`OWNERS`) and only it runs SQL on it (`FOREIGN_SQL`).
    Add a function to the owner and call that.
  - Types: a public function does not take or return `dict[str, Any]` unless `DICT_ANY` lists it as
    `module:qualname` (`coscc.service.steps:Steps.run_step`). Type the new one with a dataclass, a
    `TypedDict` or a `Literal`; delete an entry that is gone. A move across modules replaces the
    entry by hand.
  - A table a feature creates (`Plugin.tables`) is owned by that feature's module with no edit to
    `OWNERS`, which keeps the tables of `coscc/data.py`.

- A feature is a plug-in: `coscc/features/<name>.py` ends in one `PLUGIN` (`coscc/plugin.py`) and gets
  the running app only through a `Ctx` (`journal`, `workspace_key`, `enabled`, `bus`, `data`). Its
  `tables` are `CREATE TABLE IF NOT EXISTS` statements run once at build (anything else raises); it
  reads a request with `plugin.body` and streams with `plugin.line` and `plugin.ndjson`. Add it as its file plus one line in `FEATURES`
  (`coscc/features/__init__.py`); delete that line and its route and page script are gone.
  A workspace turns it off with `POST /api/features {cwd, name, on}`, no code change. Three
  rules in `tests/test_layers.py`: a feature imports only `coscc.plugin`, `coscc.bus`,
  `coscc.service.common` and packages below `service` (never another feature); only `api.py` and
  `screens/__init__.py` import `coscc.features`, as `from coscc import features`; a feature is at
  most 3 files of 800 lines each.

## Imports

- Import a name from the module that defines it. A package's `__init__.py` does not re-export for
  others.
- Moving a name moves its importers and its `mock.patch` strings in the same commit: no alias
  and no shim is left behind.
- A test patches a name where it is looked up (`mock.patch("coscc.service.steps.CI_REFRESH")`).

## Adding a feature

New work is `coscc/features/<name>.py` ending in one `PLUGIN`, a line in `FEATURES`, and its
test; it reaches the app only through `Ctx` (`coscc/plugin.py`). Copy `notices`. A need no
extension point serves is a kernel change, planned first.

- Extension points: `routes`, `scripts`, `tables`, `agent` giving `Parts` of `Tool`, `Guard`
  (`check(Facts)` returns words to deny, or `None`) and `Block` (`render(Facts)` adds prompt
  text); slots `slot-topbar` and `slot-unit`.
- Building blocks: `plugin.body/line/ndjson`, `Ctx`, `window.coscc.api/stream/every/ago/slot`.

```python
"""Bookmarks: a note per unit."""

from coscc.hooks import Facts, Guard, Parts
from coscc.plugin import Ctx, Plugin, body
from fastapi import APIRouter, Request

TABLE = "CREATE TABLE IF NOT EXISTS bookmarks (unit TEXT PRIMARY KEY, note TEXT NOT NULL)"


def routes(ctx: Ctx):
    router = APIRouter()

    @router.post("/api/bookmarks")
    async def save(request: Request) -> dict[str, str]:
        sent = await body(request)
        with ctx.data.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bookmarks VALUES (?, ?)", (sent["unit"], sent["note"])
            )
        return {"unit": sent["unit"]}

    return router.routes


def no_ship(facts: Facts) -> str | None:
    return "no ship yet" if facts.stage == "ship" else None


PLUGIN = Plugin(
    "bookmarks", routes, tables=(TABLE,), agent=lambda _: Parts(guards=(Guard("b", no_ship),))
)
```

```python
class TheGuardOnlyDenies(unittest.TestCase):
    def test_it_denies_ship_and_abstains_elsewhere(self):
        self.assertTrue(bookmarks.no_ship(mock.Mock(spec=Facts, stage="ship")))
        self.assertIsNone(bookmarks.no_ship(mock.Mock(spec=Facts, stage="impl")))
```

Rules: at most 3 files (`tests/test_layers.py`); `## What the agent sees` in its doc when it
has agent parts; a blocking tool handler awaits `asyncio.to_thread`; a handler that runs a
command calls `policy.check_command` itself; a guard only denies or abstains.

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
