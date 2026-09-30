# Code and tests

## The checks

`npm test` runs lint, then the tests; CI runs it. Before a commit:

```
uv run ruff format && uv run ruff check --fix   # then npm test
uv run pytest tests/<pkg>/test_<module>.py      # while working: the module you changed
```

Fix the code, not the check. A rule is switched off only in `pyproject.toml`, with its reason; a
`# noqa` or `# ty: ignore` names its rule and why on the same line. No `cast`. Refuse first:
return, raise or continue on the failing case, so the main path is unindented.

Repository-wide tests name the fix in their failure: comments, citations, layers (a module
imports its own package or lower, without a cycle; a new package gets a `LAYERS` line) and
boundaries. Boundary lists (private imports, table owners, `dict[str, Any]`) only shrink: fix
the code, not the list.

## Types, errors, imports

- A closed set of strings is a `Literal` whose tuple comes from `get_args`; a value read from
  JSON, the database or a request is checked against it once, where it is read.
- Catch an error by its type (`data.Unusable` for the database). `except Exception` is for what
  nobody expected: it calls `log.exception`. Only cleanup on the way out, or a poll that fails
  the same way offline, swallows silently, with a `noqa` and why.
- Import a name from the module that defines it: no re-export, alias or shim. Moving a name moves
  its importers and `mock.patch` strings in the same commit; a test patches where the name is
  looked up.

## Adding a feature

New work is `coscc/features/<name>.py` ending in one `PLUGIN`, a line in `FEATURES`, and its
test; it reaches the app only through `Ctx` (`coscc/plugin.py`). Copy `notices`. A need no
extension point serves is a kernel change, planned first.

- Extension points: `routes`, `scripts`, `tables`, `agent` giving `Parts` of `Tool`, `Guard`
  (`check(Facts)` returns words to deny, or `None`; asked before every step and integration) and `Block` (`render(Facts)` adds prompt
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

Rules: a feature imports only `coscc.plugin`, `coscc.bus`, `coscc.service.common` and packages
below `service`, never another feature, and owns the tables it creates; at most 3 files; `## What the agent sees` in its doc when it
has agent parts; a blocking tool handler awaits `asyncio.to_thread`; a handler that runs a
command calls `policy.check_command` itself; a guard only denies or abstains.

## Tests

- `tests/` mirrors `coscc/`: `coscc/<pkg>/<module>.py` is tested by
  `tests/<pkg>/test_<module>.py`; a file too big splits as `test_<module>_<aspect>.py`. Every
  directory has an `__init__.py`.
- A test class subclasses `unittest.TestCase` and is named as a sentence with no `Test` prefix:
  pytest skips a plain class silently. A method's name says the behaviour, never the unit.
- A thin wrapper around a command gets no test; its callers take a fake and their tests check
  the decisions.
- Fixtures go in `testdata/` beside the test. A test waits on the effect it asserts, never a
  fixed time.
